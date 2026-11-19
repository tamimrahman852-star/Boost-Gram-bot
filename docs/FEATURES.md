# Boost Gram – feature & operations guide

## Environment variables (Render)
| Name | Purpose |
|---|---|
| `ENCRYPTION_KEY` | long random secret (check passwords) – required |
| `ADMIN_IDS` | comma separated Telegram IDs of admins |
| `PAY_BKASH`, `PAY_NAGAD`, `PAY_ROCKET` | your receiving numbers. A method is shown to users only if its number is set |
| `DEPOSIT_RATE_PER_BDT` | GRAM credited per 1 BDT (default 3000) |
| `DEPOSIT_MIN_BDT` / `DEPOSIT_MAX_BDT` | deposit limits (default 50 / 20000) |
| `WELCOME_MEDIA_URL` | optional GIF/MP4/PNG for the /start message |

## Admin panel (`/admin`, private chat only)
Dashboard stats · Add/Deduct coins · Fraud reports · Withdrawals (approve/reject+refund) ·
Deposits (approve/reject) · Vouchers · Broadcast · Ban/Unban.

## Manual deposits
Cabinet → Replenish → bKash/Nagad/Rocket → amount → TrxID → screenshot → admin approves.
A TrxID can be used once; max 2 open requests per user; credit happens only on admin approval.

## Vouchers
`/voucher AMOUNT MAX_USES [DAYS] [CODE]` – e.g. `/voucher 50000 100 7 EID2026`
`/voucher_off CODE` – disable. Users: Cabinet → 🎟 Promo code. One use per user.

## Broadcast
Admin panel → 📣 Broadcast (or `/broadcast`) → send any message → confirm. Throttled, with a delivery report.

## Database
The new tables (`deposit_requests`, `vouchers`, `voucher_redemptions`) are created automatically at startup.

## Task types
| Type | How it is verified |
|---|---|
| Channel / Group | Telegram API (`getChatMember`) now + again after 7 days (retention) |
| ⚡ Boost | Telegram API (`getUserChatBoosts`, bot must be admin) now + after 7 days. Premium users only |
| 👁 Post views, 🤖 Bot starts, ❤️ Reactions | Telegram cannot see these. Worker sends a screenshot → advertiser approves/rejects (48h, then auto-approve) → rejected workers may dispute once → admin decides ("⚖️ Proof disputes") |

Proof-task safety: the reward is reserved from the advertiser's escrow when the proof is sent; a screenshot can only be used once;
max 10 open proofs per user; only the campaign owner can review its proofs; fake proofs lose the dispute and add risk score.

## Auto-task settings (auto-renew)
Promote → ⚙️ Auto-task settings (or a campaign's own screen): when the task finishes it is published again, up to 3 times,
charged to the owner's balance. It stops by itself (and tells the owner) if the balance is too low.

## Post views (no proof, no approval)
Promote → 👁 Post → forward a channel post to the bot (or send its public link). The bot forwards it back as a preview.
Earn → 👁 Views → tap "View post +N": the bot forwards the post into the worker's chat (this also counts as a real view on the
original post) and pays automatically after a few seconds. Safety: one view per post per user, minimum dwell time, daily task limit,
tap cooldown, own posts excluded, campaigns auto-pause if the post can't be forwarded any more.

## Languages (21 — all hand-written, no API)
Bangla and English are in the code. The other 19 languages
(hi ar ru es tr id fa fr de pt uk zh ja ko vi th uz kk my) are **manual translation files**: `locales/<lang>.tsv`.
Each line is `<line number><TAB><translation>`; the line numbers refer to `locales/lines.json` (the English texts).
* No internet / API is used. A user picks a language and sees the whole bot in it immediately (menu, buttons, messages).
* **Improve a translation:** open `locales/hi.tsv` in any text editor, change the text after the TAB, commit. Keep
  placeholders such as `{amount}`, `{v0:,.0f}` and tags such as `<b>` exactly as they are.
* **Check your edits:** `python tools/check_locales.py` (all languages) or `python tools/check_locales.py hi`.
* **Admin `/i18n`** shows how complete each language file is (translated texts / total).
* **Adding new bot texts:** write them with `L(lang, "bangla", "english")` in the code, run
  `python tools/extract_catalog.py` (it only APPENDS to `lines.json`, so existing line numbers never change), then add
  the new line numbers to each `locales/<lang>.tsv` (the checker tells you which are missing). A language that lacks a
  text simply shows that text in English until you add it — nothing breaks.
* Hand-polished single texts can also go into `locales/overrides/<lang>.json` (`{"English text": "translation"}`); it always wins.
* Optional (off by default): automatic translation through an API (Gemini / Google / LibreTranslate) can be switched on
  with `I18N_AUTO_TRANSLATE=1` + a key, but the manual files always take priority over it.

## Leaving early (retention) and clean chat
* The bot is admin in every promoted channel/group, so Telegram tells it **the moment** a worker leaves (`chat_member` update).
  If this happens before the 7 days are over:
  1. the reward is **held** (deducted from the balance) and the user gets a warning with a "Re-subscribe" button;
  2. earning is blocked: the Earnings button shows *"Restore your subscriptions or pay a penalty"* with
     **[Subscribe - №…] [Refresh list] [Return coins]**;
  3. *Refresh list* (or simply re-joining) returns the coins and re-opens earning — the original 7-day timer keeps running;
  4. *Return coins* pays the penalty at once (coins stay revoked, small risk score);
  5. after 24 h without re-subscribing the penalty becomes final automatically.
  A periodic check (every hour) catches missed events and removed boosts.
* Menu taps and commands (Earnings, Promote, /start ...) are deleted from the chat once the bot has answered, so only the
  result stays (inputs such as amounts or TrxID are not deleted).
