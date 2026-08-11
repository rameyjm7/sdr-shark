#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

HOST="${SDR_SHARK_DEMO_HOST:-0.0.0.0}"
REQUESTED_PORT="${SDR_SHARK_DEMO_PORT:-80}"
FALLBACK_PORT="${SDR_SHARK_DEMO_FALLBACK_PORT:-8080}"
SESSION_ID="${SDR_SHARK_DEMO_SESSION_ID:-public-demo-2p4ghz}"
SESSION_ROOT="${SDR_SHARK_IQ_SESSION_DIR:-${REPO_ROOT}/.demo/iq-sessions}"
ACTIVE_FILE="${REPO_ROOT}/.demo/sdr-shark-demo-active.env"
PYTHON_BIN="${PYTHON_BIN:-python3}"

mkdir -p "${REPO_ROOT}/.demo"

if [[ "${HOST}" == "0.0.0.0" || "${HOST}" == "::" ]]; then
  CONTROL_HOST="127.0.0.1"
else
  CONTROL_HOST="${HOST}"
fi

format_url() {
  local host="$1"
  local port="$2"
  if [[ "${port}" == "80" ]]; then
    printf 'http://%s' "${host}"
  else
    printf 'http://%s:%s' "${host}" "${port}"
  fi
}

detect_lan_host() {
  ip -4 route get 1.1.1.1 2>/dev/null | awk '
    {
      for (i = 1; i <= NF; i++) {
        if ($i == "src" && (i + 1) <= NF) {
          print $(i + 1)
          exit
        }
      }
    }
  '
}

can_bind_port() {
  local port="$1"
  "${PYTHON_BIN}" - "$HOST" "$port" <<'PY'
import socket
import sys

host = sys.argv[1]
port = int(sys.argv[2])
with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    sock.bind((host, port))
PY
}

PORT="${REQUESTED_PORT}"
if ! can_bind_port "${PORT}" 2>/dev/null; then
  if [[ "${PORT}" != "${FALLBACK_PORT}" ]] && can_bind_port "${FALLBACK_PORT}" 2>/dev/null; then
    echo "Port ${PORT} is unavailable without elevated permissions; falling back to ${FALLBACK_PORT}."
    PORT="${FALLBACK_PORT}"
  else
    echo "Neither port ${PORT} nor fallback port ${FALLBACK_PORT} is available." >&2
    exit 1
  fi
fi

HOST_ID="${HOST//[^A-Za-z0-9_.-]/_}"
PID_FILE="${REPO_ROOT}/.demo/sdr-shark-demo-${HOST_ID}-${PORT}.pid"
LOG_FILE="${REPO_ROOT}/.demo/sdr-shark-demo-${HOST_ID}-${PORT}.log"

if [[ ! -x "${REPO_ROOT}/.venv/bin/python" ]]; then
  echo "Creating .venv for SDR-Shark demo..."
  "${PYTHON_BIN}" -m venv "${REPO_ROOT}/.venv"
fi

DEMO_PYTHON="${REPO_ROOT}/.venv/bin/python"
"${DEMO_PYTHON}" -m pip install --upgrade pip wheel setuptools >/dev/null
"${DEMO_PYTHON}" -m pip install \
  flask flask-cors gunicorn numpy pandas requests websocket-client >/dev/null
"${DEMO_PYTHON}" -m pip install -e backend --no-deps >/dev/null

if [[ ! -f "${REPO_ROOT}/frontend/build/index.html" ]]; then
  echo "Building SDR-Shark frontend for same-origin demo serving..."
  (
    cd "${REPO_ROOT}/frontend"
    npm install --legacy-peer-deps >/dev/null
    CI=false npm run build >/dev/null
  )
fi

