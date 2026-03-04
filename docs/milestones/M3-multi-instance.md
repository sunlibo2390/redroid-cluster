# M3 Design: Single Host Multi-Instance Cluster

## Goal

Scale from one instance to many instances on a single host with stable resource and port planning.

## Inputs

- M0 capacity recommendation
- M1 runtime scripts

## Deliverables

- Multi-instance config template
- Deterministic port allocation rule
- Batch health checker for all instances

## Implementation Tasks

1. Extend `config/instances.yaml` for N instances.
2. Implement deterministic port mapping rule.
3. Add `scripts/status.sh` and batch ADB connect check.
4. Enforce per-instance resource constraints.

## Acceptance

- N instances run concurrently without port collision.
- Batch health check reports all reachable or explicitly quarantined.

## Rollback

- Scale down to 1 instance profile.
- Restore previous instance config from git.
