#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)"
SYSTEMD_USER_DIR="${HOME}/.config/systemd/user"
POLYDATA_CONFIG_DIR="${HOME}/.config/polydata"
POLYDATA_ENV_FILE="${POLYDATA_CONFIG_DIR}/polydata.env"
SOURCE_ENV_FILE="${ROOT_DIR}/.env"

LOCAL_COLLECTOR_TARGET="polydata-local-collector.target"
LEGACY_CORE_TARGET="polydata-core.target"
LOCAL_COLLECTOR_SERVICES=(
  "polydata-market-sync.service"
  "polydata-trade-sync.service"
  "polydata-block-timestamps-live.service"
  "polydata-oracle-sync.service"
  "polydata-analytics-sync.service"
  "polydata-event-market-serving.service"
  "polydata-market-workspace-serving.service"
  "polydata-db-reverse-tunnel.service"
  "polydata-quant-backtest-runner.service"
  "polydata-quant-price-maintenance.service"
  "polydata-quant-price-build-runner.service"
  "polydata-quant-frontend-price-build-runner@.service"
)
DATA_SERVICES=(
  "polydata-market-sync.service"
  "polydata-trade-sync.service"
  "polydata-block-timestamps-live.service"
  "polydata-oracle-sync.service"
  "polydata-analytics-sync.service"
  "polydata-event-market-serving.service"
  "polydata-market-workspace-serving.service"
  "polydata-quant-backtest-runner.service"
  "polydata-quant-price-maintenance.service"
  "polydata-quant-price-build-runner.service"
  "polydata-quant-frontend-price-build-runner@0.service"
  "polydata-quant-frontend-price-build-runner@1.service"
)

usage() {
  cat <<'EOF'
Usage:
  scripts/ops/polydata_services.sh install
  scripts/ops/polydata_services.sh start
  scripts/ops/polydata_services.sh start-data
  scripts/ops/polydata_services.sh restart
  scripts/ops/polydata_services.sh restart-data
  scripts/ops/polydata_services.sh stop
  scripts/ops/polydata_services.sh status
  scripts/ops/polydata_services.sh logs [service-name]
  scripts/ops/polydata_services.sh doctor

Commands:
  install       Install user-level systemd units and a sanitized env file.
  start         Start Docker dependencies and local market/orderfilled/oracle collector services.
  start-data    Start Docker dependencies and local market/orderfilled/oracle services only.
  restart       Restart local market/orderfilled/oracle collector services.
  restart-data  Restart market/oracle/snapshot services only.
  stop          Stop local collector services.
  status        Show Docker and systemd service status.
  logs          Follow one service log. Default: polydata-market-sync.service.
  doctor        Validate env and PostgreSQL connectivity.

The local collector target intentionally excludes polydata-api.service and seed
watchers. API/runtime panel seed cache belongs on the GCP API host. The same
repository code is used on both machines; only the systemd target differs.
EOF
}

python_bin_default() {
  if [[ -n "${POLYDATA_PYTHON_BIN:-}" && -x "${POLYDATA_PYTHON_BIN}" ]]; then
    printf '%s\n' "${POLYDATA_PYTHON_BIN}"
  elif [[ -x "${HOME}/.conda/envs/polyBots/bin/python" ]]; then
    printf '%s\n' "${HOME}/.conda/envs/polyBots/bin/python"
  elif command -v python3 >/dev/null 2>&1; then
    command -v python3
  else
    printf '%s\n' "python3"
  fi
}

