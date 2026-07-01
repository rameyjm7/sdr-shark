#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

WITH_SYSTEM_PACKAGES="${WITH_SYSTEM_PACKAGES:-1}"
WITH_FRONTEND_BUILD="${WITH_FRONTEND_BUILD:-1}"
WITH_SERVICE="${WITH_SERVICE:-0}"
ENABLE_SERVICE="${ENABLE_SERVICE:-0}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
VENV_DIR="${VENV_DIR:-${REPO_ROOT}/.venv}"

usage() {
  cat <<EOF
Usage: $(basename "$0") [options]

Options:
  --no-system-packages   Skip apt package installation
  --no-frontend-build    Skip production frontend build
  --service              Install/refresh the sdr-shark systemd service
  --enable-service       Install, enable, and start the systemd service
  -h, --help             Show this help

Environment:
  VENV_DIR               Python virtualenv path (default: <repo>/.venv)
  SDR_BACKEND            Backend mode written by service helper when used
  SDR_SERVER_URL         Gateway URL for SDR_BACKEND=gateway
  SDR_GATEWAY_API_TOKEN  Gateway token for SDR_BACKEND=gateway
EOF
}

while [[ $# -gt 0 ]]; do
  case "$1" in
    --no-system-packages) WITH_SYSTEM_PACKAGES=0 ;;
    --no-frontend-build) WITH_FRONTEND_BUILD=0 ;;
    --service) WITH_SERVICE=1 ;;
    --enable-service) WITH_SERVICE=1; ENABLE_SERVICE=1 ;;
    -h|--help) usage; exit 0 ;;
    *)
      echo "Unknown option: $1" >&2
      usage
      exit 1
      ;;
  esac
  shift
done

run_root() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

if [[ "${WITH_SYSTEM_PACKAGES}" == "1" ]]; then
  if command -v apt-get >/dev/null 2>&1; then
    run_root apt-get update
    run_root apt-get install -y \
      build-essential \
      cargo \
      curl \
      git \
      gpsd \
      gpsd-clients \
      gunicorn \
      libliquid-dev \
      npm \
      python3-dev \
      python3-pip \
      python3-venv \
      soapysdr-tools \
      sox \
      tshark \
      wireshark-common
  else
    echo "apt-get not found; skipping system package installation." >&2
  fi
fi

"${PYTHON_BIN}" -m venv "${VENV_DIR}"
# shellcheck disable=SC1091
source "${VENV_DIR}/bin/activate"
python -m pip install --upgrade pip wheel setuptools
python -m pip install -r requirements.txt
python -m pip install -e backend

if [[ -f "backend/src/sdr_plot_backend/native/build_fm_channelizer.sh" ]]; then
  bash backend/src/sdr_plot_backend/native/build_fm_channelizer.sh || true
fi

if [[ -d "frontend" ]]; then
  pushd frontend >/dev/null
  if command -v yarn >/dev/null 2>&1; then
    yarn install
    if [[ "${WITH_FRONTEND_BUILD}" == "1" ]]; then
      yarn build
    fi
  else
    npm install
    if [[ "${WITH_FRONTEND_BUILD}" == "1" ]]; then
      npm run build
    fi
  fi
  popd >/dev/null
fi

chmod +x scripts/start.sh scripts/sdr-shark-service.sh scripts/run_gateway.sh

if [[ "${WITH_SERVICE}" == "1" ]]; then
  SDR_SHARK_USER="${SDR_SHARK_USER:-$(id -un)}" \
  SDR_SHARK_GROUP="${SDR_SHARK_GROUP:-$(id -gn)}" \
    ./scripts/sdr-shark-service.sh install
fi

if [[ "${ENABLE_SERVICE}" == "1" ]]; then
  ./scripts/sdr-shark-service.sh enable
  ./scripts/sdr-shark-service.sh restart
fi

cat <<EOF

SDR-Shark installation complete.

Run locally:
  source "${VENV_DIR}/bin/activate"
  ./scripts/start.sh

Service helper:
  ./scripts/sdr-shark-service.sh status
  ./scripts/sdr-shark-service.sh logs
EOF
