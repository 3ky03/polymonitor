# Telegram

This directory is the Telegram processing layer for polyData.

- `topics/`: group/forum topic publishing. It reads runtime panel API snapshots,
  formats them into Telegram messages, deduplicates already-sent updates, and
  sends them through the Telegram Bot API.
- `bot/`: reserved for the interactive user-called bot, similar in spirit to
  GMGN/AVE bots. Command handlers and query routing should live here.

The old root module entrypoints remain as compatibility shims, but new code
should import from `telegram.topics.*`.

## First Run

Create the Telegram channels/groups manually, add your bot as an admin, then
set:

```bash
POLYDATA_TELEGRAM_BOT_TOKEN=123:abc
POLYDATA_TELEGRAM_REMOTE_API_BASE=https://<your-api-host>/wm-api
POLYDATA_TELEGRAM_CHANNEL_NEWS=-1001234567890
POLYDATA_TELEGRAM_CHANNEL_INTEL=-1001234567890
POLYDATA_TELEGRAM_CHANNEL_ALPHA=-1001234567890
POLYDATA_TELEGRAM_CHANNEL_MACRO=-1001234567890
POLYDATA_TELEGRAM_CHANNEL_NBA=@your_nba_channel
POLYDATA_TELEGRAM_CHANNEL_WORLDCUP=-1001234567890
POLYDATA_TELEGRAM_CHANNEL_WEATHER=@your_weather_channel
POLYDATA_TELEGRAM_CHANNEL_MONITOR=@your_main_channel
POLYDATA_TELEGRAM_THREAD_NEWS=12
POLYDATA_TELEGRAM_THREAD_INTEL=14
POLYDATA_TELEGRAM_THREAD_ALPHA=10
POLYDATA_TELEGRAM_THREAD_MACRO=8
POLYDATA_TELEGRAM_THREAD_WORLDCUP=16
```

`latest-content` publishes to the News topic. Market-scoped `related-news`
publishes one compact summary to the Intel topic so the News topic stays focused
on global headlines. `worldcup-intel` publishes a compact Signals / News /
Weather summary to the World Cup topic and links to
`https://www.polymonitor.club/?workspace=worldcup`.

Dry run:

```bash
python -m telegram.topics.publisher --once --dry-run
```

The publisher probes configured API candidates with `/health` and uses the
first healthy one. For the current split setup, the remote GCP API usually
looks like `http://<gcp-host>/wm-api`; local development API is only a fallback.

Prime state without sending the current backlog:

```bash
python -m telegram.topics.publisher --once --prime
```

Run continuously:

```bash
python -m telegram.topics.publisher --watch
```

To publish the same payload when the live website/API fetches a supported panel,
enable the API-side bridge on the machine running `polydata-api.service`:

```bash
POLYDATA_TELEGRAM_PUBLISH_ON_API_FETCH=true
```

The API returns normally while a background thread sends Telegram messages.
`data/telegram_state.json` deduplicates messages so repeated page refreshes do
not repost the same update.

## Panel delivery catalog

`topics/catalog.py` accounts for every frontend Panel and every server runtime
Panel. Each entry has exactly one delivery mode:

- `specialized`: a product-specific formatter and topic;
- `generic`: a redacted, concise semantic-change summary;
- `aggregate`: already represented by another Telegram feed;
- `market-scoped`: needs a selected market or a dedicated alert event;
- `browser-only`: no independent server snapshot exists;
- `non-pushable`: high-frequency visual data that should not become messages.

The normal publisher remains intentionally limited to the original 11 sources.
It does not start polling every Panel after an upgrade. Operators may audit the
complete server-side catalog explicitly with four bounded, isolated runtime
requests at a time plus the two existing non-runtime feeds. A slow Panel is
reported independently and cannot discard the other Panel snapshots:

```bash
python -m telegram.topics.publisher --once --target all-panels --dry-run
```

This command prints candidates and does not call Telegram or mutate dedupe
state. Result JSON reports these as `previewed`; the legacy `sent` field also
counts printed dry-run previews for backward compatibility, and must be read
together with `dry_run=true`. Before enabling recurring `all-panels` delivery, prime the current
snapshot so existing records are not posted as a backlog:

```bash
python -m telegram.topics.publisher --once --target all-panels --prime
```

The first non-dry `all-panels` run also primes automatically. The catalog
version is recorded only after all expected runtime and non-runtime snapshots
were fetched; a partial run primes the returned candidates but remains
unprimed, so recovered sources are safely absorbed on the next run. Publisher
heartbeat/result JSON exposes formatter counts for specialized, generic,
empty, aggregate, market-scoped, browser-only, non-pushable, unsupported and
format-error outcomes.
