#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "${REPO_ROOT}"

# Optional token loading for sdr-gateway auth.
# Priority:
# 1) pre-exported SDR_GATEWAY_API_TOKEN
# 2) /etc/default/sdr-gateway
# 3) SDR_SHARK_GATEWAY_TOKEN_FILE
# 4) ../sdr-gateway/configs/key.txt
# 5) ../sdr-gateway/key.txt
if [[ -z "${SDR_GATEWAY_API_TOKEN:-}" ]]; then
  TOKEN_FILE="${SDR_SHARK_GATEWAY_TOKEN_FILE:-}"
  if [[ -z "${TOKEN_FILE}" && -r "/etc/default/sdr-gateway" ]]; then
    TOKEN_FILE="/etc/default/sdr-gateway"
  fi
  if [[ -z "${TOKEN_FILE}" ]]; then
    for candidate in \
      "${REPO_ROOT}/../sdr-gateway/configs/key.txt" \
      "${REPO_ROOT}/../sdr-gateway/key.txt"
    do
      if [[ -r "${candidate}" ]]; then
        TOKEN_FILE="${candidate}"
        break
      fi
    done
  fi

  if [[ -n "${TOKEN_FILE}" && -r "${TOKEN_FILE}" ]]; then
    token_line="$(grep -E '^[[:space:]]*SDR_GATEWAY_API_TOKEN[[:space:]]*=' "${TOKEN_FILE}" | tail -n1 || true)"
    if [[ -n "${token_line}" ]]; then
      token_value="${token_line#*=}"
    else
      token_value="$(cat "${TOKEN_FILE}")"
    fi
    token_value="$(printf '%s' "${token_value}" | tr -d '\r' | tr -d '\n' | sed -e 's/^[[:space:]]*//' -e 's/[[:space:]]*$//' -e 's/^\"//' -e 's/\"$//' -e "s/^'//" -e "s/'$//")"
    if [[ -n "${token_value}" ]]; then
      export SDR_GATEWAY_API_TOKEN="${token_value}"
      echo "Loaded SDR_GATEWAY_API_TOKEN from ${TOKEN_FILE}"
    fi
  fi
fi

if [[ -z "${SDR_GATEWAY_API_TOKEN:-}" ]]; then
  echo "Warning: SDR_GATEWAY_API_TOKEN is not set; backend may fail against authenticated sdr-gateway."
fi

exec gunicorn -w 1 --threads 10 -b 0.0.0.0:5000 sdr_plot_backend.__main__:app
