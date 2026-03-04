#!/usr/bin/env bash
# Purpose: Start redroid containers from config/instances.yaml (legacy/simple path).
# Related: scripts/lib_config.sh, scripts/ensure_docker.sh, scripts/down.sh, scripts/status.sh
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/scripts/lib_config.sh"
"$ROOT_DIR/scripts/ensure_docker.sh"

image="$(cfg_image)"
count="${INSTANCE_COUNT:-$(cfg_count)}"
adb_base="$(cfg_adb_base_port)"

if [[ -z "$image" || -z "$count" || -z "$adb_base" ]]; then
  echo "invalid config in $ROOT_DIR/config/instances.yaml" >&2
  exit 1
fi

if [[ ! -e /dev/binder && ! -e /dev/binderfs/binder ]]; then
  echo "WARN: binder device not found on host. redroid may fail to boot." >&2
fi

echo "Pulling image: $image"
docker pull "$image"

for i in $(seq 0 $((count-1))); do
  name="redroid-$i"
  port=$((adb_base+i))

  if docker ps -a --format '{{.Names}}' | grep -qx "$name"; then
    echo "Container $name exists, removing..."
    docker rm -f "$name" >/dev/null 2>&1 || true
  fi

  echo "Starting $name (adb:$port -> 5555)"
  docker run -d --name "$name" --privileged \
    -p "$port:5555" \
    "$image" >/dev/null

done

echo "UP_DONE count=$count"
