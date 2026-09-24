# systemd Templates

This directory contains public user-level systemd templates for one shared
polyData codebase with two deployment roles:

- GCP serving host: API plus seed-cache watchers.
- Local collector host: market/orderfilled/oracle collectors plus local DB
  derived serving-table sync.

Both roles run the same repository commit. The boundary is the systemd target,
not a separate code tree.

## Targets

- `polydata-gcp.target`: GCP API and seed-cache services. This target must not
  start chain/indexer collectors.
- `polydata-local-collector.target`: local market/orderfilled/oracle data
  collectors and local PostgreSQL derived sync jobs.
- `polydata.target`: compatibility target that points to `polydata-gcp.target`.
  It is intentionally no longer a mixed all-in-one target.

## GCP services

`polydata-gcp.target` starts:

- `polydata-api.service`
- `polydata-db-tunnel.service` when installed by `scripts/deploy/setup_remote_readonly_api.sh`
- `polydata-*-seed.service` runtime seed watchers
- `polydata-new-market-signal.service`
- `polydata-geo-sanctions-shock.service`
- `polydata-quant-backtest-runner.service`
- `polydata-lob-maintenance.service`

These services should write/read Redis and SQLite seed snapshots. They should not
run raw market discovery, OrderFilled indexing, or oracle chain scans on GCP.
The quant backtest runner is the exception that writes quant backtest result
tables by consuming already-built queued runs; it does not rebuild price sources.

Use the remote deploy helper from the same commit you want GCP to run:

```bash
scripts/deploy/setup_remote_readonly_api.sh
```

The helper installs `polydata-gcp.target`, starts it, and explicitly stops and
disables local collector units on GCP.

## Local collector services

`polydata-local-collector.target` starts:

- `polydata-market-sync.service`
- `polydata-trade-sync.service` / OrderFilled ClickHouse live sync
- `polydata-block-timestamps-live.service` / ClickHouse block timestamp live sync
- `polydata-oracle-sync.service`
- `polydata-analytics-sync.service`
- `polydata-event-market-serving.service`
- `polydata-db-reverse-tunnel.service`
- `polydata-quant-backtest-runner.service`
- `polydata-quant-price-maintenance.service`
- `polydata-quant-price-build-runner.service`
- `polydata-quant-frontend-price-build-runner@0..1.service`

Quant price building belongs on the local collector. The default production
split is:

- `polydata-quant-price-maintenance.service`: market metadata and
  OrderFilled eligibility refresh. Keep this separate so maintenance queries do
  not stall block close catchup.
- `polydata-quant-price-build-runner.service`: OrderFilled block close builds
  only. It skips maintenance and prioritizes tokens whose
  `last_complete_block` has not reached `last_orderfilled_block`.
- `polydata-quant-frontend-price-build-runner@0..1.service`: two frontend
  prices-history shards by default. Increase
  `POLYDATA_QUANT_FRONTEND_SHARD_COUNT` and enable more instances only after
  PostgreSQL shared memory is sized for the extra parallel load.

Frontend shards do not scan every token after September 2025. They prioritize
explicit build targets, then eligible markets with at least
`POLYDATA_QUANT_FRONTEND_MIN_ORDERFILLED_TRADES` OrderFilled trades. This keeps
the production universe bounded while letting backtest-requested markets jump
the queue.

Use the local helper:

```bash
make services-install
make services-start
make services-status
```

The helper installs only the local collector target and disables GCP/API seed
runtime units locally.

## Environment

Both roles use `~/.config/polydata/polydata.env`, but with different role values:

- GCP: `POLYDATA_DEPLOY_ROLE=gcp-api`
- Local collector: `POLYDATA_DEPLOY_ROLE=local-collector`

Before copying templates manually, replace:

- `/__POLYDATA_REPO_ROOT__`

Private passwords, RPC URLs, SSH targets, and host-specific values stay in the
env file and must not be committed.

## Manual Install

GCP manual flow:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/polydata-gcp.target deploy/systemd/polydata.target ~/.config/systemd/user/
cp deploy/systemd/polydata-api.service ~/.config/systemd/user/
cp deploy/systemd/polydata-content-topic-refresh.service ~/.config/systemd/user/
cp deploy/systemd/polydata-*-seed.service ~/.config/systemd/user/
cp deploy/systemd/polydata-geo-sanctions-shock.service ~/.config/systemd/user/
cp deploy/systemd/polydata-new-market-signal.service ~/.config/systemd/user/
cp deploy/systemd/polydata-quant-backtest-runner.service ~/.config/systemd/user/
cp deploy/systemd/polydata-lob-maintenance.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now polydata-gcp.target
```

`polydata-content-topic-refresh.service` is a GCP-side seed worker. It fetches
topic-level news/video/report/research sources and writes PostgreSQL
`content_items` / `content_links`; it should not run on the local collector host.

Local collector manual flow:

```bash
mkdir -p ~/.config/systemd/user
cp deploy/systemd/polydata-local-collector.target ~/.config/systemd/user/
cp deploy/systemd/polydata-market-sync.service ~/.config/systemd/user/
cp deploy/systemd/polydata-trade-sync.service ~/.config/systemd/user/
cp deploy/systemd/polydata-block-timestamps-live.service ~/.config/systemd/user/
cp deploy/systemd/polydata-oracle-sync.service ~/.config/systemd/user/
cp deploy/systemd/polydata-analytics-sync.service ~/.config/systemd/user/
cp deploy/systemd/polydata-event-market-serving.service ~/.config/systemd/user/
cp deploy/systemd/polydata-db-reverse-tunnel.service ~/.config/systemd/user/
cp deploy/systemd/polydata-db-reverse-tunnel-healthcheck.service ~/.config/systemd/user/
cp deploy/systemd/polydata-db-reverse-tunnel-healthcheck.timer ~/.config/systemd/user/
cp deploy/systemd/polydata-quant-backtest-runner.service ~/.config/systemd/user/
cp deploy/systemd/polydata-quant-price-maintenance.service ~/.config/systemd/user/
cp deploy/systemd/polydata-quant-price-build-runner.service ~/.config/systemd/user/
cp deploy/systemd/polydata-quant-frontend-price-build-runner@.service ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now polydata-local-collector.target
systemctl --user enable --now polydata-quant-frontend-price-build-runner@0.service polydata-quant-frontend-price-build-runner@1.service
systemctl --user enable --now polydata-db-reverse-tunnel-healthcheck.timer
```

Keep user services alive after logout:

```bash
loginctl enable-linger "$USER"
```

## Safety Checks

On GCP, these units should be inactive:

```bash
systemctl --user is-active polydata-market-sync.service polydata-trade-sync.service polydata-block-timestamps-live.service polydata-oracle-sync.service polydata-analytics-sync.service polydata-event-market-serving.service polydata-db-reverse-tunnel.service polydata-local-collector.target
```

On the local collector host, `polydata-api.service` and `polydata-gcp.target`
should be inactive unless explicitly doing local API development.
