#!/usr/bin/env bash

set -euo pipefail

# One-shot helper to configure a remote GCP VM as a readonly polyData API host.
# Run this from the current machine after SSH key access to the remote host works.

REMOTE_USER="${REMOTE_USER:-}"
REMOTE_HOST="${REMOTE_HOST:-}"
REMOTE_REPO_ROOT="${REMOTE_REPO_ROOT:-/opt/polyData}"
REMOTE_WEB_ROOT="${REMOTE_WEB_ROOT:-/var/www/polydata}"

# The remote VM will create an SSH local-forward:
#   127.0.0.1:${REMOTE_DB_TUNNEL_PORT} -> ${LOCAL_DB_FORWARD_HOST}:${LOCAL_DB_FORWARD_PORT}
# over SSH to ${LOCAL_DB_SSH_USER}@${LOCAL_DB_SSH_HOST}
LOCAL_DB_SSH_USER="${LOCAL_DB_SSH_USER:-}"
LOCAL_DB_SSH_HOST="${LOCAL_DB_SSH_HOST:-}"
LOCAL_DB_FORWARD_HOST="${LOCAL_DB_FORWARD_HOST:-127.0.0.1}"
LOCAL_DB_FORWARD_PORT="${LOCAL_DB_FORWARD_PORT:-45432}"
REMOTE_DB_TUNNEL_PORT="${REMOTE_DB_TUNNEL_PORT:-45432}"
LOCAL_CLICKHOUSE_FORWARD_HOST="${LOCAL_CLICKHOUSE_FORWARD_HOST:-127.0.0.1}"
LOCAL_CLICKHOUSE_FORWARD_PORT="${LOCAL_CLICKHOUSE_FORWARD_PORT:-18123}"
REMOTE_CLICKHOUSE_TUNNEL_PORT="${REMOTE_CLICKHOUSE_TUNNEL_PORT:-18123}"

POSTGRES_DATABASE="${POSTGRES_DATABASE:-poly_data_core}"
POSTGRES_USER="${POSTGRES_USER:-poly_user}"
POSTGRES_PASSWORD="${POSTGRES_PASSWORD:-}"
POSTGRES_SEARCH_PATH="${POSTGRES_SEARCH_PATH:-core,oracle,ops,public}"
CLICKHOUSE_DATABASE="${CLICKHOUSE_DATABASE:-poly_orderfilled}"
CLICKHOUSE_USER="${CLICKHOUSE_USER:-poly_user}"
CLICKHOUSE_PASSWORD="${CLICKHOUSE_PASSWORD:-${POLYDATA_ORDERFILLED_CLICKHOUSE_PASSWORD:-}}"
API_PORT="${API_PORT:-18500}"
SERVER_NAME="${SERVER_NAME:-${REMOTE_HOST}}"
PYTHON_BIN="${PYTHON_BIN:-${REMOTE_REPO_ROOT}/.venv/bin/python}"
SNAPSHOT_SQLITE_PATH="${SNAPSHOT_SQLITE_PATH:-${REMOTE_REPO_ROOT}/data/panel_snapshots.sqlite3}"
REDIS_URL="${REDIS_URL:-redis://127.0.0.1:6379/0}"

: "${REMOTE_USER:?Set REMOTE_USER to the SSH user for the remote API host}"
: "${REMOTE_HOST:?Set REMOTE_HOST to the remote API host}"
GUNICORN_WORKERS="${GUNICORN_WORKERS:-3}"
GUNICORN_THREADS="${GUNICORN_THREADS:-4}"
GUNICORN_MAX_REQUESTS="${GUNICORN_MAX_REQUESTS:-300}"
GUNICORN_MAX_REQUESTS_JITTER="${GUNICORN_MAX_REQUESTS_JITTER:-60}"

if [[ -z "${LOCAL_DB_SSH_USER}" || -z "${LOCAL_DB_SSH_HOST}" ]]; then
  echo "LOCAL_DB_SSH_USER and LOCAL_DB_SSH_HOST are required." >&2
  exit 1
fi

if [[ -z "${POSTGRES_PASSWORD}" ]]; then
  echo "POSTGRES_PASSWORD is required." >&2
  exit 1