echo "Generating public-safe synthetic IQ session..."
"${DEMO_PYTHON}" scripts/generate_demo_iq_session.py \
  --root "${SESSION_ROOT}" \
  --session-id "${SESSION_ID}" \
  --duration "${SDR_SHARK_DEMO_DURATION:-30}" \
  --sample-rate "${SDR_SHARK_DEMO_SAMPLE_RATE:-2000000}" \
  --center-frequency "${SDR_SHARK_DEMO_CENTER_HZ:-2437000000}" \
  --bandwidth "${SDR_SHARK_DEMO_BANDWIDTH:-2000000}"

if [[ -f "${PID_FILE}" ]]; then
  old_pid="$(cat "${PID_FILE}" || true)"
  if [[ -n "${old_pid}" ]] && kill -0 "${old_pid}" 2>/dev/null; then
    echo "Demo backend already running with PID ${old_pid}."
  else
    rm -f "${PID_FILE}"
  fi
fi

if [[ ! -f "${PID_FILE}" ]]; then
  echo "Starting SDR-Shark demo backend on http://${HOST}:${PORT} ..."
  launch_env=(
    "SDR_BACKEND=${SDR_BACKEND:-gateway}"
    "SDR_SHARK_IQ_SESSION_DIR=${SESSION_ROOT}"
    "SDR_SHARK_HOST=${HOST}"
    "SDR_SHARK_PORT=${PORT}"
    "SDR_SHARK_REGISTER_WITH_PORTAL=0"
    "SDR_SHARK_FRONTEND_PORT=${PORT}"
    "SDR_SHARK_AUTO_START_FRONTEND=${SDR_SHARK_AUTO_START_FRONTEND:-0}"
    "SDR_SHARK_WORKER_SDR=${SDR_SHARK_WORKER_SDR:-0}"
    "SDR_SHARK_GUNICORN=${REPO_ROOT}/.venv/bin/gunicorn"
  )
  nohup setsid env "${launch_env[@]}" scripts/start.sh >"${LOG_FILE}" 2>&1 &
  echo "$!" > "${PID_FILE}"
fi

{
  printf 'SDR_SHARK_DEMO_HOST=%q\n' "${HOST}"
  printf 'SDR_SHARK_DEMO_PORT=%q\n' "${PORT}"
} > "${ACTIVE_FILE}"

for _ in $(seq 1 80); do
  if curl -fsS "$(format_url "${CONTROL_HOST}" "${PORT}")/api/iq/sessions" >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done

if ! curl -fsS "$(format_url "${CONTROL_HOST}" "${PORT}")/api/iq/sessions" >/dev/null 2>&1; then
  echo "SDR-Shark demo backend did not become ready. Recent log:" >&2
  tail -n 80 "${LOG_FILE}" >&2 || true
  exit 1
fi

echo "Starting IQ replay session ${SESSION_ID}..."
curl -fsS \
  -H "Content-Type: application/json" \
  -d "{\"id\":\"${SESSION_ID}\",\"loop\":true,\"speed\":1}" \
  "$(format_url "${CONTROL_HOST}" "${PORT}")/api/iq/replay/start" >/dev/null

echo
LAN_HOST="${SDR_SHARK_DEMO_LAN_HOST:-$(detect_lan_host)}"
echo "SDR-Shark synthetic demo is running:"
echo "  Local UI:  $(format_url "${CONTROL_HOST}" "${PORT}")"
if [[ -n "${LAN_HOST}" && "${HOST}" == "0.0.0.0" ]]; then
  echo "  LAN UI:    $(format_url "${LAN_HOST}" "${PORT}")"
fi
echo "  Status:    $(format_url "${CONTROL_HOST}" "${PORT}")/api/iq/replay/status"
echo "  Log:    ${LOG_FILE}"
echo
echo "Stop it with:"
if [[ "${HOST}" == "0.0.0.0" && "${PORT}" == "80" ]]; then
  echo "  ./scripts/demo_stop.sh"
else
  echo "  SDR_SHARK_DEMO_HOST=${HOST} SDR_SHARK_DEMO_PORT=${PORT} ./scripts/demo_stop.sh"
fi
