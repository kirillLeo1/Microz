# QC Quest Bot — Telegram microtasks game (aiogram 3, asyncpg, CryptoCloud, Railway-ready)

A production-ready Telegram bot with:
- Microtasks grouped into **chains** with a **30‑minute cooldown** between steps within a chain
- Internal currency **QC** (1 QC = $0.005). Daily limit: **10 steps/day**
- **One-time activation** via **CryptoCloud** ($1)
- **Referrals**: +60 QC to referrer when invited user activates
- **Withdraw requests** processed by admins
- **Multilingual** UI (uk / ru / en)
- **Admin panel** inside Telegram: stats, manage chains/steps, withdrawals, broadcast
- Works via **webhook** (Railway) or **polling** locally

---

## 1) Quick start (local, polling)

**Requirements:** Python 3.11+, PostgreSQL 14+, a Telegram bot token, a CryptoCloud API key.

```bash
python -m venv .venv
. .venv/bin/activate  # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env
# edit .env values
python -m app.main --polling
```

## 2) Deploy on Railway (webhook)

1. Push this repo to GitHub, then “Deploy from GitHub” on Railway.
2. Add **Environment Variables** (see below).
3. Set a **public domain** (Railway generates one). Put it in `WEBHOOK_URL` in env.
4. The app auto-sets webhook on start.

### Environment variables

```
BOT_TOKEN=123456:ABC-...
DATABASE_URL=postgresql://user:pass@host:5432/dbname
ADMIN_IDS=123456789,987654321          # comma-separated Telegram IDs
CRYPTOCLOUD_API_KEY=your_api_key_here
CRYPTOCLOUD_PRICE_USD=1.00             # activation price in USD
TEST_MODE=false                         # true = skip real CryptoCloud check
WEBHOOK_URL=https://your-railway-app.up.railway.app
WEBHOOK_PATH=/webhook                  # or leave default
TZ_KYIV=Europe/Kyiv
```

> `DATABASE_URL` must be a standard Postgres URI. The code uses `asyncpg` directly.

---

## 3) CryptoCloud integration

- **Create invoice**: `POST https://api.cryptocloud.plus/v2/invoice/create` with `Authorization: Token <API KEY>`
- **Check invoice(s)**: `POST https://api.cryptocloud.plus/v2/invoice/merchant/info` with `{"uuids":["INV-XXXX"]}`
- Success statuses we accept: `paid`, `overpaid`, `partial` (you can customize).
- We also support a **manual test mode**: press “I paid” and activation happens immediately if `TEST_MODE=true`.

> Docs: Invoice creation, status & auth are in CryptoCloud docs (v2).

---

## 4) Data model (PostgreSQL)

Tables auto-create at startup (idempotent):

- `users`: profile, language, status (inactive/active), referrer, balances, daily counters
- `payments`: CryptoCloud invoices
- `chains`, `steps`: tasks structure (texts in 3 langs, verify modes)
- `user_steps`: completed steps
- `user_chain_state`: per-user cooldown/position per chain
- `withdrawals`: payout requests
- `referral_rewards`: idempotent ref bonus once per referee

---

## 5) Admin panel (in-TG)

- `/admin` — enter panel (allowed only for ADMIN_IDS)
- **Tasks**: create chain, add/remove steps, toggle activity, wipe chain
- **Broadcast**: send HTML message to all users with pacing
- **Withdrawals**: view `pending`, mark processed/paid (deduct QC on “paid”)
- **Stats**: users, active, total balances, sums, payments, referrals

---

## 6) i18n

All UI strings live in `locales/*.json`. Steps have title/description in 3 languages.
User picks language at `/start`, can change in **Profile**.

---

## 7) Notes

- Daily limit (10) resets at **00:00 Europe/Kyiv**
- Cooldown between steps **within the same chain** is 30 minutes
- Membership check works only if the bot can read the channel members (make it admin if needed)
- No secrets in code; everything via env vars

---

## 8) Commands

- `/start` — language select, referral capture, activation
- **Main menu**: Tasks, Profile, Withdraw
- `/admin` — admin panel
- `/help` — short info

Enjoy! — Built for fast iteration & real use.
