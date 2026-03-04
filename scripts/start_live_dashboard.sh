#!/usr/bin/env bash
# Purpose: Start live dashboard service in background with PID/log management.
# Related: tools/live_dashboard.py, scripts/deploy_live_dashboard.sh, docs/live_dashboard_deploy.md
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
RUN_DIR="$ROOT_DIR/runs/live_dashboard"
LOG_DIR="$ROOT_DIR/runs/logs"
mkdir -p "$RUN_DIR" "$LOG_DIR"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-18080}"
ROWS="${ROWS:-2}"
COLS="${COLS:-4}"
CAPTURE_INTERVAL="${CAPTURE_INTERVAL:-2.0}"
ADB_BIN="${ADB_BIN:-adb}"

PID_FILE="$RUN_DIR/live_dashboard.pid"
LOG_FILE="$LOG_DIR/live_dashboard.log"

if [[ -f "$PID_FILE" ]]; then
  old_pid="$(cat "$PID_FILE" 2>/dev/null || true)"
  if [[ -n "$old_pid" ]] && kill -0 "$old_pid" 2>/dev/null; then
    echo "live_dashboard already running, pid=$old_pid"
    echo "log=$LOG_FILE"
    echo "url=http://$HOST:$PORT"
    exit 0
  fi
fi

if ! python3 -c "import PIL" >/dev/null 2>&1; then
  echo "ERROR: missing python package Pillow (PIL)"
  echo "Install with: python3 -m pip install --user pillow"
  exit 1
fi

nohup python3 "$ROOT_DIR/tools/live_dashboard.py" \
  --host "$HOST" \
  --port "$PORT" \
  --rows "$ROWS" \
  --cols "$COLS" \
  --capture-interval "$CAPTURE_INTERVAL" \
  --data-dir "$RUN_DIR" \
  --adb-bin "$ADB_BIN" \
  >"$LOG_FILE" 2>&1 &

pid=$!
echo "$pid" > "$PID_FILE"

sleep 1
if ! kill -0 "$pid" 2>/dev/null; then
  echo "ERROR: live_dashboard exited immediately"
  echo "LOG=$LOG_FILE"
  exit 1
fi

echo "LIVE_DASHBOARD_STARTED pid=$pid"
echo "URL=http://$HOST:$PORT"
echo "LOG=$LOG_FILE"
echo "PID_FILE=$PID_FILE"
