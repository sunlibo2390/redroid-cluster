# Milestone Design Index

- [M0 Baseline](/remote-home1/lbsun/redroid-cluster/docs/milestones/M0-baseline.md)
- [M1 Single Instance](/remote-home1/lbsun/redroid-cluster/docs/milestones/M1-single-instance.md)
- [M2 AndroidWorld E2E](/remote-home1/lbsun/redroid-cluster/docs/milestones/M2-androidworld-e2e.md)
- [M3 Multi Instance](/remote-home1/lbsun/redroid-cluster/docs/milestones/M3-multi-instance.md)
- [M4 Orchestrator](/remote-home1/lbsun/redroid-cluster/docs/milestones/M4-orchestrator.md)
- [M5 Batch Evaluation](/remote-home1/lbsun/redroid-cluster/docs/milestones/M5-batch-evaluation.md)
- [M6 Cross Host Repro](/remote-home1/lbsun/redroid-cluster/docs/milestones/M6-cross-host-repro.md)

## Execution Order

M0 -> M1 -> M2 -> M3 -> M4 -> M5 -> M6

## Policy

- Each milestone is blocked until its acceptance checks pass.
- Metric gates use `Baseline/Target/Gate` and evolve by `metric_policy_version`.
- Rollback uses `release_manifest` as the source of truth.
