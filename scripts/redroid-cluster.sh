#!/usr/bin/env bash
# Purpose: Unified redroid lifecycle entrypoint (up/down/status/smoke) with binder and docker-proxy safety checks.
# Related: scripts/up.sh, scripts/down.sh, scripts/status.sh, scripts/smoke.sh, docs/project_status_and_scripts.md
set -euo pipefail

usage() {
  cat <<USAGE
Usage:
  scripts/redroid-cluster.sh up [count] [adb_base_port] [image]
  scripts/redroid-cluster.sh down
  scripts/redroid-cluster.sh status
  scripts/redroid-cluster.sh smoke [count] [adb_base_port]

Env:
  PREFIX=redroid                # container name prefix
  ALLOW_NO_BINDER=0             # set 1 to bypass binder check
  DOCKER_PROXY_URL=http://127.0.0.1:10090
  AUTO_CONFIG_DOCKER_PROXY=1    # set 0 to disable daemon proxy auto-config

Examples:
  scripts/redroid-cluster.sh up 4 15500 redroid/redroid:12.0.0-latest
  scripts/redroid-cluster.sh status
  scripts/redroid-cluster.sh smoke 4 15500
  scripts/redroid-cluster.sh down
USAGE
}

PREFIX="${PREFIX:-redroid}"
ALLOW_NO_BINDER="${ALLOW_NO_BINDER:-0}"
DOCKER_PROXY_URL="${DOCKER_PROXY_URL:-http://127.0.0.1:10090}"
AUTO_CONFIG_DOCKER_PROXY="${AUTO_CONFIG_DOCKER_PROXY:-1}"

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || { echo "missing command: $1" >&2; exit 1; }
}

has_proxy_env_text() {
  local text="${1:-}"
  [[ "$text" == *"HTTP_PROXY=$DOCKER_PROXY_URL"* && "$text" == *"HTTPS_PROXY=$DOCKER_PROXY_URL"* ]]
}

dockerd_proc_has_proxy() {
  local pid="${1:-}"
  [[ -n "$pid" ]] || return 1
  [[ -r "/proc/$pid/environ" ]] || return 1
  local env_text
  env_text="$(tr '\0' '\n' <"/proc/$pid/environ" 2>/dev/null || true)"
  has_proxy_env_text "$env_text"
}

configure_docker_proxy() {
  [[ "$AUTO_CONFIG_DOCKER_PROXY" == "1" ]] || return 0

  if command -v systemctl >/dev/null 2>&1 && [[ -d /run/systemd/system ]]; then
    local dropin_dir="/etc/systemd/system/docker.service.d"
    local dropin_file="$dropin_dir/http-proxy.conf"
    local tmp_file
    local changed=0
    local runtime_env
    tmp_file="$(mktemp)"

    cat >"$tmp_file" <<EOF
[Service]
Environment="HTTP_PROXY=$DOCKER_PROXY_URL"
Environment="HTTPS_PROXY=$DOCKER_PROXY_URL"
Environment="NO_PROXY=localhost,127.0.0.1"
EOF

    mkdir -p "$dropin_dir"
    if [[ -f "$dropin_file" ]] && cmp -s "$tmp_file" "$dropin_file"; then
      rm -f "$tmp_file"
    else
      mv "$tmp_file" "$dropin_file"
      changed=1
      echo "docker daemon proxy drop-in updated: $DOCKER_PROXY_URL" >&2
    fi

    runtime_env="$(systemctl show --property=Environment docker 2>/dev/null || true)"
    if [[ "$changed" == "1" ]] || ! has_proxy_env_text "$runtime_env"; then
      echo "docker daemon proxy not active at runtime, restarting docker..." >&2
      systemctl daemon-reload
      systemctl restart docker
      runtime_env="$(systemctl show --property=Environment docker 2>/dev/null || true)"
      if ! has_proxy_env_text "$runtime_env"; then
        echo "WARN docker restarted but runtime proxy vars still not detected" >&2
      fi
    fi
    return 0
  fi

  local pid
  pid="$(pgrep -x dockerd | head -n1 || true)"
  if [[ -z "$pid" ]]; then
    return 0
  fi
  if dockerd_proc_has_proxy "$pid"; then
    return 0
  fi

  echo "dockerd is running without proxy env, restarting dockerd with proxy..." >&2
  pkill -x dockerd >/dev/null 2>&1 || true
  rm -f /var/run/docker.pid /var/run/docker.sock
  nohup env \
    HTTP_PROXY="$DOCKER_PROXY_URL" \
    HTTPS_PROXY="$DOCKER_PROXY_URL" \
    NO_PROXY="localhost,127.0.0.1" \
    dockerd --storage-driver=vfs --iptables=false >/tmp/dockerd.log 2>&1 &

  for _ in $(seq 1 30); do
    if docker info >/dev/null 2>&1; then
      local new_pid
      new_pid="$(pgrep -x dockerd | head -n1 || true)"
      if dockerd_proc_has_proxy "$new_pid"; then
        return 0
      fi
    fi
    sleep 1
  done

  echo "failed to restart dockerd with proxy env" >&2
  tail -n 80 /tmp/dockerd.log 2>/dev/null || true
  exit 1
}

