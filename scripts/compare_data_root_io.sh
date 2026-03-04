#!/usr/bin/env bash
# Purpose: Compare startup/resource/storage behavior across docker data-root locations under redroid batch load.
# Related: scripts/force_converge_docker.sh, runs/results/io-compare-*, docs/project_status_and_scripts.md
set -euo pipefail

# Compare redroid runtime behavior between two Docker data-root locations.
# This script is intended to run on a real host (non-sandbox).
#
# What it does for each scenario:
# 1) Restart dockerd with the target data-root (vfs driver).
# 2) Start N redroid containers.
# 3) Collect IO/resource/storage metrics.
# 4) Remove redroid containers.
#
# WARNING:
# - This script restarts dockerd and kills running containers.
# - Run as root.

IMAGE="${IMAGE:-redroid/redroid:12.0.0-latest}"
COUNT="${COUNT:-20}"
ADB_BASE_PORT="${ADB_BASE_PORT:-15500}"

DEFAULT_DATA_ROOT="${DEFAULT_DATA_ROOT:-/var/lib/docker}"
ROOT_DATA_ROOT="${ROOT_DATA_ROOT:-/root/docker-data-bench}"

RESULT_DIR="${RESULT_DIR:-/remote-home1/lbsun/redroid-cluster/runs/results/io-compare-$(date -u +%Y%m%dT%H%M%SZ)}"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing command: $1" >&2
    exit 1
  }
}

wait_no_process() {
  local name="$1"
  local timeout_sec="${2:-30}"
  local elapsed=0
  while pgrep -x "$name" >/dev/null 2>&1; do
    if [[ "$elapsed" -ge "$timeout_sec" ]]; then
      return 1
    fi
    sleep 1
    elapsed=$((elapsed + 1))
  done
  return 0
}

cleanup_stale_exec_roots() {
  local current="$1"
  local base="/tmp"
  local d
  for d in "${base}"/docker-exec-default-* "${base}"/docker-exec-root-*; do
    [[ -d "$d" ]] || continue
    [[ "$d" == "$current" ]] && continue
    # Skip busy mount trees; remove only truly stale directories.
    if findmnt -rn -T "$d" >/dev/null 2>&1; then
      continue
    fi
    rm -rf "$d" >/dev/null 2>&1 || true
  done
}

say() {
  echo "[$(date -u +%H:%M:%S)] $*"
}

cleanup_redroid() {
  local names
  names="$(docker ps -a --format '{{.Names}}' | awk '$1 ~ /^redroid-/')"
  if [[ -z "$names" ]]; then
    return 0
  fi
  while read -r n; do
    [[ -z "$n" ]] && continue
    docker rm -f "$n" >/dev/null 2>&1 || true
  done <<<"$names"
}

wait_docker() {
  for _ in $(seq 1 90); do
    if docker info >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done
  return 1
}

start_dockerd() {
  local data_root="$1"
  local tag="$2"
  local exec_root="/tmp/docker-exec-${tag}-$(date +%s)"
  local pid_file="/tmp/dockerd-${tag}-$(date +%s).pid"
  local log_file="/tmp/dockerd-${tag}.log"

  say "restart dockerd for ${tag}: data-root=${data_root}"
  cleanup_redroid
  pkill -x dockerd >/dev/null 2>&1 || true
  pkill -x containerd >/dev/null 2>&1 || true
  if ! wait_no_process dockerd 30; then
    echo "dockerd is still running after stop attempt" >&2
    pgrep -a dockerd >&2 || true
    exit 1
  fi
  if ! wait_no_process containerd 30; then
    echo "containerd is still running after stop attempt" >&2
    pgrep -a containerd >&2 || true
    exit 1
  fi

  rm -f /var/run/docker.sock /var/run/docker.pid || true
  mkdir -p "$data_root" "$exec_root"
  cleanup_stale_exec_roots "$exec_root"

  nohup dockerd \
    --data-root "$data_root" \
    --exec-root "$exec_root" \
    --pidfile "$pid_file" \
    --storage-driver vfs \
    --iptables=false \
    >"$log_file" 2>&1 &

  if ! wait_docker; then
    echo "dockerd failed for ${tag}" >&2
    tail -n 120 "$log_file" >&2 || true
    exit 1
  fi

  # Guard against accidental duplicate daemons.
  if [[ "$(pgrep -xc dockerd || true)" -gt 1 ]]; then
    echo "multiple dockerd processes detected after restart" >&2
    pgrep -a dockerd >&2 || true
    exit 1
  fi

  docker info --format "StorageDriver={{.Driver}} DockerRootDir={{.DockerRootDir}}"
}

