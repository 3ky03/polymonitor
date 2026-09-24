# polyData Monitor

This repository is a source snapshot of the running Polymonitor deployment.
See [SNAPSHOT.md](SNAPSHOT.md) for its scope, provenance, and setup boundaries.

polyData Monitor is a live intelligence workspace for Polymarket operators,
analysts, and market watchers who need to understand what is moving, why it is
moving, and where risk is building.

It brings market prices, on-chain flow, oracle activity, order-book depth,
macro context, sports signals, weather markets, news, and AI-generated market
briefs into one monitoring surface.

Instead of switching between Polymarket, block explorers, spreadsheets, news
feeds, and internal notes, users get a single command center for live market
awareness.

## What It Helps You Do

- Track active Polymarket markets and fast-changing outcomes in real time.
- Watch large trades, suspicious flow, and fresh OrderFilled activity.
- Inspect market depth, best bid/ask conditions, and visible liquidity.
- Follow oracle events so resolution risk is easier to spot before it matters.
- Connect market moves with related news, reports, video, and research context.
- Monitor macro, CPI, Fed, crypto, commodities, sports, NBA, weather, and
  geopolitical panels from the same workspace.
- Use AI market insights to turn fragmented signals into a short, readable
  brief.

## Who It Is For

polyData Monitor is designed for people who need a faster read on prediction
markets:

- Polymarket traders watching price, liquidity, and event risk.
- Research teams following market narratives across categories.
- Risk and operations teams checking oracle status, sync health, and abnormal
  flow.
- Content and community teams looking for markets worth explaining now.
- Builders who want a live example of a Polymarket intelligence product.

## Product Experience

The dashboard opens as a live global market atlas. From there, users can focus
on a market, scan outcome probabilities, inspect order-book depth, review recent
trades, check oracle history, and read linked context without leaving the page.

The workspace is panel based, so the same product can support different modes:

- A market tape for active trading.
- A resolution-risk desk for oracle-sensitive markets.
- A macro board for CPI, Fed, growth, labor, energy, and recession signals.
- A sports board for NBA, odds, and matchup intelligence.
- A weather market board for city temperature markets and forecast ranges.
- A content desk for related news, video, reports, and research.

## Current Capabilities

### Live Polymarket View

Active markets, grouped outcomes, market summaries, price movement, volume,
trade counts, and focused market context.

### Flow and Liquidity

OrderFilled tape, whale tracking, suspicious-flow monitoring, runtime order
book depth, best bid/ask snapshots, and focused trade panels.

### Oracle and Resolution Watch

Recent oracle events, focused oracle timelines, proposal and settlement context,
and links between oracle activity and the relevant markets.

### AI Market Insights

Agent-generated briefs that summarize the selected market, surface focus
signals, and organize supporting evidence for faster review.

### Macro and Event Panels

CPI release timing, inflation nowcasts, Fed/rates context, labor and services
pressure, tariff and supply-chain watch, energy and gasoline shocks, food and
retail basket pressure, crypto funding, commodities, geopolitical sanctions,
and Polymarket macro-market clusters.

### Sports, Weather, and Content

NBA scoreboard and intel, sports odds comparison, ESPN matchup indicators,
global temperature monitoring, weather quote curves, related news, video,
reports, and research feeds.

### Telegram Publishing

The project includes a Telegram publishing layer for sending selected monitor
updates into topic-based channels.

## Why It Matters

Prediction markets move when price, liquidity, information, and resolution risk
change together. polyData Monitor is built around that reality.

It gives users a faster way to answer practical questions:

- Which markets are active right now?
- Which outcomes are moving?
- Is the move supported by trade flow or thin liquidity?
- Are there oracle events or settlement risks nearby?
- What external news or macro context may explain the move?
- Which markets deserve attention before the crowd notices?

## Access and Deployment

The product can be served as a web dashboard backed by the polyData runtime API.
For a production-style deployment, the recommended setup is:

- serve the built dashboard as static web assets
- proxy dashboard API requests through `/wm-api/`
- keep data sync jobs and runtime services running separately

Public deployment templates are available in [`deploy/`](deploy/README.md).

## Project Status

polyData Monitor is an actively developed product workspace. The current public
dashboard version shown in the app is `v0.2.1`.

The product already covers live market monitoring, oracle tracking, runtime
order-book views, macro/sports/weather panels, AI insights, and Telegram
publishing. Upcoming work is focused on sharper customer-facing workflows,
cleaner sharing, stronger runtime reliability, and deeper signal quality.

## For Teams Running the Project

This README is intentionally customer facing. Technical setup and maintenance
details live in the supporting docs:

- [`docs/development.md`](docs/development.md) for local development commands
- [`docs/architecture.md`](docs/architecture.md) for system shape and runtime
  boundaries
- [`docs/panel-modules.md`](docs/panel-modules.md) for dashboard panel modules
- [`deploy/README.md`](deploy/README.md) for deployment templates
- [`deploy/systemd/README.md`](deploy/systemd/README.md) for service templates
- [`telegram/README.md`](telegram/README.md) for Telegram publishing setup
- [`docs/updates.md`](docs/updates.md) for the public update log

Private host-specific notes and sensitive operational details should stay out
of this README.
