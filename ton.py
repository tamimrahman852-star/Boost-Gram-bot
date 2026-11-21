"""TON (crypto) support: automatic deposits and the helpers for withdrawals.

Deposits: every user has a personal memo  BG<user id>. They send TON to TON_WALLET with that memo as the comment.
A worker reads the wallet's incoming transfers from toncenter (public blockchain data), and credits each transfer once
(tx hash is unique) at the live TON/USD price:  GRAM = TON x price x GRAM_PER_USD.
Nothing here holds a private key: withdrawals are paid by an admin (like the other methods).
"""
import asyncio
import base64
import re
from decimal import Decimal
from typing import Optional

from aiogram import Bot
from aiogram.enums import ParseMode
from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from config import (
    GRAM_PER_USD, TON_DEPOSIT_ENABLED, TON_DEPOSIT_MIN_USD, TON_USD_FALLBACK, TON_WALLET, TONCENTER_API_KEY, logger,
)
from database import AsyncSessionLocal, TonDeposit, Transaction, TransactionType, User, redis_client

MEMO_RE = re.compile(r"^BG(\d{3,15})$")
ADDR_RE = re.compile(r"^(?:[EUk0][QAf-][A-Za-z0-9_-]{46}|-?\d:[0-9a-fA-F]{64})$")      # friendly (48 chars) or raw address
NANO = Decimal(1_000_000_000)
RATE_KEY, LAST_KEY = "ton:usd", "ton:usd:last"


def memo_for(uid: int) -> str:
    return f"BG{uid}"


def valid_address(addr: str) -> bool:
    return bool(ADDR_RE.match((addr or "").strip()))


async def ton_usd() -> Optional[Decimal]:
    """Live TON price in USD (cached 60 s). If every source is down, the last known price for up to 6 h, then TON_USD_FALLBACK."""
    cached = await redis_client.get(RATE_KEY)
    if cached:
        return Decimal(cached)
    import aiohttp
    sources = [
        ("https://api.coingecko.com/api/v3/simple/price?ids=the-open-network&vs_currencies=usd", lambda j: j["the-open-network"]["usd"]),
        ("https://api.binance.com/api/v3/ticker/price?symbol=TONUSDT", lambda j: j["price"]),
    ]
    for url, pick in sources:
        try:
            async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=8)) as s:
                async with s.get(url) as r:
                    if r.status != 200:
                        continue
                    rate = Decimal(str(pick(await r.json(content_type=None))))
            if rate > 0:
                await redis_client.set(RATE_KEY, str(rate), ex=60)
                await redis_client.set(LAST_KEY, str(rate), ex=6 * 3600)
                return rate
        except Exception:
            continue
    last = await redis_client.get(LAST_KEY)
    if last:
        return Decimal(last)
    try:
        return Decimal(TON_USD_FALLBACK) if TON_USD_FALLBACK else None
    except Exception:
        return None


def parse_incoming(js: dict) -> list:
    """toncenter v2 getTransactions -> [{hash, value (nanoTON), comment, source}] for incoming internal transfers."""
    out = []
    for tx in (js or {}).get("result") or []:
        m = tx.get("in_msg") or {}
        if not m.get("source"):
            continue                                                   # external message / not a transfer to us
        try:
            value = int(m.get("value") or 0)
        except (TypeError, ValueError):
            continue
        comment = m.get("message") or ""
        if not comment:
            txt = ((m.get("msg_data") or {}).get("text")) or ""
            try:
                comment = base64.b64decode(txt).decode("utf-8", "ignore") if txt else ""
            except Exception:
                comment = ""
        h = (tx.get("transaction_id") or {}).get("hash") or ""
        if h and value > 0:
            out.append({"hash": h, "value": value, "comment": comment.strip(), "source": m["source"]})
    return out


async def fetch_incoming(limit: int = 100) -> Optional[list]:
    import aiohttp
    url = f"https://toncenter.com/api/v2/getTransactions?address={TON_WALLET}&limit={limit}&archival=false"
    headers = {"X-API-Key": TONCENTER_API_KEY} if TONCENTER_API_KEY else {}
    try:
        async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=15)) as s:
            async with s.get(url, headers=headers) as r:
                if r.status != 200:
                    logger.warning(f"toncenter returned {r.status}")
                    return None
                js = await r.json(content_type=None)
        return parse_incoming(js) if js.get("ok") else None
    except Exception as e:
        logger.warning(f"toncenter fetch failed: {e}")
        return None


async def credit_transfer(bot: Optional[Bot], t: dict, rate: Decimal) -> Optional[Decimal]:
    """Credit one transfer. Returns the GRAM credited (0 if it was below the minimum), None if skipped."""
    m = MEMO_RE.match(t["comment"])
    if not m:
        return None
    uid = int(m.group(1))
    ton = (Decimal(t["value"]) / NANO)
    usd = (ton * rate).quantize(Decimal("0.0001"))
    below = usd < TON_DEPOSIT_MIN_USD
    coins = Decimal("0") if below else (usd * GRAM_PER_USD).to_integral_value(rounding="ROUND_DOWN")
    async with AsyncSessionLocal() as s:
        user = (await s.execute(select(User).where(User.id == uid).with_for_update())).scalar_one_or_none()
        if not user:
            return None
        s.add(TonDeposit(user_id=uid, tx_hash=t["hash"][:80], ton_amount=ton, usd=usd, coins=coins,
                         status="below_min" if below else "credited"))
        try:
            await s.flush()                                            # unique tx_hash: a transfer is credited once, ever
        except IntegrityError:
            await s.rollback()
            return None
        if coins > 0:
            user.balance += coins
            s.add(Transaction(user_id=uid, amount=coins, type=TransactionType.STAR_TOPUP, description=f"TON deposit {ton:.4f} TON"[:255],
                              reference_id=t["hash"][:36], reference_type="ton", balance_after=user.balance))
        await s.commit()
    if bot and coins > 0:
        try:
            await bot.send_message(uid, f"✅ TON deposit received: {ton:.4f} TON ≈ ${usd:.2f}\n+<code>{coins:,.0f}</code> GRAM", parse_mode=ParseMode.HTML)
        except Exception:
            pass
    return coins


async def poll_once(bot: Optional[Bot] = None) -> int:
    """Read the wallet's recent incoming transfers and credit the new ones. Returns how many were credited."""
    if not TON_WALLET or not TON_DEPOSIT_ENABLED:
        return 0
    transfers = await fetch_incoming()
    if not transfers:
        return 0
    todo = [t for t in transfers if MEMO_RE.match(t["comment"])]
    if not todo:
        return 0
    async with AsyncSessionLocal() as s:
        seen = {r[0] for r in (await s.execute(select(TonDeposit.tx_hash).where(TonDeposit.tx_hash.in_([x["hash"][:80] for x in todo])))).all()}
    todo = [x for x in todo if x["hash"][:80] not in seen]
    if not todo:
        return 0
    rate = await ton_usd()
    if not rate:
        return 0                                                       # no price: leave them for the next round
    done = 0
    for t in reversed(todo):                                           # oldest first
        try:
            r = await credit_transfer(bot, t, rate)
            done += 1 if r else 0
        except Exception as e:
            logger.warning(f"ton credit failed: {e}")
    return done


async def ton_worker(bot: Bot):
    if not TON_WALLET or not TON_DEPOSIT_ENABLED:
        return
    while True:
        try:
            await poll_once(bot)
        except asyncio.CancelledError:
            break
        except Exception as e:
            logger.exception(f"ton worker error: {e}")
        await asyncio.sleep(30)
