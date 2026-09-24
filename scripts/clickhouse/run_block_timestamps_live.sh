#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

mkdir -p logs/clickhouse

export PYTHONUNBUFFERED=1
: "${CLICKHOUSE_PASSWORD:?CLICKHOUSE_PASSWORD is required}"

env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy \
  python -u scripts/clickhouse/sync_block_timestamps.py continue-sync \
    --watch \
    --interval-seconds "${BLOCK_TIMESTAMPS_INTERVAL_SECONDS:-300}" \
    --confirmations "${BLOCK_TIMESTAMPS_CONFIRMATIONS:-20}" \
    --bootstrap-blocks "${BLOCK_TIMESTAMPS_BOOTSTRAP_BLOCKS:-2000}" \
    --max-catchup-blocks "${BLOCK_TIMESTAMPS_MAX_CATCHUP_BLOCKS:-5000}" \
    --workers "${BLOCK_TIMESTAMPS_WORKERS:-16}" \
    --insert-batch "${BLOCK_TIMESTAMPS_INSERT_BATCH:-1000}" \
    --no-proxy \
    --clickhouse-container "${POLYDATA_ORDERFILLED_CLICKHOUSE_CONTAINER:-polydata_clickhouse_orderfilled}" \
    --clickhouse-database "${POLYDATA_ORDERFILLED_CLICKHOUSE_DATABASE:-poly_orderfilled}" \
    --clickhouse-user "${POLYDATA_ORDERFILLED_CLICKHOUSE_USER:-poly_user}" \
    --clickhouse-password "$CLICKHOUSE_PASSWORD"
