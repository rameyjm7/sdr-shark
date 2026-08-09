#!/usr/bin/env bash
set -euo pipefail

cd /app

HOST="${SDR_SHARK_HOST:-0.0.0.0}"
PORT="${SDR_SHARK_PORT:-8080}"
CONTROL_HOST="127.0.0.1"
SESSION_ID="${SDR_SHARK_DEMO_SESSION_ID:-public-demo-2p4ghz}"
SESSION_ROOT="${SDR_SHARK_IQ_SESSION_DIR:-/app/.demo/iq-sessions}"

mkdir -p "${SESSION_ROOT}"

echo "Generating public-safe synthetic IQ session ${SESSION_ID}..."
python scripts/generate_demo_iq_session.py \
  --root "${SESSION_ROOT}" \
  --session-id "${SESSION_ID}" \
  --duration "${SDR_SHARK_DEMO_DURATION:-30}" \
  --sample-rate "${SDR_SHARK_DEMO_SAMPLE_RATE:-2000000}" \
  --center-frequency "${SDR_SHARK_DEMO_CENTER_HZ:-2437000000}" \
  --bandwidth "${SDR_SHARK_DEMO_BANDWIDTH:-2000000}"

export SDR_BACKEND="${SDR_BACKEND:-gateway}"
export SDR_SHARK_IQ_SESSION_DIR="${SESSION_ROOT}"
export SDR_SHARK_AUTO_START_FRONTEND="${SDR_SHARK_AUTO_START_FRONTEND:-0}"
export SDR_SHARK_WORKER_SDR="${SDR_SHARK_WORKER_SDR:-0}"

echo "Starting SDR-Shark demo backend on ${HOST}:${PORT}..."
gunicorn \
  -w "${SDR_SHARK_WORKERS:-1}" \
  --threads "${SDR_SHARK_THREADS:-10}" \
  -b "${HOST}:${PORT}" \
  sdr_plot_backend.__main__:app &
server_pid="$!"

cleanup() {
  curl -fsS -X POST "http://${CONTROL_HOST}:${PORT}/api/iq/replay/stop" >/dev/null 2>&1 || true
  kill "${server_pid}" 2>/dev/null || true
}
trap cleanup INT TERM

for _ in $(seq 1 100); do
  if curl -fsS "http://${CONTROL_HOST}:${PORT}/api/iq/sessions" >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done

if ! curl -fsS "http://${CONTROL_HOST}:${PORT}/api/iq/sessions" >/dev/null 2>&1; then
  echo "SDR-Shark demo backend did not become ready." >&2
  kill "${server_pid}" 2>/dev/null || true
  wait "${server_pid}" 2>/dev/null || true
  exit 1
fi

echo "Starting IQ replay session ${SESSION_ID}..."
curl -fsS \
  -H "Content-Type: application/json" \
  -d "{\"id\":\"${SESSION_ID}\",\"loop\":true,\"speed\":1}" \
  "http://${CONTROL_HOST}:${PORT}/api/iq/replay/start" >/dev/null

echo "SDR-Shark Docker demo is running on port ${PORT}."
wait "${server_pid}"
