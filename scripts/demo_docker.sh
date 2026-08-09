#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

HOST_PORT="${SDR_SHARK_DEMO_PORT:-8080}"

if ss -ltn "sport = :${HOST_PORT}" | grep -q ":${HOST_PORT}"; then
  if [[ -f "${REPO_ROOT}/.demo/sdr-shark-demo-active.env" ]]; then
    echo "Stopping local script demo before starting Docker demo..."
    ./scripts/demo_stop.sh || true
  fi
fi

docker compose -f docker-compose.demo.yml up --build
