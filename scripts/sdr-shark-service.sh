#!/usr/bin/env bash
set -euo pipefail

SERVICE_NAME="${SERVICE_NAME:-sdr-shark}"
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
UNIT_PATH="/etc/systemd/system/${SERVICE_NAME}.service"

RUN_USER="${SDR_SHARK_USER:-$(id -un)}"
RUN_GROUP="${SDR_SHARK_GROUP:-$(id -gn)}"
ENV_FILE="${SDR_SHARK_ENV_FILE:-/etc/default/${SERVICE_NAME}}"
START_SCRIPT="${SDR_SHARK_START_SCRIPT:-${REPO_ROOT}/scripts/start.sh}"

usage() {
  cat <<EOF
Usage: $(basename "$0") <command>

Commands:
  install    Install/refresh ${SERVICE_NAME}.service
  enable     Enable service at boot
  disable    Disable service at boot
  start      Start service
  stop       Stop service
  restart    Restart service
  status     Show service status
  logs       Tail service logs
  uninstall  Stop, disable, and remove service unit

Environment overrides:
  SERVICE_NAME            (default: sdr-shark)
  SDR_SHARK_USER          (default: current user)
  SDR_SHARK_GROUP         (default: current user's group)
  SDR_SHARK_ENV_FILE      (default: /etc/default/<service>)
  SDR_SHARK_START_SCRIPT  (default: <repo>/scripts/start.sh)
  SDR_GATEWAY_API_TOKEN   (optional; written to env file on install)
EOF
}

run_root() {
  if [[ "${EUID}" -eq 0 ]]; then
    "$@"
  else
    sudo "$@"
  fi
}

require_systemd() {
  if ! command -v systemctl >/dev/null 2>&1; then
    echo "systemctl not found. This script requires systemd." >&2
    exit 1
  fi
}

install_unit() {
  require_systemd

  if [[ ! -x "${START_SCRIPT}" ]]; then
    echo "Start script not found/executable at: ${START_SCRIPT}" >&2
    echo "Run: chmod +x ${REPO_ROOT}/scripts/start.sh" >&2
    exit 1
  fi

  cat >"/tmp/${SERVICE_NAME}.service" <<EOF
[Unit]
Description=SDR Shark Service
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=${RUN_USER}
Group=${RUN_GROUP}
WorkingDirectory=${REPO_ROOT}
Environment=PYTHONUNBUFFERED=1
EnvironmentFile=-${ENV_FILE}
ExecStart=${START_SCRIPT}
Restart=on-failure
RestartSec=2
TimeoutStopSec=10
LimitNOFILE=65535

[Install]
WantedBy=multi-user.target
EOF

  run_root install -m 0644 "/tmp/${SERVICE_NAME}.service" "${UNIT_PATH}"
  rm -f "/tmp/${SERVICE_NAME}.service"

  if [[ -n "${SDR_GATEWAY_API_TOKEN:-}" ]]; then
    token_escaped="$(printf "%s" "${SDR_GATEWAY_API_TOKEN}" | sed "s/'/'\"'\"'/g")"
    run_root sh -c "cat > '${ENV_FILE}' <<'EOF'
SDR_GATEWAY_API_TOKEN='${token_escaped}'
EOF"
    run_root chmod 0600 "${ENV_FILE}"
    echo "Wrote token to ${ENV_FILE}"
  else
    echo "Note: SDR_GATEWAY_API_TOKEN not set. Service relies on scripts/start.sh token discovery."
  fi

  run_root systemctl daemon-reload
  echo "Installed ${UNIT_PATH}"
}

enable_unit() {
  require_systemd
  run_root systemctl enable "${SERVICE_NAME}"
}

disable_unit() {
  require_systemd
  run_root systemctl disable "${SERVICE_NAME}"
}

start_unit() {
  require_systemd
  run_root systemctl start "${SERVICE_NAME}"
}

stop_unit() {
  require_systemd
  run_root systemctl stop "${SERVICE_NAME}"
}

restart_unit() {
  require_systemd
  run_root systemctl restart "${SERVICE_NAME}"
}

status_unit() {
  require_systemd
  run_root systemctl status "${SERVICE_NAME}" --no-pager
}

logs_unit() {
  require_systemd
  run_root journalctl -u "${SERVICE_NAME}" -f
}

uninstall_unit() {
  require_systemd
  run_root systemctl stop "${SERVICE_NAME}" || true
  run_root systemctl disable "${SERVICE_NAME}" || true
  run_root rm -f "${UNIT_PATH}"
  run_root systemctl daemon-reload
  echo "Removed ${UNIT_PATH}"
}

if [[ $# -lt 1 ]]; then
  usage
  exit 1
fi

case "$1" in
  install) install_unit ;;
  enable) enable_unit ;;
  disable) disable_unit ;;
  start) start_unit ;;
  stop) stop_unit ;;
  restart) restart_unit ;;
  status) status_unit ;;
  logs) logs_unit ;;
  uninstall) uninstall_unit ;;
  -h|--help|help) usage ;;
  *)
    echo "Unknown command: $1" >&2
    usage
    exit 1
    ;;
esac
