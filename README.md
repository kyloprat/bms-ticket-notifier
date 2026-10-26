# BMS Ticket Notifier (Telegram)

Fork of [aviiciii/bms-ticket-notifier](https://github.com/aviiciii/bms-ticket-notifier), extended to be run
entirely from Telegram. GitHub Actions runs it every 10 minutes for free; nothing runs on a PC.

## Use it

Send the bot a **BookMyShow movie link** (the movie page or the `/buytickets/` page, any supported city).

1. It starts tracking right away: alert the moment showtimes are listed.
2. It then offers buttons to narrow it down: **dates**, **theatres**, **time of day**.
3. When something matches you get a message immediately. If nothing is available you get one note a day at 8 pm IST.

| Command | What it does |
|---|---|
| `/list` | everything being tracked, with the last result |
| `/edit N` | change dates / theatres / time of day |
| `/pause N`, `/resume N`, `/remove N` | N is the number from `/list` |

Dates: `18-20 Dec, 25 Dec`. Theatres: `PVR, INOX, IMAX`. Send `any` to clear a filter.
Replies can take up to ~10 minutes, because the bot reads your messages on each run.

**District links** are understood (`district.in/movies/...-movie-tickets-in-<city>-MV...`) but district.in
returns 403 to GitHub's servers, so those trackers show as blocked unless the watcher runs from another network.

## Setup

1. Fork this repo. Create a bot with @BotFather.
2. Repo secrets: `TELEGRAM_BOT_TOKEN`, `TELEGRAM_CHAT_ID`. Only that chat can control the bot.
3. Enable the workflow under Actions. Optional email alerts: `RESEND_API_KEY`, `RESEND_TO_EMAIL`, `RESEND_FROM_EMAIL`.

## Files

| File | Purpose |
|---|---|
| `main.py` | BookMyShow API, change detection, the run loop |
| `chat.py` | Telegram conversation, link/date parsing |
| `district.py` | District link parsing and page check |
| `trackers.json`, `bms_state.json`, `meta.json` | what is tracked, last seen showtimes, bookkeeping (committed by the workflow) |

Supported cities: see `REGION_MAP` in `main.py`. Tests: `python -m pytest -q`.
