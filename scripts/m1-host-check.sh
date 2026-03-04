#!/usr/bin/env bash
# Purpose: M1 host capability check (docker/adb/binder/kvm) with explicit binder-vs-kvm guard.
# Related: scripts/smoke.sh, docs/milestones/M1-single-instance.md
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

if [[ -e /dev/binder ]]; then
  dev_hex="$(stat -c '%t:%T' /dev/binder 2>/dev/null || true)"
  major_hex="${dev_hex%%:*}"
  minor_hex="${dev_hex##*:}"
  major_dec="$((16#${major_hex:-0}))"
  minor_dec="$((16#${minor_hex:-0}))"
  binder_major="$(awk '$2==\"binder\"{print $1; exit}' /proc/devices 2>/dev/null || true)"
  kvm_minor="$(awk '$2==\"kvm\"{print $1; exit}' /proc/misc 2>/dev/null || true)"

  if [[ -n "$binder_major" && "$major_dec" != "$binder_major" ]]; then
    if [[ "$major_dec" == "10" && -n "$kvm_minor" && "$minor_dec" == "$kvm_minor" ]]; then
      say "FAIL /dev/binder is actually kvm (major=$major_dec minor=$minor_dec)"
    else
      say "FAIL /dev/binder major mismatch major=$major_dec binder_major=$binder_major"
    fi
    ok=false
  fi
fi

if [[ "$ok" == true ]]; then
  say "M1_HOST_CHECK=PASS"
  exit 0
fi
say "M1_HOST_CHECK=FAIL"
exit 1
