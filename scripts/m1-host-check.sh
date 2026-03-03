#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
"$ROOT_DIR/scripts/ensure_docker.sh"

ok=true

say() { echo "[$(date -u +%H:%M:%S)] $*"; }

if command -v docker >/dev/null 2>&1; then
  say "PASS docker binary"
else
  say "FAIL docker missing"
  ok=false
fi

if docker info >/dev/null 2>&1; then
  say "PASS docker daemon"
else
  say "FAIL docker daemon unreachable"
  ok=false
fi

if command -v adb >/dev/null 2>&1; then
  say "PASS adb binary"
else
  say "FAIL adb missing"
  ok=false
fi

if [[ -e /dev/kvm ]]; then
  say "PASS /dev/kvm present"
else
  say "WARN /dev/kvm missing"
fi

if [[ -e /dev/binder || -e /dev/binderfs/binder ]]; then
  say "PASS binder device present"
else
  say "FAIL binder device missing (redroid likely cannot boot)"
  ok=false
fi

if [[ "$ok" == true ]]; then
  say "M1_HOST_CHECK=PASS"
  exit 0
fi
say "M1_HOST_CHECK=FAIL"
exit 1