fi

if [[ -z "${CLICKHOUSE_PASSWORD}" ]]; then
  echo "CLICKHOUSE_PASSWORD or POLYDATA_ORDERFILLED_CLICKHOUSE_PASSWORD is required." >&2
  exit 1
fi

SSH_OPTS=(
  -o BatchMode=yes
  -o StrictHostKeyChecking=accept-new
  -o ConnectTimeout=10
)

echo "[1/7] Checking SSH access to ${REMOTE_USER}@${REMOTE_HOST} ..."
ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" "echo connected: \$(hostname)"

echo "[2/7] Uploading runtime configuration ..."
LOCAL_ENV_FILE="$(mktemp)"
LOCAL_TUNNEL_SERVICE="$(mktemp)"
LOCAL_API_SERVICE="$(mktemp)"
LOCAL_NGINX_CONF="$(mktemp)"
trap 'rm -f "${LOCAL_ENV_FILE}" "${LOCAL_TUNNEL_SERVICE}" "${LOCAL_API_SERVICE}" "${LOCAL_NGINX_CONF}"' EXIT

cat > "${LOCAL_ENV_FILE}" <<EOF
POLYMARKET_DB_BACKEND=postgres
POLYDATA_DEPLOY_ROLE=gcp-api
POLYDATA_POSTGRES_HOST=127.0.0.1
POLYDATA_POSTGRES_PORT=${REMOTE_DB_TUNNEL_PORT}
POLYDATA_POSTGRES_USER=${POSTGRES_USER}
POLYDATA_POSTGRES_PASSWORD=${POSTGRES_PASSWORD}
POLYDATA_POSTGRES_DATABASE=${POSTGRES_DATABASE}
POLYDATA_POSTGRES_SEARCH_PATH=${POSTGRES_SEARCH_PATH}

POLYDATA_ORDERFILLED_CLICKHOUSE_READ_ENABLED=1
POLYDATA_ORDERFILLED_CLICKHOUSE_HTTP_URL=http://127.0.0.1:${REMOTE_CLICKHOUSE_TUNNEL_PORT}
POLYDATA_ORDERFILLED_CLICKHOUSE_DATABASE=${CLICKHOUSE_DATABASE}
POLYDATA_ORDERFILLED_CLICKHOUSE_USER=${CLICKHOUSE_USER}
CLICKHOUSE_PASSWORD=${CLICKHOUSE_PASSWORD}