check_docker() {
  require_cmd docker
  if docker info >/dev/null 2>&1; then
    return 0
  fi

  echo "docker daemon unreachable, trying to start..." >&2

  # Prefer systemd service when available.
  if command -v systemctl >/dev/null 2>&1; then
    if systemctl start docker >/dev/null 2>&1; then
      for _ in $(seq 1 20); do
        if docker info >/dev/null 2>&1; then
          return 0
        fi
        sleep 1
      done
    fi
  fi

  # Fallback for non-systemd environments.
  if ! pgrep -x dockerd >/dev/null 2>&1; then
    nohup env \
      HTTP_PROXY="$DOCKER_PROXY_URL" \
      HTTPS_PROXY="$DOCKER_PROXY_URL" \
      NO_PROXY="localhost,127.0.0.1" \
      dockerd --storage-driver=vfs --iptables=false >/tmp/dockerd.log 2>&1 &
  fi

  for _ in $(seq 1 30); do
    if docker info >/dev/null 2>&1; then
      return 0
    fi
    sleep 1
  done

  echo "docker daemon unreachable after auto-start attempts" >&2
  tail -n 80 /tmp/dockerd.log 2>/dev/null || true
  exit 1
}

check_binder() {
  if [[ -e /dev/binder || -e /dev/binderfs/binder ]]; then
    :
  else
    if [[ "$ALLOW_NO_BINDER" == "1" ]]; then
      echo "WARN binder missing, but ALLOW_NO_BINDER=1 so continuing" >&2
      return 0
    fi
    echo "binder device missing: /dev/binder or /dev/binderfs/binder not found" >&2
    echo "set ALLOW_NO_BINDER=1 to bypass (not recommended)" >&2
    exit 1
  fi

  # Validate that /dev/binder is a real binder node when present.
  # In nested-container environments, /dev/binder is sometimes accidentally
  # mapped to /dev/kvm (major=10,minor=232), which causes redroid ADB offline.
  if [[ -e /dev/binder ]]; then
    local dev_hex major_hex minor_hex major_dec minor_dec
    local binder_major kvm_minor
    dev_hex="$(stat -L -c '%t:%T' /dev/binder 2>/dev/null || true)"
    major_hex="${dev_hex%%:*}"
    minor_hex="${dev_hex##*:}"
    major_dec="$((16#${major_hex:-0}))"
    minor_dec="$((16#${minor_hex:-0}))"
    binder_major="$(awk '$2=="binder"{print $1; exit}' /proc/devices 2>/dev/null || true)"
    kvm_minor="$(awk '$2=="kvm"{print $1; exit}' /proc/misc 2>/dev/null || true)"

    if [[ -n "$binder_major" && "$major_dec" != "$binder_major" ]]; then
      if [[ "$major_dec" == "10" && -n "$kvm_minor" && "$minor_dec" == "$kvm_minor" ]]; then
        echo "FATAL /dev/binder points to kvm (major=$major_dec minor=$minor_dec), not binder" >&2
        echo "Fix on host: provide real binder device (prefer /dev/binderfs/binder) to this runtime." >&2
      else
        echo "FATAL /dev/binder major=$major_dec does not match binder major=$binder_major" >&2
        echo "Fix on host: mount binderfs and map real binder node into this environment." >&2
      fi
      [[ "$ALLOW_NO_BINDER" == "1" ]] || exit 1
    fi
  fi

  return 0
}

