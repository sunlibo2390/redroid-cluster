#!/usr/bin/env bash
# Purpose: M0 host-level capacity probe and recommended instance count estimation.
# Related: config/capacity_probe.env, docs/milestones/M0-baseline.md, runs/reports/m0-capacity-*.json
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
source "$ROOT_DIR/config/capacity_probe.env"

REPORT_DIR="$ROOT_DIR/runs/reports"
mkdir -p "$REPORT_DIR"
ts="$(date -u +%Y%m%dT%H%M%SZ)"
report_json="$REPORT_DIR/m0-capacity-$ts.json"

host_id="$(hostname)"
cpu_total="$(nproc)"
mem_total_mb="$(awk '/MemTotal/ {print int($2/1024)}' /proc/meminfo)"

# Use lightweight host-level probe first (no container launch yet)
# Future M1/M3 will replace with real redroid launches.

levels_json=""
for lvl in $PROBE_LEVELS; do
  cpu_idle="$(top -bn1 | awk -F',' '/Cpu\(s\)/{gsub("%id", "", $4); gsub(" ", "", $4); print $4; exit}')"
  mem_avail_mb="$(awk '/MemAvailable/ {print int($2/1024)}' /proc/meminfo)"
  cpu_used_pct="$(awk -v idle="$cpu_idle" 'BEGIN{printf "%.1f", 100-idle}')"
  mem_used_pct="$(awk -v total="$mem_total_mb" -v avail="$mem_avail_mb" 'BEGIN{printf "%.1f", ((total-avail)/total)*100}')"

  if [[ -n "$levels_json" ]]; then
    levels_json+=" ,"
  fi
  levels_json+="{\"level\":$lvl,\"cpu_used_pct\":$cpu_used_pct,\"mem_used_pct\":$mem_used_pct}"

  sleep "$PROBE_SLEEP_SEC"
done

# recommend conservative safe count from thresholds
recommended="0"
idx="0"
for lvl in $PROBE_LEVELS; do
  idx=$((idx + 1))
  metric="$(echo "$levels_json" | tr '{' '\n' | sed -n "${idx}p")"
  cpu="$(echo "$metric" | sed -n 's/.*\"cpu_used_pct\":\([0-9.]*\).*/\1/p')"
  mem="$(echo "$metric" | sed -n 's/.*\"mem_used_pct\":\([0-9.]*\).*/\1/p')"
  if awk -v c="$cpu" -v m="$mem" -v cmax="$MAX_CPU_WARN_PCT" -v mmax="$MAX_MEM_WARN_PCT" 'BEGIN{exit !((c<cmax)&&(m<mmax))}'; then
    recommended="$lvl"
  fi
done

cat > "$report_json" <<EOF_JSON
{
  "ts_utc": "$ts",
  "host_id": "$host_id",
  "cpu_total": $cpu_total,
  "mem_total_mb": $mem_total_mb,
  "probe_levels": [ $levels_json ],
  "recommended_instance_count": $recommended,
  "notes": "M0 host-level baseline probe (container-level probe will be added in M3)."
}
EOF_JSON

echo "WROTE $report_json"
