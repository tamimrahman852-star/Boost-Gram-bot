# Mini App ⇄ backend contract

All requests: `Authorization: tma <Telegram initData>` (raw `Telegram.WebApp.initData`). JSON in/out unless noted.
Errors: HTTP 4xx with `{"code": "daily_limit", "message": "…"}`. Codes the UI translates:
`daily_limit premium_only not_member bad_link not_bot no_post insufficient_balance too_many bad_trx level_locked range bad_code invalid not_found blocked cooldown network`.
Money values are plain numbers (GRAM). Timestamps are ISO-8601 UTC.

## Auth (must do first)
Validate `initData` server-side (HMAC-SHA256 with key `HMAC("WebAppData", BOT_TOKEN)`), reject if `auth_date` older than ~1 day,
then use `user.id` from it. **Never trust a user id sent by the client.**
```python
import hmac, hashlib, json, time
from urllib.parse import parse_qsl
def verify_init_data(init_data: str, bot_token: str, max_age=86400):
    pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    got = pairs.pop("hash", "")
    check = "\n".join(f"{k}={v}" for k, v in sorted(pairs.items()))
    secret = hmac.new(b"WebAppData", bot_token.encode(), hashlib.sha256).digest()
    if not hmac.compare_digest(hmac.new(secret, check.encode(), hashlib.sha256).hexdigest(), got): raise ValueError("bad hash")
    if time.time() - int(pairs.get("auth_date", 0)) > max_age: raise ValueError("expired")
    return json.loads(pairs["user"])
```

## Endpoints
| Method & path | Body / query | Returns |
|---|---|---|
| `GET /api/bootstrap?start=` | – | `{user, config, daily:{used,max}, badges:{reviews}}` (see below) |
| `GET /api/tasks?type=all\|channel_sub\|…` | – | `{items:[{id,type,title,username,link,reward,slots_total,slots_left,premium,retention_days,state:"new"\|"pending"}], daily, next}` |
| `POST /api/tasks/{id}/start` | – | `{kind:"join"\|"view"\|"proof", url, dwell, token}` — for `view` the server forwards the post into the user's bot chat |
| `POST /api/tasks/{id}/verify` | `{token}` | `{ok, reward, xp, level_up, new_level, balance, retention, user, daily}` or error (`not_member`, `daily_limit`, `cooldown`…) |
| `POST /api/tasks/{id}/proof` | multipart `file` | `{status:"pending", review_hours}` |
| `GET /api/campaigns` | – | `{items:[{id,type,title,username,reward,slots_total,completed,status,premium,auto_renew,spent,created_at,pending_proofs}]}` |
| `POST /api/campaigns/resolve` | `{type, link}` | `{ok,title,username,members,bot_admin,kind}` or `bad_link/not_bot/no_post` |
| `POST /api/campaigns` | `{type,link,title,username,premium,reward,slots,auto_renew}` | `{campaign, balance, charged}` — charge = reward×slots + 15 % fee (`services.campaign_cost`) |
| `POST /api/campaigns/{id}/pause\|resume\|cancel` | – | `{status, refunded?, balance?}` |
| `GET /api/proofs/review` | – | `{items:[{id,campaign_id,campaign_title,type,reward,worker,image,created_at,expires_at}]}` (anonymise worker) |
| `POST /api/proofs/{id}/approve\|reject` | – | `{ok}` (use `services.approve_submission / reject_submission`) |
| `GET /api/wallet/history?cursor=&kind=all\|in\|out` | – | `{items:[{id,type,amount,title,created_at}], next}` (`type` = `TransactionType` value) |
| `GET /api/wallet/requests` | – | `{items:[{id,kind:"deposit"\|"withdrawal",amount,status,method,created_at}]}` |
| `POST /api/deposits` | multipart `method, amount_bdt, trx_id, screenshot` | `{status:"pending", gram}` |
| `POST /api/withdrawals` | `{amount, method, details}` | `{status:"pending", balance}` |
| `POST /api/vouchers/redeem` | `{code}` | `{amount, balance}` |
| `POST /api/checks` / `POST /api/checks/redeem` | `{amount,uses,password}` / `{code,password}` | `{code,link,balance}` / `{amount,balance}` |
| `GET /api/referrals` | – | `{code,link,count,earnings,tier,tiers:[{level,percent,count,earned}],recent:[{name,joined,earned}]}` |
| `POST /api/settings` | `{language?, notifications?}` | `{ok}` |

### `bootstrap` shape
```jsonc
{ "user": { "id":1, "first_name":"", "username":"", "is_premium":false, "photo_url":null, "balance":0, "xp":0, "level":1,
            "current_level_xp":0, "next_level_xp":1500, "total_earned":0, "total_withdrawn":0, "completed_tasks":0,
            "referral_earnings":0, "referral_code":"", "referral_tier":1, "language":"en", "notifications":true,
            "locked":0 /* rewards still in 7-day retention */, "withdrawable":0 },
  "config": { "bot_username":"BoostGramPromotionBot",
              "rules": { "xp_per_level":1500, "withdraw_min_level":30, "withdraw_min":250000, "withdraw_max":10000000,
                         "max_daily_tasks":100, "view_dwell":6, "proof_review_hours":48, "retention_days":7,
                         "slots_min":1, "slots_max":10000, "max_active_campaigns":10, "commission":0.15, "premium_multiplier":2 },
              "pricing": { "channel_sub":{"min":850,"suggested":1000,"max":5000}, "…":{} },   // TaskPricing.PRICING
              "langs": { "en":"English", "bn":"বাংলা" },                                    // L10N.LANGS
              "pay": { "bkash":"01…", "nagad":"", "rocket":"" },                            // only configured methods
              "deposit": { "rate":3000, "min":50, "max":20000 },
              "withdraw": { "methods":["usdt_trc20","usdt_erc20","bank","mobile"] },
              "referral": { "base":5000, "tiers":{"1":0.08,"2":0.05,"3":0.02} } },
  "daily": {"used":0,"max":100}, "badges": {"reviews":0} }
```
Every value above already exists in `config.py` / `database.py` / `services.py`; the API layer only has to expose it and reuse
the existing business functions (so bot and app can never disagree).

## Backend checklist (minimal changes to existing code)
1. **New** `api.py` — a FastAPI `APIRouter(prefix="/api")` with the endpoints above, reusing `database.py` models and `services.py`.
2. `web.py` — 2 lines: `app.include_router(api_router)` and `app.mount("/app", StaticFiles(directory="webapp", html=True))`.
3. `config.py` — `WEBAPP_URL` env var (e.g. `https://boost-gram-bot.onrender.com/app/`).
4. `keyboards.py` / `handlers/start.py` — an "Open App" `WebAppInfo(url=WEBAPP_URL)` button.
5. `requirements.txt` — add `python-multipart` (file uploads) and `aiofiles` if missing.
6. BotFather → `/mybots` → Bot Settings → Menu Button → set `WEBAPP_URL`; optionally `/newapp` for a `t.me/bot/app` link.
Nothing else (workers, alembic, handlers, locales) needs to change; no new tables are required.
