# Optimization report

## Workload and environment

Record the goal, original project revision, exact SQL or dbt invocation,
parameters, selected nodes, source snapshot, client versions, and starting
Duckling sizes and dbt threads. Link the experiment checkpoint and resource
ledger. Include the experiment batch concurrency and warm-up evidence.
For dbt, record the confirmed run scope and focus: recurring incremental runs,
full refreshes, initial builds/backfills, or multiple modes. Report each scoped
mode separately rather than generalizing results from one mode to another.

## Findings and proposed changes

For each candidate, link the profile, compiled SQL, operator analysis, and
unified diff. Explain the bottleneck and the expected physical change. Identify
any changed dependency or public relation boundary.

## Measurements

| Variant | Batch conditions | Query IDs | Client time | Server time | Wait | Spill |
| --- | --- | --- | --- | --- | --- | --- |
| Baseline | | | | | | |
| Candidate | | | | | | |

Keep unavailable metrics explicitly unavailable. Include every measured sample;
record warm-up separately. Compare runtime deltas only for comparable batches.
When repeated sampling was requested, include its request, sample counts,
median, range, and variability. A single observation has no measured variance.

For dbt, distinguish principal model queries, execution span, the dependency
critical path, and complete command wall time. Record all failed and skipped
nodes, including any pre-existing failures.

## Correctness

Link schema, count, duplicate-preserving comparison, floating-point tolerances,
nondeterminism controls, dbt test results, and downstream checks. A command that
selected no tests is `not_applicable_no_selected_tests`. Describe anything that
remains inconclusive. Hashes alone do not establish equality.

## Decisions

Record accepted suggestions, rejected candidates, and inconclusive results with
reasons. Distinguish isolated query improvements from verified workload gains.
Original source changes and commits require explicit instructions.

## Resources, cleanup, and resume

List retained result relations and their owners. Link independent verification
that disposable clones, campaign-created shares, scoped tokens, and accounts
were removed when their work finished. Preserve any failed cleanup here.

If work is incomplete, state the exact next step, which resources still exist,
and which evidence is valid. Another session must be able to continue without
reading the conversation.
