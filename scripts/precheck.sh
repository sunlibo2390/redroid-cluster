#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPORT_DIR="$ROOT_DIR/runs/reports"
mkdir -p "$REPORT_DIR"

ts="$(date -u +%Y%m%dT%H%M%SZ)"
report="$REPORT_DIR/precheck-$ts.txt"

ok=true
strict_docker="${PRECHECK_STRICT_DOCKER:-0}"

log() { echo "[$(date -u +%H:%M:%S)] $*" | tee -a "$report"; }
check_cmd() {
  local cmd="$1"
  if command -v "$cmd" >/dev/null 2>&1; then
    log "PASS command: $cmd"
  else
    log "FAIL command missing: $cmd"
    ok=false
  fi
}

log "M0 precheck started"
log "host=$(hostname)"
log "kernel=$(uname -r)"

# Ensure docker daemon is running in this environment (best effort).
if command -v docker >/dev/null 2>&1; then
  if "$ROOT_DIR/scripts/ensure_docker.sh" >/dev/null 2>&1; then
    log "PASS docker daemon ensured"
  else
    log "WARN docker daemon ensure failed"
  fi
fi

if command -v docker >/dev/null 2>&1; then
  log "PASS command: docker"
else
  if [[ "$strict_docker" == "1" ]]; then
    log "FAIL command missing: docker (strict mode)"
    ok=false
  else
    log "WARN command missing: docker (non-strict mode)"
  fi
fi
check_cmd awk
check_cmd sed
check_cmd ss

if command -v docker >/dev/null 2>&1; then
  if docker info >/dev/null 2>&1; then
    log "PASS docker daemon reachable"
  else
    log "FAIL docker daemon not reachable"
    ok=false
  fi
fi

# Disk check for workspace mount
avail_gb="$(df -BG "$ROOT_DIR" | awk 'NR==2{gsub("G", "", $4); print $4}')"
log "disk_available_gb=$avail_gb"
if [[ "${avail_gb:-0}" -lt 20 ]]; then
  log "WARN low disk space (<20GB)"
fi

# Port spot check
for p in 15500 15501 15502 15503; do
  if ss -ltn | awk '{print $4}' | grep -q ":$p$"; then
    log "WARN port_in_use=$p"
  else
    log "PASS port_free=$p"
  fi
done

if [[ "$ok" == true ]]; then
  log "PRECHECK_RESULT=PASS"
  exit 0
else
  log "PRECHECK_RESULT=FAIL"
  exit 1
fi
