#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/../.."

mkdir -p logs/backfill

export PYTHONUNBUFFERED=1
: "${POLY_MYSQL_PASSWORD:?POLY_MYSQL_PASSWORD is required}"
: "${CLICKHOUSE_PASSWORD:?CLICKHOUSE_PASSWORD is required}"

COMMON_ARGS=(
  --backend mysql
  --mysql-host 127.0.0.1
  --mysql-port 43306
  --mysql-user poly_user
  --mysql-password "$POLY_MYSQL_PASSWORD"
  --mysql-database poly_data
  --postgres-host 127.0.0.1
  --postgres-port 45432
  --postgres-user poly_user
  --postgres-database poly_data_core
  --postgres-search-path core,oracle,ops,public
  --batch "${ORDERFILLED_LIVE_BATCH:-50}"
  --max-workers "${ORDERFILLED_LIVE_WORKERS:-1}"
  --confirmations "${ORDERFILLED_LIVE_CONFIRMATIONS:-20}"
  --market-lookup-backend postgres
  --market-backfill-mode none
  --create-placeholder-markets
  --clickhouse-write-mode clickhouse
  --clickhouse-container "${POLYDATA_ORDERFILLED_CLICKHOUSE_CONTAINER:-polydata_clickhouse_orderfilled}"
  --clickhouse-database "${POLYDATA_ORDERFILLED_CLICKHOUSE_DATABASE:-poly_orderfilled}"
  --clickhouse-user "${POLYDATA_ORDERFILLED_CLICKHOUSE_USER:-poly_user}"
  --clickhouse-password "$CLICKHOUSE_PASSWORD"
  --clickhouse-orderfilled-insert-table "${POLYDATA_ORDERFILLED_CLICKHOUSE_INSERT_TABLE:-orderfilled_fact_buffer}"
)

echo "[orderfilled-clickhouse-live] catch-up start $(date -Is)"
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy \
  POLYDATA_RPC_TRUST_ENV_PROXY=0 \
  python -u scripts/trade/trades_indexer.py --continue-sync "${COMMON_ARGS[@]}"

echo "[orderfilled-clickhouse-live] watch start $(date -Is)"
env -u HTTP_PROXY -u HTTPS_PROXY -u ALL_PROXY -u http_proxy -u https_proxy -u all_proxy \
  POLYDATA_RPC_TRUST_ENV_PROXY=0 \
  python -u scripts/trade/trades_indexer.py --continue-sync --watch \
    --interval "${ORDERFILLED_LIVE_INTERVAL:-180}" \
    "${COMMON_ARGS[@]}"