POLYDATA_PYTHON_BIN=${PYTHON_BIN}
POLYDATA_API_READONLY=1
POLYDATA_API_HOST=127.0.0.1
POLYDATA_API_PORT=${API_PORT}
POLYDATA_SNAPSHOT_PREWARM=1
POLYDATA_GUNICORN_WORKERS=${GUNICORN_WORKERS}
POLYDATA_GUNICORN_THREADS=${GUNICORN_THREADS}
POLYDATA_GUNICORN_MAX_REQUESTS=${GUNICORN_MAX_REQUESTS}
POLYDATA_GUNICORN_MAX_REQUESTS_JITTER=${GUNICORN_MAX_REQUESTS_JITTER}
POLYDATA_REDIS_URL=${REDIS_URL}
POLYDATA_REDIS_PREFIX=polydata:
POLYDATA_SNAPSHOT_SQLITE_PATH=${SNAPSHOT_SQLITE_PATH}
POLYDATA_RUNTIME_TRUST_ENV=0
POLYDATA_CONTENT_API_REFRESH_ENABLED=false
POLYDATA_CONTENT_MARKET_DYNAMIC=false
POLYDATA_CONTENT_TOPIC_SEARCH_PROVIDER=true
POLYDATA_CONTENT_TOPIC_MEDIA_SEARCH_PROVIDER=true
POLYDATA_CONTENT_TOPIC_RESEARCH_PROVIDER=true
POLYDATA_CONTENT_TOPIC_REFRESH_INTERVAL_SECONDS=900
POLYDATA_CONTENT_TOPIC_REFRESH_LIMIT=48
POLYDATA_CONTENT_TOPIC_IDS=
POLYDATA_AGENT_ENABLED=false
POLYDATA_AGENT_LOCAL_ONLY=true
POLYDATA_AGENT_RATE_LIMIT_PER_MINUTE=6
POLYDATA_MARKETS_RUNTIME_PRICES=0
POLYDATA_MARKETS_LATEST_SNAPSHOT_FALLBACK=1
POLYDATA_ALLOWED_ORIGINS=
POLYDATA_CLOB_API_BASE=https://clob.polymarket.com
POLYDATA_CLOB_WS_URL=wss://ws-subscriptions-clob.polymarket.com/ws/market
POLYDATA_API_LOB_WEBSOCKET_ENABLED=0
POLYDATA_LOB_WEBSOCKET_ENABLED=1
POLYDATA_LOB_RUNTIME_STATUS_PATH=/tmp/polydata/local-orderbook-websocket-status.json
POLYDATA_LOB_RUNTIME_STATUS_WRITE_INTERVAL_SECONDS=2
POLYDATA_LOB_COVERAGE_LIMIT=250
POLYDATA_LOB_COVERAGE_TOPICS=worldcup,crypto,politics
POLYDATA_LOB_COVERAGE_REFRESH_SECONDS=60
POLYDATA_LOB_BOOTSTRAP_MARKET_LIMIT=6
POLYDATA_LOB_WS_PERSIST_ENABLED=1
POLYDATA_LOB_UNCHANGED_SNAPSHOT_MIN_INTERVAL_SECONDS=300
POLYDATA_LOB_MAINTENANCE_INTERVAL_SECONDS=21600
POLYDATA_LOB_ROLLUP_BEFORE_DAYS=7
POLYDATA_LOB_HOT_RETENTION_DAYS=14
POLYDATA_LOB_WARM_RETENTION_DAYS=14
POLYDATA_LOB_COLD_RETENTION_DAYS=7
POLYDATA_LOB_MAINTENANCE_BATCH_SIZE=5000
POLYDATA_LOB_DRIFT_CHECK_INTERVAL_SECONDS=60
POLYDATA_LOB_DRIFT_CHECK_MAX_PER_TICK=3
POLYDATA_LOB_DRIFT_SECONDS_HOT=900
POLYDATA_LOB_DRIFT_SECONDS_WARM=1800
POLYDATA_LOB_DRIFT_SECONDS_COLD=3600
POLYDATA_LOB_STALE_IDLE_SECONDS_HOT=120
POLYDATA_LOB_STALE_IDLE_SECONDS_WARM=300
POLYDATA_LOB_STALE_IDLE_SECONDS_COLD=900
POLYDATA_LOB_CLICKHOUSE_ENABLED=0
POLYDATA_LOB_CLICKHOUSE_TIERS=hot,warm
POLYDATA_LOB_CLICKHOUSE_DELTA_TABLE=quant_lob_delta_fact
POLYDATA_LOB_CLICKHOUSE_LEVEL_TABLE=quant_lob_level_fact
POLYDATA_LOB_CLICKHOUSE_BATCH_SIZE=250
POLYDATA_LOB_CLICKHOUSE_FLUSH_INTERVAL_SECONDS=5
POLYDATA_LOB_CLICKHOUSE_TTL_DAYS=30
POLYDATA_LOB_CLICKHOUSE_WRITE_LEVELS=1
POLYDATA_LOB_WORLDCUP_GUARD_INTERVAL_SECONDS=300
POLYDATA_LOB_WORLDCUP_GUARD_API_TIMEOUT_SECONDS=60
POLYDATA_LOB_WORLDCUP_GUARD_COVERAGE_API_TIMEOUT_SECONDS=15
POLYDATA_LOB_WORLDCUP_GUARD_RUN_TIMEOUT_SECONDS=120
POLYDATA_LOB_WORLDCUP_GUARD_POSTGRES_STATEMENT_TIMEOUT_MS=15000
POLYDATA_LOB_WORLDCUP_GUARD_STATUS_PATH=/tmp/polydata/worldcup-lob-guard-status.json
POLYDATA_LOB_WORLDCUP_GUARD_LOOKAHEAD_HOURS=36
POLYDATA_LOB_WORLDCUP_GUARD_LOOKBACK_HOURS=12
POLYDATA_LOB_WORLDCUP_GUARD_MIN_MARKETS=1
POLYDATA_LOB_WORLDCUP_GUARD_CLICKHOUSE_ENABLED=0
EOF

