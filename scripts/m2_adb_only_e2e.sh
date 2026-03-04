#!/usr/bin/env bash
# Purpose: Run M2 adb-only end-to-end probe via orchestrator.worker and write a structured report.
# Related: orchestrator/worker.py, scripts/m1_m2_gate.sh, docs/milestones/M2-androidworld-e2e.md
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPORT_DIR="$ROOT_DIR/runs/reports"
mkdir -p "$REPORT_DIR"

serial="${1:-127.0.0.1:15500}"
ts="$(date -u +%Y%m%dT%H%M%SZ)"
run_id="run-adb-only-$ts"
log_file="$REPORT_DIR/m2-adb-only-$ts.log"
report_json="$REPORT_DIR/m2-adb-only-$ts.json"

say() { echo "[$(date -u +%H:%M:%S)] $*"; }

json_bool() {
  if [[ "${1:-0}" == "1" ]]; then
    echo "true"
  else
    echo "false"
  fi
}

say "run adb-only m2 e2e serial=$serial run_id=$run_id"
set +e
python3 -m orchestrator.worker --adb-only-e2e --serial "$serial" --run-id "$run_id" >"$log_file" 2>&1
rc=$?
set -e

if [[ "$rc" == "0" ]]; then
  ok=1
  blocked_reason=""
else
  ok=0
  if grep -q "adb serial not ready" "$log_file"; then
    blocked_reason="adb_not_ready"
  else
    blocked_reason="e2e_failed"
  fi
fi

trace_file="$ROOT_DIR/runs/logs/m2-trace/$run_id/task-adb-only-$ts-attempt-1.json"
result_file="$ROOT_DIR/runs/results/$run_id.jsonl"

cat >"$report_json" <<JSON
{
  "ts_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "serial": "$serial",
  "run_id": "$run_id",
  "accepted": $(json_bool "$ok"),
  "blocked_reason": "$blocked_reason",
  "log_file": "$log_file",
  "result_file": "$result_file",
  "artifact_dir": "$ROOT_DIR/runs/logs/m2-adb-only/$run_id"
}
JSON

say "M2_ADB_ONLY_REPORT $report_json"
cat "$report_json"
exit "$rc"
