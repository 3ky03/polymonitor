#!/usr/bin/env bash
set -euo pipefail

ENV_FILE="${POLYDATA_ENV_FILE:-$HOME/.config/polydata/polydata.env}"
TUNNEL_UNIT="${POLYDATA_TUNNEL_UNIT:-polydata-db-reverse-tunnel.service}"
REMOTE_APP_DIR="${POLYDATA_REMOTE_APP_DIR:-/opt/polyData}"

load_env_file() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%$'\r'}"
    [[ -n "$line" && "${line:0:1}" != "#" && "$line" == *=* ]] || continue
    local key="${line%%=*}"
    local value="${line#*=}"
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    value="${value%\"}"
    value="${value#\"}"
    export "$key=$value"
  done < "$file"
}

log() {
  printf '[postgres-tunnel-health] %s\n' "$*" >&2
}

remote_pg_check() {
  local target="$1"
  local port="$2"
  ssh \
    -o BatchMode=yes \
    -o ConnectTimeout=8 \
    -o ConnectionAttempts=2 \
    "$target" \
    "cd '$REMOTE_APP_DIR' && POLYDATA_HEALTHCHECK_POSTGRES_PORT='$port' .venv/bin/python3 - <<'PY'
import os
from pathlib import Path

env_path = Path.home() / '.config' / 'polydata' / 'polydata.env'
if env_path.exists():
    for raw_line in env_path.read_text().splitlines():
        line = raw_line.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        key, value = line.split('=', 1)
        key = key.strip()
        value = value.strip().strip('\"')
        if key and key not in os.environ:
            os.environ[key] = value

os.environ['POLYDATA_POSTGRES_HOST'] = '127.0.0.1'
os.environ['POLYDATA_POSTGRES_PORT'] = os.environ.get('POLYDATA_HEALTHCHECK_POSTGRES_PORT', '45432')

from scripts.db import db

with db.get_db(readonly=True) as conn:
    row = conn.execute('SELECT 1').fetchone()
    if not row or int(row[0]) != 1:
        raise SystemExit('unexpected PostgreSQL healthcheck result')
PY"
}

restart_tunnel() {
  local target="$1"
  local port="$2"
  log "restarting $TUNNEL_UNIT after failed remote PostgreSQL check"
  ssh \
    -o BatchMode=yes \
    -o ConnectTimeout=8 \
    -o ConnectionAttempts=2 \
    "$target" \
    "sudo -n fuser -k ${port}/tcp >/dev/null 2>&1 || fuser -k ${port}/tcp >/dev/null 2>&1 || true" || true
  systemctl --user restart "$TUNNEL_UNIT"
}

main() {
  load_env_file "$ENV_FILE"
  local target="${POLYDATA_GCP_SSH_TARGET:-}"
  local remote_port="${POLYDATA_REMOTE_POSTGRES_PORT:-45432}"
  if [[ -z "$target" ]]; then
    log "POLYDATA_GCP_SSH_TARGET is not set; skipping"
    exit 0
  fi

  if remote_pg_check "$target" "$remote_port"; then
    log "remote PostgreSQL tunnel healthy"
    exit 0
  fi

  restart_tunnel "$target" "$remote_port"
  sleep 4

  if remote_pg_check "$target" "$remote_port"; then
    log "remote PostgreSQL tunnel recovered"
    exit 0
  fi

  log "remote PostgreSQL tunnel still unhealthy after restart"
  exit 1
}

main "$@"
