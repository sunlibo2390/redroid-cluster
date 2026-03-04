# M2 Design: AndroidWorld End-to-End

## Goal

Finish at least one end-to-end task on redroid and persist a full trace/result
record. In this environment we run an `adb-only` backend (no emulator gRPC).

## Inputs

- Working redroid instance with stable ADB
- `orchestrator/worker.py` adb-only e2e path

## Deliverables

- `orchestrator/worker.py` `--adb-only-e2e` runner path
- One successful e2e run record with logs/artifacts

## Implementation Tasks

1. Wire worker execution loop with ADB-only task/agent/env.
2. Implement `run_completed` and `is_successful` write-back.
3. Capture run trace, step summary, screenshot, and UI XML dump.

## Acceptance

- At least one adb-only task reaches terminal state with full trace.
- Result record includes `run_completed`, `is_successful`, `attempt_index`.
- Artifact directory includes screenshot and XML dump.

## Rollback

- Disable worker integration path and keep M1 smoke path available.