cat > "${LOCAL_TUNNEL_SERVICE}" <<EOF
[Unit]
Description=polyData DB SSH tunnel
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
ExecStart=/usr/bin/ssh -N -L ${REMOTE_DB_TUNNEL_PORT}:${LOCAL_DB_FORWARD_HOST}:${LOCAL_DB_FORWARD_PORT} -L ${REMOTE_CLICKHOUSE_TUNNEL_PORT}:${LOCAL_CLICKHOUSE_FORWARD_HOST}:${LOCAL_CLICKHOUSE_FORWARD_PORT} ${LOCAL_DB_SSH_USER}@${LOCAL_DB_SSH_HOST} -o ServerAliveInterval=30 -o ServerAliveCountMax=3 -o ExitOnForwardFailure=yes
Restart=always
RestartSec=5

[Install]
WantedBy=polydata-gcp.target
EOF

cat > "${LOCAL_API_SERVICE}" <<EOF
[Unit]
Description=polyData readonly API service
After=network-online.target polydata-db-tunnel.service
Wants=network-online.target polydata-db-tunnel.service

[Service]
Type=simple
WorkingDirectory=${REMOTE_REPO_ROOT}
EnvironmentFile=%h/.config/polydata/polydata.env
ExecStart=/bin/bash -lc 'POLYDATA_LOB_WEBSOCKET_ENABLED="${POLYDATA_API_LOB_WEBSOCKET_ENABLED:-0}" exec "${REMOTE_REPO_ROOT}/.venv/bin/gunicorn" --workers "${POLYDATA_GUNICORN_WORKERS:-3}" --threads "${POLYDATA_GUNICORN_THREADS:-4}" --bind "${POLYDATA_API_HOST:-127.0.0.1}:${POLYDATA_API_PORT:-18500}" --timeout 180 --graceful-timeout 30 --max-requests "${POLYDATA_GUNICORN_MAX_REQUESTS:-300}" --max-requests-jitter "${POLYDATA_GUNICORN_MAX_REQUESTS_JITTER:-60}" scripts.api.app:app'
Restart=always
RestartSec=10
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=polydata-gcp.target
EOF

cat > "${LOCAL_NGINX_CONF}" <<EOF
server {
    listen 80;
    server_name ${SERVER_NAME} _;

    root ${REMOTE_WEB_ROOT};
    index index.html;

    location / {
        try_files \$uri \$uri/ /index.html;
    }

    location ^~ /wm-api/agent/ {
        return 403;
    }

    location /wm-api/ {
        proxy_pass http://127.0.0.1:${API_PORT}/;
        proxy_http_version 1.1;
        proxy_set_header Host \$host;
        proxy_set_header X-Real-IP \$remote_addr;
        proxy_set_header X-Forwarded-For \$proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto \$scheme;
        proxy_connect_timeout 10s;
        proxy_read_timeout 60s;
        proxy_send_timeout 60s;
    }
}
EOF

scp "${SSH_OPTS[@]}" "${LOCAL_ENV_FILE}" "${REMOTE_USER}@${REMOTE_HOST}:/tmp/polydata.env"
scp "${SSH_OPTS[@]}" "${LOCAL_TUNNEL_SERVICE}" "${REMOTE_USER}@${REMOTE_HOST}:/tmp/polydata-db-tunnel.service"
scp "${SSH_OPTS[@]}" "${LOCAL_API_SERVICE}" "${REMOTE_USER}@${REMOTE_HOST}:/tmp/polydata-api.service"
scp "${SSH_OPTS[@]}" "${LOCAL_NGINX_CONF}" "${REMOTE_USER}@${REMOTE_HOST}:/tmp/polydata-nginx.conf"