start_redroid_batch() {
  local count="$1"
  local adb_base="$2"
  local image="$3"

  cleanup_redroid
  say "Pulling image: ${image}"
  docker pull "$image"

  for i in $(seq 0 $((count - 1))); do
    local name="redroid-${i}"
    local port=$((adb_base + i))
    say "Starting ${name} on adb port ${port}"
    docker run -d \
      --name "$name" \
      --privileged \
      --restart unless-stopped \
      -p "${port}:5555" \
      "$image" >/dev/null
  done
  say "UP_DONE count=${count} adb_base=${adb_base} image=${image}"
}

collect_metrics() {
  local label="$1"
  local data_root="$2"
  local out_dir="$3"

  mkdir -p "$out_dir"

  say "collect metrics for ${label}"
  {
    echo "label=${label}"
    date -u +"ts_utc=%Y-%m-%dT%H:%M:%SZ"
    docker info --format 'storage_driver={{.Driver}} docker_root={{.DockerRootDir}}'
    echo "redroid_count=$(docker ps --format '{{.Names}}' | awk '$1 ~ /^redroid-/' | wc -l)"
    echo "data_root_du=$(du -sh "$data_root" | awk '{print $1}')"
  } >"${out_dir}/summary.txt"

  vmstat 1 10 >"${out_dir}/vmstat.txt"
  uptime >"${out_dir}/uptime.txt"
  free -h >"${out_dir}/free.txt"

  docker stats --no-stream \
    --format '{{.Name}}\t{{.CPUPerc}}\t{{.MemUsage}}\t{{.NetIO}}\t{{.BlockIO}}' \
    | awk '$1 ~ /^redroid-/' \
    | sort -V >"${out_dir}/docker-stats.tsv"

  docker system df >"${out_dir}/docker-system-df.txt"
  docker ps -s --format 'table {{.Names}}\t{{.Status}}\t{{.Size}}' \
    | awk 'NR==1 || $1 ~ /^redroid-/' >"${out_dir}/docker-ps-size.txt"

  du -sh "$data_root"/* 2>/dev/null | sort -hr | head -n 40 >"${out_dir}/data-root-top-du.txt" || true
}

run_case() {
  local label="$1"
  local data_root="$2"
  local case_dir="${RESULT_DIR}/${label}"
  local up_start up_end

  say "==== CASE ${label} BEGIN ===="
  mkdir -p "$case_dir"
  echo "case=${label} ts_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) stage=begin" >>"${RESULT_DIR}/progress.log"
  start_dockerd "$data_root" "$label"

  up_start="$(date +%s)"
  start_redroid_batch "$COUNT" "$ADB_BASE_PORT" "$IMAGE"
  up_end="$(date +%s)"
  echo "up_seconds=$((up_end - up_start))" >"${case_dir}/timing.txt"
  echo "case=${label} ts_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) stage=up_done up_seconds=$((up_end - up_start))" >>"${RESULT_DIR}/progress.log"

  sleep 25
  echo "case=${label} ts_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) stage=collect_metrics" >>"${RESULT_DIR}/progress.log"
  collect_metrics "$label" "$data_root" "$case_dir"
  cleanup_redroid
  echo "case=${label} ts_utc=$(date -u +%Y-%m-%dT%H:%M:%SZ) stage=end" >>"${RESULT_DIR}/progress.log"
  say "==== CASE ${label} END ===="
}

main() {
  require_cmd docker
  require_cmd vmstat
  require_cmd awk
  require_cmd du

  mkdir -p "$RESULT_DIR"
  say "results dir: $RESULT_DIR"
  say "image=$IMAGE count=$COUNT adb_base_port=$ADB_BASE_PORT"

  run_case "default" "$DEFAULT_DATA_ROOT"
  run_case "root" "$ROOT_DATA_ROOT"

  say "DONE. compare files:"
  echo "  ${RESULT_DIR}/default"
  echo "  ${RESULT_DIR}/root"
}

main "$@"
