#!/usr/bin/env bash
# Purpose: Probe Android World minimal_task_runner availability in target conda env (diagnostic path).
# Related: third_party/android_world/minimal_task_runner.py, scripts/m1_m2_gate.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
REPORT_DIR="$ROOT_DIR/runs/reports"
mkdir -p "$REPORT_DIR"

CONDA_SH="/root/miniconda3/etc/profile.d/conda.sh"
CONDA_ENV="${CONDA_ENV:-aw311}"
CONSOLE_PORT="${1:-15500}"
TASK_NAME="${TASK_NAME:-ClockStopWatchRunning}"

ts="$(date -u +%Y%m%dT%H%M%SZ)"
log_file="$REPORT_DIR/m2-androidworld-probe-$ts.log"
report_json="$REPORT_DIR/m2-androidworld-probe-$ts.json"

say() { echo "[$(date -u +%H:%M:%S)] $*"; }

json_bool() {
  if [[ "${1:-0}" == "1" ]]; then
    echo "true"
  else
    echo "false"
  fi
}

python_ok=0
module_ok=0
probe_ok=0
blocked_reason=""

if [[ -f "$CONDA_SH" ]]; then
  # shellcheck source=/dev/null
  source "$CONDA_SH"
  if conda activate "$CONDA_ENV" >/dev/null 2>&1; then
    python_ok=1
  fi
fi

if [[ "$python_ok" == "1" ]]; then
  if python - <<'PY' >/dev/null 2>&1
import android_world  # noqa: F401
import android_env  # noqa: F401
PY
  then
    module_ok=1
  else
    blocked_reason="python_modules_missing"
  fi
else
  blocked_reason="conda_env_unavailable"
fi

if [[ "$python_ok" == "1" && "$module_ok" == "1" ]]; then
  say "run android_world minimal_task_runner probe (console_port=$CONSOLE_PORT)"
  set +e
  timeout 180 python "$ROOT_DIR/third_party/android_world/minimal_task_runner.py" \
    --adb_path=/usr/bin/adb \
    --console_port="$CONSOLE_PORT" \
    --task="$TASK_NAME" \
    >"$log_file" 2>&1
  rc=$?
  set -e

  if [[ "$rc" == "0" ]]; then
    probe_ok=1
  else
    if grep -q "Failed to connect to the emulator" "$log_file"; then
      blocked_reason="emulator_grpc_unreachable"
    elif grep -q "adb not found in the common Android SDK paths" "$log_file"; then
      blocked_reason="android_sdk_adb_path_missing"
    elif [[ "$rc" == "124" ]]; then
      blocked_reason="probe_timeout"
    else
      blocked_reason="probe_failed"
    fi
  fi
else
  : >"$log_file"
fi

cat >"$report_json" <<JSON
{
  "ts_utc": "$(date -u +%Y-%m-%dT%H:%M:%SZ)",
  "console_port": $CONSOLE_PORT,
  "task": "$TASK_NAME",
  "conda_env": "$CONDA_ENV",
  "python_env_ok": $(json_bool "$python_ok"),
  "android_world_import_ok": $(json_bool "$module_ok"),
  "probe_ok": $(json_bool "$probe_ok"),
  "blocked_reason": "$blocked_reason",
  "probe_log": "$log_file"
}
JSON

say "M2_ANDROIDWORLD_PROBE_REPORT $report_json"
cat "$report_json"
