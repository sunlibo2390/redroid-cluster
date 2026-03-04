# M4 Design: Task Orchestrator and Recovery

## Goal

Deliver production-like scheduling core for queueing, leasing, retry, state transitions, and rerun.

## Inputs

- M3 multi-instance runtime
- `docs/design.md` schema and state machine

## Deliverables

- `orchestrator/db.py`, `state.py`, `loop.py`, `worker.py`, `ops.py`
- SQLite schema migration scripts
- Retry rule config

## Must-Have Features

1. Task and instance state machine enforcement.
2. Lease + heartbeat + lease-expiry reclaim.
3. Retry decision by `error_code` rule table.
4. `parent_result_set_id` driven rerun queue generation.
5. Single-instance single-task lock.
6. Structured logs and core scheduler metrics.
7. Ops commands: pause/resume/remove/replay.

## Acceptance

- Baseline/Target/Gate metrics are produced for scheduler.
- No zombie running tasks after lease-expiry tests.
- No duplicate execution on the same instance.

## Rollback

- Stop scheduler loop.
- Restore previous DB snapshot and `release_manifest` versions.
