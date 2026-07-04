# Kintara Merchant Alert Bot

A monitor-only Telegram bot that watches the Kintara merchant APIs and sends
instant alerts when important events happen.

> **Read-only.** This bot only calls GET endpoints. It never donates,
> claims, buys, sells, or calls any POST/action endpoints.

---

## Features

| Alert | Trigger |
|-------|---------|
| Merchant open for donation | `phase` changes to `donate` |
| Claim phase open | `phase` changes to `claim` |
| Donation complete | `complete` changes from `false` to `true` |
| Pool empty | `poolRemaining` drops to `0` |
| Gold available | `myAvailable > 0` (personal, requires cookie) |
| All gold claimed | `myClaimed >= myLimit` (personal, requires cookie) |

### Telegram Commands
- `/start` — Show bot status and check intervals
- `/status` — Fetch and display current merchant state immediately
- `/help` — Explain alerts and setup

---

## Requirements

- **Python 3.10+** (tested on 3.14)
- Windows Command Prompt or PowerShell
- A Telegram bot token from [@BotFather](https://t.me/BotFather)
- Your Telegram chat ID

---

## Quick Start

### 1. Install dependencies

```cmd
pip install -r requirements.txt
```

### 2. Create your `.env` file

Copy the example and edit it:

```cmd
copy .env.example .env
notepad .env
```

Fill in:

```env
TELEGRAM_BOT_TOKEN=123456789:ABCdef...   <- from @BotFather
TELEGRAM_CHAT_ID=987654321               <- your chat ID
```

### 3. (Optional) Add your Kintara cookie

To enable personal gold-claim alerts:

1. Open [kintara.com](https://kintara.com) in your browser and log in.
2. Press **F12** to open DevTools.
3. Go to the **Network** tab and refresh the page.
4. Click any `kintara.com` request.
5. Find the **Request Headers** section and copy the full value of the `Cookie` header.
6. Paste it into `.env`:

```env
KINTARA_COOKIE=your_full_cookie_string_here
```

### 4. Run the bot

```cmd
python bot.py
```

Press **Ctrl+C** to stop.

---

## Configuration Reference

| Variable | Default | Description |
|----------|---------|-------------|
| `TELEGRAM_BOT_TOKEN` | *(required)* | Bot token from @BotFather |
| `TELEGRAM_CHAT_ID` | *(required)* | Your Telegram chat ID |
| `KINTARA_COOKIE` | *(empty)* | Browser cookie for personal alerts |
| `PUBLIC_CHECK_SECONDS` | `15` | How often to check public merchant API (min 5s) |
| `PRIVATE_CHECK_SECONDS` | `60` | How often to check personal API (min 10s) |

---

## APIs Used

| API | URL | Auth |
|-----|-----|------|
| Public merchant campaign | `GET https://fanout.kintara.gg/api/world/merchant-campaign` | None |
| Public expansion tribute | `GET https://fanout.kintara.gg/api/world/expansion-tribute` | None |
| Personal merchant status | `GET https://kintara.com/api/auth/merchant-cycle-status` | Cookie |

---

## Project Files

```
Kintara Merchant Bot/
├── bot.py            <- Main bot script
├── requirements.txt  <- Python dependencies
├── .env.example      <- Configuration template
├── .env              <- Your config (create from .env.example, do not share)
├── state.json        <- Auto-created; persists last known state across restarts
└── README.md         <- This file
```

---

## State Persistence

The bot saves its last-known state to `state.json` automatically. If the bot
restarts, it reads this file and avoids sending duplicate alerts for states it
already reported.

---

## Troubleshooting

| Problem | Solution |
|---------|----------|
| `TELEGRAM_BOT_TOKEN is missing` | Ensure `.env` exists and has the token |
| Bot starts but sends no alerts | Check logs; the API may return ok=false |
| Personal alerts not working | Verify `KINTARA_COOKIE` is set and still valid |
| `HTTP 401` on private API | Cookie has expired; copy a fresh one from your browser |
| `getUpdates` errors | Transient network issue; the bot retries automatically |

---

## Getting Your Telegram Chat ID

1. Search for **@userinfobot** in Telegram and send `/start`.
2. It will reply with your user ID — use that as `TELEGRAM_CHAT_ID`.

Alternatively, send a message to your bot, then visit:
```
https://api.telegram.org/bot<YOUR_TOKEN>/getUpdates
```
and look for `"chat":{"id":...}` in the response.