echo "[3/7] Installing packages, services, and env on the remote VM ..."
ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" bash <<'EOF'
set -euo pipefail

REMOTE_REPO_ROOT="${REMOTE_REPO_ROOT:-/opt/polyData}"
REMOTE_WEB_ROOT="${REMOTE_WEB_ROOT:-/var/www/polydata}"

sudo apt update
sudo apt install -y nginx redis-server python3-venv python3-pip rsync

mkdir -p "${HOME}/.config/polydata"
mkdir -p "${HOME}/.config/systemd/user"
mkdir -p "${REMOTE_REPO_ROOT}/data"
chmod 700 "${HOME}/.config/polydata"

install -m 600 /tmp/polydata.env "${HOME}/.config/polydata/polydata.env"
install -m 644 /tmp/polydata-db-tunnel.service "${HOME}/.config/systemd/user/polydata-db-tunnel.service"
install -m 644 /tmp/polydata-api.service "${HOME}/.config/systemd/user/polydata-api.service"

GCP_UNITS=(
  polydata-gcp.target
  polydata.target
  polydata-alpha-signal-seed.service
  polydata-bootstrap-seed.service
  polydata-content-topic-refresh.service
  polydata-cpi-release-calendar-seed.service
  polydata-crypto-funding-seed.service
  polydata-defi-token-watch-seed.service
  polydata-energy-gasoline-shock-seed.service
  polydata-f1-seed.service
  polydata-finance-external-sources-seed.service
  polydata-finance-watch-panels-seed.service
  polydata-food-retail-basket-seed.service
  polydata-geo-sanctions-shock.service
  polydata-global-weather-map-seed.service
  polydata-grid-esports-seed.service
  polydata-inflation-nowcast-seed.service
  polydata-jin10-seed.service
  polydata-lob-websocket.service
  polydata-lob-maintenance.service
  polydata-worldcup-lob-guard.service
  polydata-macro-cpi-panels-seed.service
  polydata-macro-cpi-registry-seed.service
  polydata-market-group-seed.service
  polydata-nba-seed.service
  polydata-new-market-signal.service
  polydata-polybeats-feed-seed.service
  polydata-polymarket-macro-map-seed.service
  polydata-quant-backtest-runner.service
  polydata-sports-odds-seed.service
  polydata-suspicious-trades-seed.service
  polydata-tech-panels-seed.service
  polydata-telegram-query-bot.service
  polydata-weather-news-seed.service
  polydata-whale-trades-seed.service
  polydata-worldcup-dashboard-seed.service
)
for unit in "${GCP_UNITS[@]}"; do
  src="${REMOTE_REPO_ROOT}/deploy/systemd/${unit}"
  if [[ ! -f "${src}" ]]; then
    echo "Missing GCP unit template: ${src}" >&2
    exit 1
  fi
  sed "s|/__POLYDATA_REPO_ROOT__|${REMOTE_REPO_ROOT}|g" "${src}" > "${HOME}/.config/systemd/user/${unit}"
done

if [[ ! -x "${REMOTE_REPO_ROOT}/.venv/bin/python" ]]; then
  python3 -m venv "${REMOTE_REPO_ROOT}/.venv"
fi
"${REMOTE_REPO_ROOT}/.venv/bin/pip" install -U pip
"${REMOTE_REPO_ROOT}/.venv/bin/pip" install -r "${REMOTE_REPO_ROOT}/scripts/requirements.txt"

sudo mkdir -p "${REMOTE_WEB_ROOT}"
cd "${REMOTE_REPO_ROOT}/webpage"
npm install
npm run build
sudo rsync -av --delete "${REMOTE_REPO_ROOT}/webpage/dist/" "${REMOTE_WEB_ROOT}/"

sudo install -m 644 /tmp/polydata-nginx.conf /etc/nginx/sites-available/polydata
sudo ln -sf /etc/nginx/sites-available/polydata /etc/nginx/sites-enabled/polydata
sudo nginx -t

