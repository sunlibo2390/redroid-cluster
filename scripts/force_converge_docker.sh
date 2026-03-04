#!/usr/bin/env bash
# Purpose: Force-converge docker runtime state by killing stale processes/mount trees and restarting one clean daemon.
# Related: scripts/compare_data_root_io.sh, scripts/ensure_docker.sh
set -euo pipefail

# Force-converge Docker runtime state in nested/containerized env.
# WARNING: this will kill dockerd/containerd/containerd-shim processes.

DATA_ROOT="${DATA_ROOT:-/var/lib/docker}"
DOCKER_LOG="${DOCKER_LOG:-/tmp/dockerd.log}"

say() { echo "[$(date -u +%H:%M:%S)] $*"; }

wait_no_proc() {
  local name="$1"
  local timeout="${2:-30}"
  local i
  for i in $(seq 1 "$timeout"); do
    if ! pgrep -x "$name" >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

say "Stop benchmark scripts if any"
pkill -f compare_data_root_io.sh >/dev/null 2>&1 || true

say "Force stop docker stack"
pkill -9 -x dockerd >/dev/null 2>&1 || true
pkill -9 -x containerd >/dev/null 2>&1 || true
pkill -9 -f containerd-shim-runc-v2 >/dev/null 2>&1 || true

wait_no_proc dockerd 20 || true
wait_no_proc containerd 20 || true

say "Lazy-unmount old exec-root mount trees"
for d in /tmp/docker-exec-* /var/run/docker/containerd; do
  [[ -e "$d" ]] || continue
  while read -r target; do
    [[ -n "$target" ]] || continue
    umount -l "$target" >/dev/null 2>&1 || true
  done < <(findmnt -rn -R "$d" -o TARGET 2>/dev/null || true)
done

say "Remove stale runtime sockets and dirs"
rm -f /var/run/docker.sock /var/run/docker.pid /tmp/dockerd*.pid || true
rm -rf /var/run/docker/containerd /tmp/docker-exec-* || true

say "Start one clean dockerd"
nohup dockerd \
  --data-root "$DATA_ROOT" \
  --storage-driver vfs \
  --iptables=false \
  >"$DOCKER_LOG" 2>&1 &

ok=0
for _ in $(seq 1 60); do
  if docker info >/dev/null 2>&1; then
    ok=1
    break
  fi
  sleep 1
done

if [[ "$ok" != "1" ]]; then
  echo "CONVERGE_FAIL: docker did not come up" >&2
  tail -n 120 "$DOCKER_LOG" >&2 || true
  exit 1
fi

say "Converged"
docker info --format 'StorageDriver={{.Driver}} DockerRootDir={{.DockerRootDir}}'
echo "dockerd_count=$(pgrep -xc dockerd || true)"
echo "containerd_count=$(pgrep -xc containerd || true)"
echo "shim_count=$(pgrep -fc containerd-shim-runc-v2 || true)"