up() {
  local count="${1:-1}"
  local adb_base="${2:-15500}"
  local image="${3:-redroid/redroid:12.0.0-latest}"

  configure_docker_proxy
  check_docker
  check_binder

  echo "Pulling image: $image"
  docker pull "$image"

  for i in $(seq 0 $((count-1))); do
    local name="${PREFIX}-${i}"
    local port=$((adb_base+i))

    if docker ps -a --format '{{.Names}}' | grep -qx "$name"; then
      docker rm -f "$name" >/dev/null 2>&1 || true
    fi

    echo "Starting $name on adb port $port"
    docker run -d \
      --name "$name" \
      --privileged \
      --restart unless-stopped \
      -p "$port:5555" \
      "$image" >/dev/null
  done

  echo "UP_DONE count=$count adb_base=$adb_base image=$image"
}

down() {
  check_docker
  local names
  names="$(docker ps -a --format '{{.Names}}' | grep "^${PREFIX}-" || true)"
  if [[ -z "$names" ]]; then
    echo "no ${PREFIX}-* containers found"
    return 0
  fi
  while read -r n; do
    [[ -z "$n" ]] && continue
    echo "Removing $n"
    docker rm -f "$n" >/dev/null 2>&1 || true
  done <<< "$names"
  echo "DOWN_DONE"
}

status() {
  check_docker
  docker ps -a --format 'table {{.Names}}\t{{.Status}}\t{{.Ports}}' | awk -v pfx="^""${PREFIX}-" 'NR==1 || $1 ~ pfx'
}

smoke() {
  local count="${1:-1}"
  local adb_base="${2:-15500}"

  check_docker
  require_cmd adb

  local failed=0
  for i in $(seq 0 $((count-1))); do
    local name="${PREFIX}-${i}"
    local serial="127.0.0.1:$((adb_base+i))"

    if ! docker ps --format '{{.Names}}' | grep -qx "$name"; then
      echo "SMOKE_FAIL $name not running" >&2
      failed=1
      continue
    fi

    adb connect "$serial" >/dev/null 2>&1 || true
    local state=""
    for _ in $(seq 1 30); do
      state="$(adb -s "$serial" get-state 2>/dev/null || true)"
      [[ "$state" == "device" ]] && break
      sleep 1
    done

    if [[ "$state" != "device" ]]; then
      echo "SMOKE_FAIL $name adb=$serial state=${state:-none}" >&2
      docker logs --tail 60 "$name" >&2 || true
      failed=1
      continue
    fi

    local release
    release="$(adb -s "$serial" shell getprop ro.build.version.release 2>/dev/null | tr -d '\r' | head -n1)"
    adb -s "$serial" shell input keyevent 3 >/dev/null 2>&1 || true
    echo "SMOKE_PASS $name adb=$serial android_release=${release:-unknown}"
  done

  [[ "$failed" == "0" ]]
}

cmd="${1:-}"
shift || true

case "$cmd" in
  up) up "$@" ;;
  down) down ;;
  status) status ;;
  smoke) smoke "$@" ;;
  -h|--help|help|"") usage ;;
  *) echo "unknown command: $cmd" >&2; usage; exit 2 ;;
esac
