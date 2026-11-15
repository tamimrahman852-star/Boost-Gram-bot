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

## Languages (all 21 supported)
Bangla and English are hand-written. Every other language (hi, ru, ar, es, de, fr, pt, id, tr, fa, uz, kk, uk, zh, vi, th, ja, ko, my)
is translated automatically the first time it is needed and stored in the `translations` table, so it is instant afterwards.
Placeholders (`{amount}`), HTML tags and numbers are protected and verified; if a translation can't be verified the English text is used.
Set ONE of these in Render for the translator:
| Env | Provider |
|---|---|
| `GOOGLE_TRANSLATE_API_KEY` | official Google Cloud Translation (recommended) |
| `LIBRETRANSLATE_URL` (+ `LIBRETRANSLATE_API_KEY`) | your LibreTranslate server |
| nothing | free public Google endpoint (works, but best-effort) |

When a user picks a language the bot translates it (up to ~45 s the first time), then shows the menu in that language.
Polish any text by hand: `locales/overrides/<lang>.json` → `{"English text": "your translation"}` (always wins).
After adding new texts to the code run `python tools/extract_catalog.py` and commit `locales/catalog.json`.
