#!/usr/bin/env bash
# Purpose: Remove all redroid-* containers.
# Related: scripts/up.sh, scripts/status.sh, scripts/redroid-cluster.sh
set -euo pipefail
"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ensure_docker.sh"

names="$(docker ps -a --format '{{.Names}}' | grep '^redroid-' || true)"
if [[ -z "$names" ]]; then
  echo "No redroid containers found"
  exit 0
fi

while read -r n; do
  [[ -z "$n" ]] && continue
  echo "Removing $n"
  docker rm -f "$n" >/dev/null 2>&1 || true
done <<< "$names"

echo "DOWN_DONE"
