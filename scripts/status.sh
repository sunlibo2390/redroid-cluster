#!/usr/bin/env bash
# Purpose: Show status table for redroid-* containers.
# Related: scripts/up.sh, scripts/down.sh, scripts/redroid-cluster.sh
set -euo pipefail
"$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)/ensure_docker.sh"

docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | awk 'NR==1 || $1 ~ /^redroid-/'
