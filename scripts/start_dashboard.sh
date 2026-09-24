#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")/.." && pwd)"
API_HOST="${POLYDATA_API_HOST:-127.0.0.1}"
API_PORT="${POLYDATA_API_PORT:-18500}"

cd "${ROOT_DIR}"

if [[ -f ".env" ]]; then
  while IFS= read -r line; do
    [[ "${line}" =~ ^[[:space:]]*# ]] && continue
    [[ "${line}" =~ ^[[:space:]]*$ ]] && continue
    if [[ "${line}" =~ ^[[:space:]]*([A-Za-z_][A-Za-z0-9_]*)[[:space:]]*=(.*)$ ]]; then
      key="${BASH_REMATCH[1]}"
      value="${BASH_REMATCH[2]}"
      value="${value%%#*}"
      value="${value#"${value%%[![:space:]]*}"}"
      value="${value%"${value##*[![:space:]]}"}"
      value="${value%\"}"
      value="${value#\"}"
      value="${value%\'}"
      value="${value#\'}"
      export "${key}=${value}"
    fi
  done < ".env"
fi

if [[ -z "${POLYDATA_PYTHON_BIN:-}" && -x "${HOME}/.conda/envs/polyBots/bin/python" ]]; then
  POLYDATA_PYTHON_BIN="${HOME}/.conda/envs/polyBots/bin/python"
fi
POLYDATA_PYTHON_BIN="${POLYDATA_PYTHON_BIN:-python3}"
API_HOST="${POLYDATA_API_HOST:-${API_HOST}}"
API_PORT="${POLYDATA_API_PORT:-${API_PORT}}"

cat <<EOF
Starting polyData API helper.

API: http://${API_HOST}:${API_PORT}
Frontend dev server is not started by this script.
Run it separately with:
  cd ${ROOT_DIR}/webpage
  npm run dev
EOF

exec "${POLYDATA_PYTHON_BIN}" scripts/api_server.py --backend postgres --host "${API_HOST}" --port "${API_PORT}"
