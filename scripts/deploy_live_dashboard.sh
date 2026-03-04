#!/usr/bin/env bash
# Purpose: One-shot deploy/start helper for live dashboard (dependency check + startup + listen verification).
# Related: scripts/start_live_dashboard.sh, tools/live_dashboard.py, docs/live_dashboard_deploy.md
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-18080}"
ROWS="${ROWS:-2}"
COLS="${COLS:-4}"
CAPTURE_INTERVAL="${CAPTURE_INTERVAL:-2.0}"
ADB_BIN="${ADB_BIN:-adb}"

echo "[1/3] check python PIL dependency"
if ! python3 -c "import PIL" >/dev/null 2>&1; then
  echo "Pillow not found, installing to user site..."
  python3 -m pip install --user pillow
fi

echo "[2/3] start live dashboard"
HOST="$HOST" PORT="$PORT" ROWS="$ROWS" COLS="$COLS" CAPTURE_INTERVAL="$CAPTURE_INTERVAL" ADB_BIN="$ADB_BIN" \
  bash "$ROOT_DIR/scripts/start_live_dashboard.sh"

echo "[3/3] verify listening"
if ss -lnt 2>/dev/null | awk '{print $4}' | grep -q ":$PORT$"; then
  echo "LISTEN_OK port=$PORT"
else
  echo "WARN port $PORT not detected yet, check log: $ROOT_DIR/runs/logs/live_dashboard.log"
fi

echo ""
echo "Dashboard URL (server side): http://127.0.0.1:$PORT"
echo "If remote headless, use local tunnel:"
echo "  ssh -N -L 28080:127.0.0.1:$PORT -p <ssh_port> <user>@<host>"
echo "Then open: http://127.0.0.1:28080"
