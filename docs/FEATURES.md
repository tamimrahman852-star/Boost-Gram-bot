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
