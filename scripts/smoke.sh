#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/lib_config.sh"
"$ROOT_DIR/scripts/ensure_docker.sh"

adb_base="$(cfg_adb_base_port)"
serial="127.0.0.1:$adb_base"

if ! command -v adb >/dev/null 2>&1; then
  echo "adb not installed" >&2
  exit 1
fi

# container existence check
if ! docker ps --format '{{.Names}}' | grep -qx 'redroid-0'; then
  echo "SMOKE_FAIL redroid-0 is not running" >&2
  docker ps -a --filter name=redroid-0 --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' >&2 || true
  echo "Hint: in real hosts this usually means binder device is missing or container bootstrap failed." >&2
  exit 1
fi

adb connect "$serial" >/tmp/adb-connect.out 2>/tmp/adb-connect.err || true

echo "Waiting for device $serial"
for _ in $(seq 1 40); do
  state="$(adb -s "$serial" get-state 2>/dev/null || true)"
  if [[ "$state" == "device" ]]; then
    break
  fi
  sleep 1
done

state="$(adb -s "$serial" get-state 2>/dev/null || true)"
if [[ "$state" != "device" ]]; then
  echo "SMOKE_FAIL adb state=$state" >&2
  adb devices >&2 || true
  docker ps -a --filter name=redroid-0 --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' >&2 || true
  docker logs --tail 80 redroid-0 >&2 || true
  exit 1
fi

adb -s "$serial" shell getprop ro.build.version.release >/tmp/redroid-release.txt 2>/dev/null || true
adb -s "$serial" shell input keyevent 3 >/dev/null 2>&1 || true
adb -s "$serial" shell wm size >/tmp/redroid-wm-size.txt 2>/dev/null || true

echo "SMOKE_PASS serial=$serial android_release=$(tr -d '\r' </tmp/redroid-release.txt | head -n1)"
