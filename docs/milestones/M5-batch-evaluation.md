# M5 Design: Batch Run and Result-Set Evaluation

## Goal

Run batch tasks and output result-set based evaluation, including first-pass and rerun-final metrics.

## Inputs

- M4 orchestrator runtime
- Result set manifests
- Evaluator plugins

## Deliverables

- `batch_report.json`
- `aggregated_report.json`
- `evaluation_report.json`

## Implementation Tasks

1. Implement result set loaders (merge/compare modes).
2. Implement dedup strategy (`last/first/best`).
3. Compute:
   - run_completed_rate
   - task_success_rate in completed subset
   - incomplete breakdown
   - first-pass vs final-after-rerun
4. Version stamp reports: manifest and evaluator versions.

## Acceptance

- Reports are reproducible for same inputs.
- Completed subset policy is enforced.
- Parent-result-set rerun path is reflected in final metrics.

## Rollback

- Re-run evaluation on previous stable result set and policy version.