systemctl --user daemon-reload
systemctl --user disable polydata-core.target polydata-local-collector.target >/dev/null 2>&1 || true
systemctl --user stop \
  polydata-core.target \
  polydata-local-collector.target \
  polydata-market-sync.service \
  polydata-trade-sync.service \
  polydata-oracle-sync.service \
  polydata-analytics-sync.service \
  polydata-event-market-serving.service \
  polydata-market-workspace-serving.service \
  polydata-db-reverse-tunnel.service \
  >/dev/null 2>&1 || true
systemctl --user disable \
  polydata-local-collector.target \
  polydata-market-sync.service \
  polydata-trade-sync.service \
  polydata-oracle-sync.service \
  polydata-analytics-sync.service \
  polydata-event-market-serving.service \
  polydata-market-workspace-serving.service \
  polydata-db-reverse-tunnel.service \
  >/dev/null 2>&1 || true
systemctl --user enable --now polydata-gcp.target

sudo systemctl enable --now redis-server
sudo systemctl enable --now nginx
sudo systemctl reload nginx

loginctl enable-linger "${USER}" >/dev/null 2>&1 || true
EOF

echo "[4/7] Verifying DB tunnel and API health ..."
ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" "set -a; . ~/.config/polydata/polydata.env; set +a; python3 - <<'PY'
import os
import psycopg
conn = psycopg.connect(
    host=os.environ['POLYDATA_POSTGRES_HOST'],
    port=int(os.environ['POLYDATA_POSTGRES_PORT']),
    user=os.environ['POLYDATA_POSTGRES_USER'],
    password=os.environ['POLYDATA_POSTGRES_PASSWORD'],
    dbname=os.environ['POLYDATA_POSTGRES_DATABASE'],
    options=f\"-c search_path={os.environ.get('POLYDATA_POSTGRES_SEARCH_PATH', 'core,oracle,ops,public')}\",
)
with conn.cursor() as cur:
    cur.execute('SELECT 1')
    print('db-ok', cur.fetchone())
conn.close()
PY"

ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" "set -a; . ~/.config/polydata/polydata.env; set +a; python3 - <<'PY'
import os
from urllib.parse import urlencode
from urllib.request import Request, urlopen

base = os.environ['POLYDATA_ORDERFILLED_CLICKHOUSE_HTTP_URL'].rstrip('/')
params = urlencode({
    'database': os.environ.get('POLYDATA_ORDERFILLED_CLICKHOUSE_DATABASE', 'poly_orderfilled'),
    'user': os.environ.get('POLYDATA_ORDERFILLED_CLICKHOUSE_USER', 'poly_user'),
    'password': os.environ.get('CLICKHOUSE_PASSWORD', ''),
})
separator = '&' if '?' in base else '?'
req = Request(f'{base}{separator}{params}', data=b'SELECT 1 FORMAT JSONEachRow', method='POST')
with urlopen(req, timeout=5) as resp:
    print('clickhouse-ok', resp.read().decode().strip())
PY"

ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" "curl -fsS http://127.0.0.1:${API_PORT}/health && echo && curl -fsS http://127.0.0.1:${API_PORT}/system/health >/dev/null && echo api-ok"

echo "[5/7] Verifying Nginx proxy ..."
ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" "curl -fsS http://127.0.0.1/wm-api/health && echo"

echo "[6/7] Remote service status ..."
ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" "systemctl --user --no-pager --full status polydata-gcp.target polydata-db-tunnel.service polydata-api.service polydata-lob-websocket.service | sed -n '1,220p'"
ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" "for unit in polydata-market-sync.service polydata-trade-sync.service polydata-oracle-sync.service polydata-analytics-sync.service polydata-event-market-serving.service polydata-market-workspace-serving.service polydata-db-reverse-tunnel.service polydata-local-collector.target; do state=\$(systemctl --user is-active \"\$unit\" 2>/dev/null || true); printf '%s %s\n' \"\$unit\" \"\$state\"; test \"\$state\" != active; done"
ssh "${SSH_OPTS[@]}" "${REMOTE_USER}@${REMOTE_HOST}" "sudo systemctl --no-pager --full status nginx redis-server | sed -n '1,160p'"

echo "[7/7] Done."
echo "Remote PostgreSQL + ClickHouse readonly API host is configured on ${REMOTE_HOST}."
