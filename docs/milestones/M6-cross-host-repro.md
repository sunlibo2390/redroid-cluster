# M6 Design: Cross-Host Reproducibility

## Goal

Reproduce the same workflow on another server with the same commands and docs.

## Inputs

- Shared docs, scripts, configs
- Host-specific env file only

## Deliverables

- Reproduction checklist output
- Cross-host comparison report

## Implementation Tasks

1. Create host profile files (`host-a/b/c`).
2. Run standard pipeline on host B:
   - precheck
   - up
   - smoke
   - batch
3. Compare report structure and metric policy versions.
4. Record deviations and patch docs/scripts.

## Acceptance

- Host B run succeeds with same command set.
- No host-specific code changes required.

## Rollback

- Revert host-specific config changes only.
- Keep shared logic unchanged.
