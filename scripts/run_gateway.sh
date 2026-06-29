#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

export SDR_BACKEND=gateway
export SDR_GATEWAY_IQ_FORMAT="${SDR_GATEWAY_IQ_FORMAT:-native}"

exec "${SCRIPT_DIR}/start.sh"
