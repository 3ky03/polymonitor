# Telegram Bot

Interactive Telegram bot for user-called polyData queries.

## Commands

- `/start`
- `/help`
- `/worldcup`
- `/matches`
- `/matches today`
- `/matches tomorrow`
- `/matches group a`
- `/matches page 2`
- `/match mexico south africa`
- `/team mexico`
- `/venue dallas`
- `/weather dallas`
- `/news mexico`
- `/odds mexico south africa`
- `/market nba`
- `/market bitcoin`
- `/wallet 0xabc...`
- `/pnl 0xabc...`
- `/signal polymarket`
- `/alert BTC 95000`
- `/alerts`
- `/alert_remove 1`

`/pnl` is intentionally coverage-only in v1. It does not output a full PnL
number until the cashflow / position snapshot serving layer is ready.

## Run

```bash
python -m telegram.bot.poller --once --dry-run
python -m telegram.bot.poller
```

## Environment

```bash
POLYDATA_TELEGRAM_BOT_TOKEN=123:abc
POLYDATA_TELEGRAM_QUERY_BOT_TOKEN=123:query-bot-token
POLYDATA_TELEGRAM_BOT_POLYDATA_API_BASE=http://127.0.0.1:18500
POLYDATA_TELEGRAM_QUERY_BOT_STATE_PATH=data/telegram_query_bot_state.json
POLYDATA_TELEGRAM_BOT_POLL_INTERVAL_SECONDS=2
POLYDATA_TELEGRAM_BOT_ALERT_CHECK_INTERVAL_SECONDS=30
POLYDATA_TELEGRAM_QUERY_BOT_ALLOWED_CHAT_IDS=
POLYDATA_TELEGRAM_QUERY_BOT_ADMIN_USER_IDS=
POLYDATA_TELEGRAM_QUERY_BOT_RATE_LIMIT_PER_MINUTE=20
```

If `POLYDATA_TELEGRAM_BOT_POLYDATA_API_BASE` is not set, the bot falls back to
the existing Telegram / polyData API env vars. If
`POLYDATA_TELEGRAM_QUERY_BOT_TOKEN` is set, it takes priority over the legacy
push-bot token so query traffic can run on a separate Telegram bot.

The query bot sets the Telegram command menu on startup and attaches inline
buttons to key World Cup replies. Team queries support common aliases such as
`USA`, `Korea`, `mex`, `墨西哥`, and `南非`.
