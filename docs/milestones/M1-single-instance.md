# M1 Design: Single Host Single Redroid

## Goal

Run one redroid instance stably and verify ADB interaction path.

## Inputs

- `config/instances.yaml` with one instance
- Image and runtime options

## Deliverables

- Single-instance compose/runtime script
- Smoke script proving `adb connect` and core actions

## Implementation Tasks

1. Create `scripts/up.sh` and `scripts/down.sh` for one instance.
2. Implement `scripts/smoke.sh` with checks:
   - container up
   - adb connect
   - basic input/screenshot
3. Persist logs in `runs/logs`.

## Acceptance

- One instance starts and survives restart policy.
- Smoke check passes consistently.

## Rollback

- Stop and remove the instance.
- Revert to previous image/runtime parameters.
