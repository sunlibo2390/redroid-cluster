#!/usr/bin/env bash
# Purpose: Read project config values from config/instances.yaml.
# Related: scripts/up.sh, scripts/smoke.sh, config/instances.yaml
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CFG_FILE="$ROOT_DIR/config/instances.yaml"

cfg_get() {
  local key="$1"
  awk -F': ' -v k="$key" '$1==k {print $2}' "$CFG_FILE" | tr -d '"'
}

cfg_image() { cfg_get "image"; }
cfg_count() { cfg_get "instance_count"; }
cfg_adb_base_port() { cfg_get "adb_base_port"; }
cfg_host_id() { cfg_get "host_id"; }
