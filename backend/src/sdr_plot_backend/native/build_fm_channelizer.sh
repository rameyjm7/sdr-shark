#!/usr/bin/env bash
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cc -O3 -fPIC -shared \
  "${HERE}/fm_channelizer_liquid.c" \
  -o "${HERE}/libfm_channelizer_liquid.so" \
  -lliquid -lm
