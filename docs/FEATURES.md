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
