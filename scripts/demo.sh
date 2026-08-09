#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

HOST="${SDR_SHARK_DEMO_HOST:-127.0.0.1}"
PORT="${SDR_SHARK_DEMO_PORT:-5000}"
SESSION_ID="${SDR_SHARK_DEMO_SESSION_ID:-public-demo-2p4ghz}"
SESSION_ROOT="${SDR_SHARK_IQ_SESSION_DIR:-${REPO_ROOT}/.demo/iq-sessions}"
HOST_ID="${HOST//[^A-Za-z0-9_.-]/_}"
PID_FILE="${REPO_ROOT}/.demo/sdr-shark-demo-${HOST_ID}-${PORT}.pid"
LOG_FILE="${REPO_ROOT}/.demo/sdr-shark-demo-${HOST_ID}-${PORT}.log"
PYTHON_BIN="${PYTHON_BIN:-python3}"

mkdir -p "${REPO_ROOT}/.demo"

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
  nohup bash -c '
    log_file="$1"
    pid_file="$2"
    shift 2
    setsid env "$@" scripts/start.sh >"${log_file}" 2>&1 &
    echo "$!" > "${pid_file}"
  ' sdr-shark-demo "${LOG_FILE}" "${PID_FILE}" "${launch_env[@]}" >/dev/null 2>&1 &
  for _ in $(seq 1 40); do
    [[ -f "${PID_FILE}" ]] && break
    sleep 0.05
  done
fi

for _ in $(seq 1 80); do
  if curl -fsS "http://${HOST}:${PORT}/api/iq/sessions" >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done

if ! curl -fsS "http://${HOST}:${PORT}/api/iq/sessions" >/dev/null 2>&1; then
  echo "SDR-Shark demo backend did not become ready. Recent log:" >&2
  tail -n 80 "${LOG_FILE}" >&2 || true
  exit 1
fi

echo "Starting IQ replay session ${SESSION_ID}..."
curl -fsS \
  -H "Content-Type: application/json" \
  -d "{\"id\":\"${SESSION_ID}\",\"loop\":true,\"speed\":1}" \
  "http://${HOST}:${PORT}/api/iq/replay/start" >/dev/null

echo
echo "SDR-Shark synthetic demo is running:"
echo "  UI:     http://${HOST}:${PORT}"
echo "  Status: http://${HOST}:${PORT}/api/iq/replay/status"
echo "  Log:    ${LOG_FILE}"
echo
echo "Stop it with:"
if [[ "${HOST}" == "127.0.0.1" && "${PORT}" == "5000" ]]; then
  echo "  ./scripts/demo_stop.sh"
else
  echo "  SDR_SHARK_DEMO_HOST=${HOST} SDR_SHARK_DEMO_PORT=${PORT} ./scripts/demo_stop.sh"
fi