sanitize_env_file() {
  mkdir -p "${POLYDATA_CONFIG_DIR}"
  chmod 700 "${POLYDATA_CONFIG_DIR}"

  local python_bin
  python_bin="$(python_bin_default)"

  if [[ ! -f "${SOURCE_ENV_FILE}" ]]; then
    echo "Missing ${SOURCE_ENV_FILE}; create it before installing services." >&2
    exit 1
  fi

  python3 - "${SOURCE_ENV_FILE}" "${POLYDATA_ENV_FILE}.tmp" "${python_bin}" <<'PY'
import os
import re
import sys
from pathlib import Path

src = Path(sys.argv[1])
dst = Path(sys.argv[2])
python_bin = sys.argv[3]

key_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
values = {}

for raw in src.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    key = key.strip()
    value = value.strip()
    if not key_re.match(key):
        continue
    values[key] = value

forced = {
    "POLYDATA_DEPLOY_ROLE": "local-collector",
    "POLYMARKET_DB_BACKEND": "postgres",
    "POLYDATA_POSTGRES_HOST": values.get("POLYDATA_POSTGRES_HOST", "127.0.0.1"),
    "POLYDATA_POSTGRES_PORT": values.get("POLYDATA_POSTGRES_PORT", "45432"),
    "POLYDATA_POSTGRES_USER": values.get("POLYDATA_POSTGRES_USER", "poly_user"),
    "POLYDATA_POSTGRES_DATABASE": values.get("POLYDATA_POSTGRES_DATABASE", "poly_data_core"),
    "POLYDATA_POSTGRES_SEARCH_PATH": values.get("POLYDATA_POSTGRES_SEARCH_PATH", "core,oracle,ops,public"),
    "POLYDATA_REDIS_URL": values.get("POLYDATA_REDIS_URL", "redis://127.0.0.1:6379/0"),
    "POLYDATA_REDIS_PREFIX": values.get("POLYDATA_REDIS_PREFIX", "polydata:"),
    "POLYDATA_PYTHON_BIN": values.get("POLYDATA_PYTHON_BIN", python_bin),
    "POLYDATA_SNAPSHOT_PREWARM": "0",
}
values.update(forced)

if "POLYDATA_API_HOST" not in values:
    values["POLYDATA_API_HOST"] = "127.0.0.1"
if "POLYDATA_API_PORT" not in values:
    values["POLYDATA_API_PORT"] = "18500"

lines = [
    "# Generated by scripts/ops/polydata_services.sh install",
    "# Do not commit this file. It may contain local secrets.",
    "",
]
for key in sorted(values):
    lines.append(f"{key}={values[key]}")
dst.write_text("\n".join(lines) + "\n", encoding="utf-8")
PY

  install -m 600 "${POLYDATA_ENV_FILE}.tmp" "${POLYDATA_ENV_FILE}"
  rm -f "${POLYDATA_ENV_FILE}.tmp"
}

install_unit_file() {
  local unit="$1"
  local src="${ROOT_DIR}/deploy/systemd/${unit}"
  local dst="${SYSTEMD_USER_DIR}/${unit}"
  if [[ ! -f "${src}" ]]; then
    echo "Missing unit template: ${src}" >&2
    exit 1
  fi
  sed "s|/__POLYDATA_REPO_ROOT__|${ROOT_DIR}|g" "${src}" > "${dst}.tmp"
  install -m 644 "${dst}.tmp" "${dst}"
  rm -f "${dst}.tmp"
}

install_services() {
  mkdir -p "${SYSTEMD_USER_DIR}"
  sanitize_env_file
  for unit in "${LOCAL_COLLECTOR_SERVICES[@]}"; do
    install_unit_file "${unit}"
  done
  install_unit_file "${LOCAL_COLLECTOR_TARGET}"
  systemctl --user daemon-reload
  echo "Installed ${LOCAL_COLLECTOR_TARGET} and local collector services."
  echo "Env file: ${POLYDATA_ENV_FILE}"
}

docker_container_exists() {
  local name="$1"
  docker ps -a --format '{{.Names}}' 2>/dev/null | grep -Fxq "${name}"
}

start_docker_dependencies() {
  if ! command -v docker >/dev/null 2>&1; then
    echo "docker not found; skipping Docker dependency startup." >&2
    return
  fi

  if docker_container_exists "polydata_postgres"; then
    docker start polydata_postgres >/dev/null
    echo "PostgreSQL container ready: polydata_postgres"
  else
    echo "polydata_postgres container not found; create/configure PostgreSQL before starting services." >&2
    exit 1
  fi

  if docker_container_exists "polydata_redis"; then
    docker start polydata_redis >/dev/null
    echo "Redis container ready: polydata_redis"
  else
    docker run -d --name polydata_redis -p 6379:6379 redis:7-alpine >/dev/null
    echo "Redis container created: polydata_redis"
  fi

  if docker_container_exists "polydata_clickhouse_orderfilled"; then
    docker start polydata_clickhouse_orderfilled >/dev/null
    echo "ClickHouse container ready: polydata_clickhouse_orderfilled"
  else
    echo "polydata_clickhouse_orderfilled container not found; create/configure OrderFilled ClickHouse before starting trade sync." >&2
    exit 1
  fi
}

stop_gcp_and_legacy_runtime() {
  systemctl --user stop polydata-api.service 2>/dev/null || true
  systemctl --user disable polydata-api.service 2>/dev/null || true
  systemctl --user stop polydata.target 2>/dev/null || true
  systemctl --user disable polydata.target 2>/dev/null || true
  systemctl --user stop polydata-gcp.target 2>/dev/null || true
  systemctl --user disable polydata-gcp.target 2>/dev/null || true
  systemctl --user stop "${LEGACY_CORE_TARGET}" 2>/dev/null || true
  systemctl --user disable "${LEGACY_CORE_TARGET}" 2>/dev/null || true
  systemctl --user stop polydata-new-market-signal.service 2>/dev/null || true
  systemctl --user disable polydata-new-market-signal.service 2>/dev/null || true
}

ensure_installed() {
  if [[ ! -f "${SYSTEMD_USER_DIR}/${LOCAL_COLLECTOR_TARGET}" ]]; then
    install_services
  fi
}

