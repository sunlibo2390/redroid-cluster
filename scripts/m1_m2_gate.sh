#!/usr/bin/env bash
# Purpose: Aggregate M1+M2 acceptance checks and emit a gate JSON report.
# Related: scripts/m1-host-check.sh, scripts/smoke.sh, scripts/m2_adb_only_e2e.sh, orchestrator/worker.py
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPORT_DIR="$ROOT_DIR/runs/reports"
mkdir -p "$REPORT_DIR"

ts="$(date -u +%Y%m%dT%H%M%SZ)"
report_json="$REPORT_DIR/m1-m2-gate-$ts.json"
m1_log="$REPORT_DIR/m1-smoke-$ts.log"
m2_log="$REPORT_DIR/m2-self-test-$ts.log"
m2_probe_log="$REPORT_DIR/m2-adb-only-$ts.log"

say() { echo "[$(date -u +%H:%M:%S)] $*"; }

json_bool() {
  if [[ "${1:-0}" == "1" ]]; then
    echo "true"
  else
    echo "false"
  fi
}

say "run m1 host check"
if bash "$ROOT_DIR/scripts/m1-host-check.sh" >/dev/null 2>&1; then
  m1_host_ok=1
else
  m1_host_ok=0
fi

say "run m1 smoke(single instance)"
if bash "$ROOT_DIR/scripts/smoke.sh" >"$m1_log" 2>&1; then
  m1_smoke_ok=1
else
  m1_smoke_ok=0
fi

say "run m2 self-test"
if python3 -m orchestrator.worker --self-test >"$m2_log" 2>&1; then
  m2_self_test_ok=1
else
  m2_self_test_ok=0
fi

say "run m2 adb-only e2e"
if bash "$ROOT_DIR/scripts/m2_adb_only_e2e.sh" >"$m2_probe_log" 2>&1; then
  m2_probe_cmd_ok=1
else
  m2_probe_cmd_ok=0
fi

m2_probe_ok=0
m2_blocked_reason=""
m2_probe_json="$(grep -Eo '/remote-home1/lbsun/redroid-cluster/runs/reports/m2-adb-only-[0-9TZ]+\.json' "$m2_probe_log" | tail -n1 || true)"
if [[ -n "${m2_probe_json:-}" && -f "$m2_probe_json" ]]; then
  if grep -q '"accepted": true' "$m2_probe_json"; then
    m2_probe_ok=1
  else
    m2_blocked_reason="$(python3 - <<PY
import json
with open("$m2_probe_json", "r", encoding="utf-8") as f:
    data = json.load(f)
print(data.get("blocked_reason", ""))
PY
)"
  fi
fi

m1_pass=0
if [[ "$m1_host_ok" == "1" && "$m1_smoke_ok" == "1" ]]; then
  m1_pass=1
fi

m2_pass=0
if [[ "$m2_self_test_ok" == "1" && "$m2_probe_ok" == "1" ]]; then
  m2_pass=1
fi

cat >"$report_json" <<JSON
{
  "ts_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "m1": {
    "host_check_pass": $(json_bool "$m1_host_ok"),
    "smoke_pass": $(json_bool "$m1_smoke_ok"),
    "accepted": $(json_bool "$m1_pass"),
    "smoke_log": "$m1_log"
  },
  "m2": {
    "self_test_pass": $(json_bool "$m2_self_test_ok"),
    "adb_only_e2e_ok": $(json_bool "$m2_probe_ok"),
    "adb_only_e2e_cmd_ok": $(json_bool "$m2_probe_cmd_ok"),
    "blocked_reason": "$m2_blocked_reason",
    "accepted": $(json_bool "$m2_pass"),
    "self_test_log": "$m2_log",
    "probe_log": "$m2_probe_log",
    "note": "M2 runs on adb-only backend to avoid emulator gRPC dependency."
  }
}
JSON

say "GATE_REPORT $report_json"
cat "$report_json"
