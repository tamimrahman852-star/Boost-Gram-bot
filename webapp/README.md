# Boost Gram — Telegram Mini App (frontend)

Zero-build, dependency-free frontend (HTML + CSS + ES modules). Works inside Telegram and, for design/demo, in any browser.

```
webapp/
├─ index.html          app shell (loads telegram-web-app.js)
├─ config.js           optional API base URL
├─ css/app.css         design system (dark/light, glass, aurora)
└─ js/
   ├─ app.js           boot, router, tab bar, theme
   ├─ api.js           REAL API client  (contract: API.md)
   ├─ mock.js          full in-browser mock backend (demo mode)
   ├─ tg.js            Telegram SDK wrapper (MainButton, BackButton, haptics, CloudStorage, share…)
   ├─ ui.js            icons, sheets, toasts, confetti, helpers
   ├─ i18n.js          English + Bangla (others fall back to English)
   ├─ state.js         shared state + task/transaction metadata
   └─ views/           home · earn · promote · wallet · invite · me
```

## Try it now (no backend)
```
cd webapp && python3 -m http.server 8080
# open http://localhost:8080/?reset=1
```
Outside Telegram the app runs on `mock.js` (a "DEMO" tag is shown). Useful query params:
`?tab=earn|promote|wallet|me|invite` · `?ptab=mine|reviews` · `?seg=deposit|withdraw|gifts` · `?lang=bn` · `?level=40` (unlocks withdrawals) · `?premium=1` · `?reset=1`.
Demo promo code: `WELCOME`.

## Features
* **Home** – animated balance, level ring + XP, daily limit meter, stats, top tasks, activity.
* **Earn** – all 6 task types (channel, group, boost, post views, bot start, reactions), filters, premium gating,
  guided sheet: open → verify / timed view / screenshot proof, confetti + XP + level-up, retention notice.
* **Promote** – 4-step campaign wizard (type → link check → audience & price → slots + live cost incl. 15 % fee),
  uses Telegram's native MainButton; *My ads* (pause / resume / cancel+refund); *Reviews* (approve / reject proofs).
* **Wallet** – bKash/Nagad/Rocket deposit (TrxID + screenshot), level-gated withdrawal (USDT/bank/mobile), promo codes,
  GRAM checks (create/share/redeem), history grouped by day, pending requests.
* **Invite** – referral link, copy/share via Telegram, 3-level commission, recent friends.
* **Profile** – achievements, theme (auto/dark/light), language, notifications, haptics.
* Telegram-native: theme sync, BackButton, MainButton, haptics, safe-area, CloudStorage, closing confirmation, deep links (`startapp`).

## Going live
This folder only needs static hosting over HTTPS — and the backend endpoints in **API.md** (not part of this change).
See API.md → "Backend checklist" for the exact files to touch.
