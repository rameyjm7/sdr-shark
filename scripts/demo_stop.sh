#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
HOST="${SDR_SHARK_DEMO_HOST:-127.0.0.1}"
PORT="${SDR_SHARK_DEMO_PORT:-5000}"
HOST_ID="${HOST//[^A-Za-z0-9_.-]/_}"
PID_FILE="${REPO_ROOT}/.demo/sdr-shark-demo-${HOST_ID}-${PORT}.pid"
curl -fsS -X POST "http://${HOST}:${PORT}/api/iq/replay/stop" >/dev/null 2>&1 || true

if [[ -f "${PID_FILE}" ]]; then
  pid="$(cat "${PID_FILE}" || true)"
  if [[ "${pid}" =~ ^[0-9]+$ ]] && kill -0 "${pid}" 2>/dev/null; then
    kill -- "-${pid}" 2>/dev/null || kill "${pid}" 2>/dev/null || true
    for _ in $(seq 1 30); do
      if ! kill -0 "${pid}" 2>/dev/null; then
        break
      fi
      sleep 0.2
    done
    if kill -0 "${pid}" 2>/dev/null; then
      kill -9 -- "-${pid}" 2>/dev/null || kill -9 "${pid}" 2>/dev/null || true
    fi
    echo "Stopped SDR-Shark demo backend PID ${pid}."
  fi
  rm -f "${PID_FILE}"
else
  echo "No SDR-Shark demo PID file found."
fi
