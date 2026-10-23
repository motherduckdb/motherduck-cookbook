# Optimization report

## Workload

Goal; revision; exact SQL/dbt command, parameters, selectors, and confirmed run
scope/focus; snapshot; client versions; sizes/threads; batch concurrency; warm-up.
Link checkpoints and resource ledger. Report scoped dbt modes separately.

## Experiments

Per candidate: bottleneck/hypothesis, expected plan change, affected dependencies
or public contracts, diff, SQL/profile evidence, decision, and reason.
Include rejected/inconclusive ideas.

## Measurements

| Variant | Conditions | Query IDs | Client time | Server time | Wait | Spill |
| --- | --- | --- | --- | --- | --- | --- |
| Baseline | | | | | | |
| Candidate | | | | | | |

All observations; missing metrics unavailable; warm-up separate; comparable
batches only. Requested repetitions: request, count, median, range, variability.
For dbt: principal queries, server span, critical path, command wall time,
failed/skipped nodes. Distinguish isolated evidence from workload gains.

## Correctness

Link schema/count/multiset checks, numeric tolerances/deltas, nondeterminism
controls, dbt/downstream tests, and unresolved differences.
No tests: `not_applicable_no_selected_tests`. Hashes alone do not prove equality.

## Cleanup and resume

Owned/retained resources; independent cleanup evidence/errors; still-live
resources; valid evidence; exact next step. Resume without transcript context.
Original-source edits/commits require explicit authorization.
