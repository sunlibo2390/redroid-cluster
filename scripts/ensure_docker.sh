#!/usr/bin/env bash
set -euo pipefail

if docker info >/dev/null 2>&1; then
  exit 0
fi

# Clean stale runtime artifacts from crashed daemon
rm -f /var/run/docker.pid /var/run/docker.sock

nohup dockerd \
  --storage-driver=vfs \
  --iptables=false \
  --bridge=none \
  --ip-forward=false \
  --ip-masq=false \
  >/tmp/dockerd.log 2>&1 &

for _ in $(seq 1 30); do
  if docker info >/dev/null 2>&1; then
    exit 0
  fi
  sleep 1
done

echo "failed to start docker daemon" >&2
tail -n 80 /tmp/dockerd.log >&2 || true
exit 1