start_services() {
  ensure_installed
  stop_gcp_and_legacy_runtime
  start_docker_dependencies
  systemctl --user start "${LOCAL_COLLECTOR_TARGET}"
  echo "Started ${LOCAL_COLLECTOR_TARGET}."
}

start_data_services() {
  ensure_installed
  stop_gcp_and_legacy_runtime
  start_docker_dependencies
  systemctl --user start "${DATA_SERVICES[@]}"
  echo "Started data sync services."
}

restart_services() {
  ensure_installed
  stop_gcp_and_legacy_runtime
  start_docker_dependencies
  systemctl --user restart "${LOCAL_COLLECTOR_SERVICES[@]}"
  echo "Restarted local collector services."
}

restart_data_services() {
  ensure_installed
  stop_gcp_and_legacy_runtime
  start_docker_dependencies
  systemctl --user restart "${DATA_SERVICES[@]}"
  echo "Restarted data sync services."
}

stop_services() {
  systemctl --user stop "${LOCAL_COLLECTOR_TARGET}" "${LOCAL_COLLECTOR_SERVICES[@]}" 2>/dev/null || true
  systemctl --user stop polydata-api.service 2>/dev/null || true
  echo "Stopped local collector services."
}

status_services() {
  echo "Docker containers:"
  if command -v docker >/dev/null 2>&1; then
    for name in polydata_postgres polydata_redis polydata_clickhouse_orderfilled; do
      docker ps --filter "name=${name}" --format '  {{.Names}}\t{{.Status}}\t{{.Ports}}' || true
    done
  else
    echo "  docker not found"
  fi
  echo
  local units=()
  local unit
  for unit in "${LOCAL_COLLECTOR_TARGET}" "${LOCAL_COLLECTOR_SERVICES[@]}"; do
    if systemctl --user cat "${unit}" >/dev/null 2>&1; then
      units+=("${unit}")
    fi
  done
  if [[ "${#units[@]}" -eq 0 ]]; then
    echo "No installed polyData local collector units found. Run: make services-install"
    return
  fi
  if [[ ! " ${units[*]} " =~ " ${LOCAL_COLLECTOR_TARGET} " ]]; then
    echo "${LOCAL_COLLECTOR_TARGET} is not installed yet. Run: make services-install"
    echo
  fi
  systemctl --user --no-pager --plain status "${units[@]}" 2>&1 | sed -n '1,220p'
}

logs_service() {
  local service="${1:-polydata-market-sync.service}"
  if [[ "${service}" != *.service ]]; then
    service="${service}.service"
  fi
  journalctl --user-unit "${service}" -f
}

env_file_value() {
  local key="$1"
  awk -F= -v target="${key}" '$1 == target {print substr($0, index($0, "=") + 1); exit}' "${POLYDATA_ENV_FILE}" 2>/dev/null
}

doctor() {
  ensure_installed
  python3 "${ROOT_DIR}/scripts/deploy/check_env_split.py" --role local --env "${POLYDATA_ENV_FILE}"
  local python_bin
  python_bin="$(env_file_value POLYDATA_PYTHON_BIN)"
  python_bin="${python_bin:-$(python_bin_default)}"
  (
    cd "${ROOT_DIR}"
    POLYDATA_ENV_FILE="${POLYDATA_ENV_FILE}" "${python_bin}" - <<'PY'
import os
import re
import sys
from pathlib import Path

env_path = Path(os.environ["POLYDATA_ENV_FILE"])
key_re = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
for raw in env_path.read_text(encoding="utf-8").splitlines():
    line = raw.strip()
    if not line or line.startswith("#") or "=" not in line:
        continue
    key, value = line.split("=", 1)
    key = key.strip()
    if key_re.match(key):
        os.environ[key] = value.strip()

sys.path.insert(0, str(Path("scripts").resolve()))
from db import describe_db_target, get_connection

print(describe_db_target())
conn = get_connection()
try:
    cur = conn.cursor()
    cur.execute("SELECT COUNT(*) FROM markets")
    row = cur.fetchone()
    count = row[0] if not hasattr(row, "get") else row.get("count")
    print(f"markets_count={count}")
finally:
    conn.close()
PY
  )
}

cmd="${1:-help}"
shift || true

case "${cmd}" in
  install)
    install_services
    ;;
  start)
    start_services
    ;;
  start-data)
    start_data_services
    ;;
  restart)
    restart_services
    ;;
  restart-data)
    restart_data_services
    ;;
  stop)
    stop_services
    ;;
  status)
    status_services
    ;;
  logs)
    logs_service "${1:-}"
    ;;
  doctor)
    doctor
    ;;
  help|-h|--help)
    usage
    ;;
  *)
    echo "Unknown command: ${cmd}" >&2
    usage >&2
    exit 2
    ;;
esac
