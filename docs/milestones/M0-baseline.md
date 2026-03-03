# M0 Design: Environment Baseline and Capacity

## Goal

Establish single-host baseline for CPU, memory, IO, port usage, startup latency, and safe instance count.

## Inputs

- Host config
- Redroid image tag
- Resource constraints (cpu, memory)

## Deliverables

- Baseline report JSON/Markdown
- Recommended `instance_count` range
- Initial `metric_policy_version`

## Implementation Tasks

1. Build `scripts/precheck.sh` for Docker/ADB/ports/disk checks.
2. Build `scripts/capacity_probe.sh` to run step-load (1/4/8/16/...) instances.
3. Collect metrics: startup time, ADB ready time, RSS/CPU, error rates.
4. Output report into `runs/reports/m0-baseline-<date>.json`.

## Acceptance

- Precheck passes.
- Capacity report exists with recommended safe capacity.
- No unresolved fatal infra errors.

## Rollback

- Stop probe instances.
- Restore previous host config and image tag from `release_manifest`.
