# Optimization methodology

Use this workflow for a single query, a query group, or a dbt project. Produce
reviewable suggestions, measured evidence, and durable checkpoints. Leave
original source files unchanged and make no commits unless explicitly instructed.

## 1. Resolve the inputs

Record the five inputs from the recipe README: goal, workload, credential
references, source data, and compute envelope. Discover region with
`SELECT region FROM md_user_info();`, and record client, engine, dbt, and adapter
versions. Do not make success metrics, tolerances, allowed-change policies,
spending limits, or delivery choices an additional intake questionnaire.

Inspect the customer's existing environment and dependency setup. Reuse it or
configure an isolated experiment environment with matching client and adapter
versions using `uv`; do not impose recipe-specific versions or modify the
original project's dependency files. Record the resolved environment so another
agent session can reproduce it.

For SQL, save the exact statements, parameter values, required session settings,
and the original file's revision or hash. For a query group, preserve its order,
dependencies, parameter sets, and intended query concurrency.

For dbt, clarify the optimization scope with the user unless already explicit:
are recurring incremental runs, full refreshes, initial builds/backfills, or
multiple modes the focus? Which jobs or models are in scope? Do not infer this
from model materializations or default to full-refresh benchmarking. Record the
confirmed run scope and focus in the experiment checkpoint and report. If
multiple modes are in scope, evaluate and report each separately; a gain in one
does not establish a gain in the others.

Record the exact target command, selectors, exclusions, variables,
target, and full-refresh/incremental behavior. Run `dbt ls` with matching
selection arguments and save node IDs, dependencies, materializations, and
selected tests. Include incremental models when investigating their full-refresh
path. Do not infer the intended workload from every model in the repository.

For incremental and microbatch jobs, preserve the starting target state, batch
windows, checkpoints, and late-arriving-data behavior. Empty-target and
already-populated-target runs answer different questions. Capture actual
per-batch compiled SQL where compilation depends on the event window. Do not
assume every `dbt build` argument is accepted by `dbt compile`.

## 2. Establish the durable record before provisioning

Create `optimization/` in the customer's workspace. Copy and adapt
[experiment.json](templates/experiment.json), [conditions.json](templates/conditions.json),
and [report.md](templates/report.md). Give each experiment and batch stable IDs.
Store an immutable source revision or export, including relevant uncommitted
changes, and a separate editable workspace for each candidate. Exclude secrets,
existing environments, and old generated outputs from those exports.

Use this artifact convention, extending it for the workload:

```text
optimization/
  campaign.json                 # resolved inputs, slot limits, next step
  resources.json                # ownership, IDs, live state, verified cleanup
  batches/batch-001.json         # actual concurrency and environment conditions
  experiments/experiment-001/
    experiment.json             # phase, status, errors, next step
    hypothesis.md
    source-revision.json
    candidate.diff
    baseline/                   # SQL, commands, logs, profiles, query history
    candidate/
    validation/                 # comparison SQL, outputs, tests, tolerances
    report.md
  report.md                     # campaign decisions and remaining work
```

Update checkpoints atomically before and after each state transition:
`inventory`, `provisioning`, `warming`, `profiling`, `baseline`, `candidate`,
`validating`, `decided`, and `cleaning`. Store timestamps, exact commands,
working directories, return codes, resource ownership, source snapshots, and
artifact paths. A process left `running` is incomplete until its exit status
and expected artifacts have been recovered.

Record every candidate diff before execution, including candidates later
rejected or cancelled. Keep secret values out of all checkpoints and logs.
Multiple sessions should be able to review decisions without conversation history.

## 3. Isolate the experiments

Allocate one service account per concurrent slot. Reuse that account for
sequential experiments, while giving each experiment its own writable clones,
code copy, dbt profile, target directory, logs, and local DuckDB paths.
Claim each slot atomically in the coordinator and record its owning session and
process. Another session may audit its artifacts, but must not reset the slot
or clean its resources while its experiment is active.

Use the public [service-account REST API](https://motherduck.com/docs/key-tasks/service-accounts-guide/create-and-configure-service-accounts/):

| Operation | Endpoint |
| --- | --- |
| Create a slot account | `POST /v1/users` with `{"username":"<campaign_slot>"}` |
| Mint a scoped token | `POST /v1/users/{username}/tokens` with `{"name":"<campaign>","token_type":"read_write","ttl":21600}` |
| Inspect Duckling configuration | `GET /v1/users/{username}/instances` |
| Set the permitted size | `PUT /v1/users/{username}/instances` |
| Revoke a token | `DELETE /v1/users/{username}/tokens/{token_id}` |
| Delete the slot account | `DELETE /v1/users/{username}` |

The base URL is `https://api.motherduck.com`. Verify the supplied principal can
perform the required operations before launching work. Platform permissions
and access to source shares are separate. Keep the observer credential for
query history outside the measured workload as well.

When changing size, first GET the configuration. Preserve its existing values,
including `read_scaling`, and PUT both configuration blocks:

```json
{
  "config": {
    "read_write": {"instance_size": "jumbo", "cooldown_seconds": 60},
    "read_scaling": {"instance_size": "standard", "flock_size": 0, "cooldown_seconds": 60}
  }
}
```

The example illustrates the request shape, not values to overwrite blindly.
Verify the returned configuration and record the measured query's actual
`instance_type` from query history. Size changes or resets happen only while
that slot is idle. If using `SHUTDOWN TERMINATE`, wait for a probe to succeed
before starting work, and warm inputs afterward under the new conditions.

Use one immutable source-share incarnation for a comparison. If a suitable
snapshot share already exists, record and retain it. Otherwise create a
restricted manual-update share through the authorized source owner:

```sql
CREATE SHARE opt_source_snapshot FROM source_db
    (ACCESS RESTRICTED, UPDATE MANUAL);
```

Grant access using the organization's share-access rules. With a slot token,
attach that snapshot and create writable siblings:

```sql
ATTACH '<restricted-share-url>' AS opt_source;
CREATE DATABASE opt_baseline FROM opt_source;
CREATE DATABASE opt_candidate FROM opt_source;
```

Use campaign-specific names in practice. Verify the clone contains every source
the selected models read, and repoint source references and output databases in
the isolated workspace. Audit fully qualified names, hooks, seeds, snapshots,
and external side effects before a dbt command. A profile aimed at a clone can
still read or write production if hard-coded model configuration points there.

Each local DuckDB client or dbt invocation runs in a separate OS process with
its own environment and output directories. Never share embedded connections
or writable local database files between concurrent slots.

The account cap covers the whole campaign. Count a slot until its resources
are accounted for, and count its account until final deletion is verified.
Respect per-size limits as well as the total. Choose smaller sizes for model
exploration when representative; use job-sized Ducklings for workload tests.

### Read-scaling checklist: read-only SQL only

Apply this checklist only to standalone read-only queries or query groups using
read scaling. It does not apply to dbt optimization campaigns, even when
profiling a compiled model's `SELECT`, or to write-query optimizations. Do not
enable read scaling or increase its pool unless the compute envelope explicitly
permits it. See the public [read-scaling guide](https://motherduck.com/docs/key-tasks/authenticating-and-connecting-to-motherduck/read-scaling/).

- Record the account's configured pool size and replica sizes, and the actual
  query concurrency. Account for all permitted replicas, not just service
  accounts; do not treat extra replicas as free slots outside the compute
  envelope. Keep these settings fixed for a SQL rewrite comparison unless one
  is the deliberate experiment variable.
- Prepare and attach approved experiment inputs with the scoped read/write
  setup credential before opening read-scaling connections. Execute the
  measured read workload with a separate read-scaling token. Do not use CTAS,
  `--materialize`, or other writes through that token, including during warm-up.
- Record the intended `session_name` mapping or absence of affinity. Preserve
  it between baseline and candidate unless affinity is the experiment. Verify
  observed placement using `session_name` and `duckling_id` in query history
  when available; otherwise label placement unverified.
- Verify that readers see the same pinned input snapshot before comparing
  results. Replicas are eventually consistent, so do not compare recently
  changed writer data with an unverified reader state. Use the guide's freshness
  procedure on experiment resources when needed.
- Apply the warm-up policy below to the readers actually serving the workload,
  using unmeasured, fully consumed read-only queries. Reassess warm-up and batch
  comparability if the serving replicas or pool configuration change.

## 4. Warm the input data

Before measured execution, identify the union of relations, columns, and data
ranges that the baseline and candidate will read. If any of that data has no
verified read within the past 24 hours, or the read history is unknown, read it
once to warm MotherDuck's caches. The first read can be slower when data must
be fetched from cold storage. Twenty-four hours is a conservative operational
trigger, not a stated cache expiration guarantee.

Use unmeasured executions of the actual read paths, consuming their results.
For large model outputs, warm through a disposable remote CTAS so the rows stay
in MotherDuck. A metadata-only `COUNT(*)`, a `LIMIT` preview, or reading one
column does not stand in for the workload's full input access. Build and warm
required ancestors before profiling a dependent model.

Warm both input paths before measuring either variant. Record the statements,
coverage, snapshot, slot, start/end timestamps, and completion status. A verified
recent read can be reused only for the same inputs and comparable execution
conditions. Reassess warm-up after new inputs, a size change, or a slot reset.
Warm-up samples never enter reported timing deltas or benchmark statistics.

## 5. Find the work that matters

Generate usable operator profiles with the [measurement guide](motherduck-measurement.md).
Inspect the expensive operators before proposing rewrites. Use the
[patterns reference](duckdb-patterns.md) to explain the observed work rather
than treating SQL appearance as proof of an optimization opportunity.

For each principal model query, examine scans and pushed predicates, projected
columns, join inputs and outputs, repeated computation, sorts, windows, write
cost, wait, and spill. Preserve query IDs and the source of every metric. A
completed query profile can still be useful if the surrounding dbt process
failed later; the failed invocation is not a successful benchmark sample.

For a dbt job, reconstruct its weighted dependency graph from the manifest and
run results. Separate slow queries on the critical path from large amounts of
parallel work that do not govern completion. Include materialization and setup
costs. Removing repeated hooks, narrowing a consumer branch, or avoiding a large
intermediate write may be more valuable than simplifying expressions.

## 6. Propose and run a candidate

Write a concrete hypothesis, the expected plan change, its affected consumers,
and the checks that will preserve semantics. Explain the proposed experiment
so the user can steer it, then proceed within the supplied compute envelope.
Do not require a separate allowed-change questionnaire.

Make candidate changes only in an isolated copy. Coherent combinations of
changes are reasonable when they address the same bottleneck. Save their diff
and measure the unmodified baseline and candidate from equivalent starting
states. One measured observation per variant is the default. Clear regressions
can be rejected before expensive correctness comparisons, with the reason
recorded and cleanup still performed.

Do not automatically repeat samples, launch a confidence-interval campaign, or
remeasure every commit. Repeated benchmark sampling is opt-in, only when the
user requests it. A warm-up is separate from measured repetitions. A later
job-level comparison answers a different question from an isolated model test.

Compare absolute runtime numbers only between batches with similar conditions:
same source snapshot, input windows, warm-up policy, client environment,
Duckling size, dbt threads, and actual experiment concurrency. Record changes in
batch occupancy; a partly drained batch is a different condition. Do not pool
unlike batches into a speedup or attribute their delta to a SQL change.

When deliberately testing threads or size, change that variable and hold the
others steady. Both dbt threads and Duckling size are optimization levers within
the user's envelope. Use wait and spill to guide which configurations to try.
If repetitions were requested, retain every observation and report sample
count, median, minimum, maximum, and variability. One observation establishes
neither variance nor statistical confidence.

## 7. Validate correctness

Compare freshly built baseline and candidate outputs from the same inputs and
starting target state. Stale production materializations are not an equivalent
reference for a candidate whose ancestors were rebuilt.

Require matching column names, order, and types; matching row counts; and a
duplicate-preserving comparison. [compare_results.sql](scripts/compare_results.sql)
provides schema, count, and bidirectional `EXCEPT ALL` checks. Execute its schema
gate first; implicit casts in a set comparison must not hide changed types.
Run relevant dbt data/unit tests and validate downstream outputs affected by
dependency or materialization changes. Zero selected tests means
`not_applicable_no_selected_tests`, not `passed`.

Use a sensible numeric policy without an additional intake question. Keep
integers, identifiers, booleans, strings, timestamps, decimals, and duplicate
multiplicity exact. For floating-point results, start with small documented
per-column absolute and relative tolerances appropriate to their magnitude and
calculation, for example `abs(a-b) <= 1e-9 + 1e-9 * greatest(abs(a), abs(b))`
for ordinary finite DOUBLE aggregates. Account explicitly for NULL, NaN, and
infinities; do not allow tolerance to hide changed keys or missing rows.

For unique business keys, first prove key uniqueness and equality, then compare
floating fields by key. With duplicate keys, preserve the full row multiplicity
and validate within equivalent groups; an arbitrary row pairing is not proof.
An unchanged-vs-unchanged control helps identify pre-existing floating variation.
Record the tolerance, maximum deltas, affected rows, and comparison SQL. Hashes
can reject a candidate cheaply, but cannot prove equality. For very large
outputs, compare complete deterministic partitions with exact checks instead
of replacing equality with an unchecked checksum.

Freeze time-dependent inputs or normalize both outputs from the same stored
inputs at a common instant. If normalization or duplicate-safe numeric
comparison cannot establish equivalence, record the result as inconclusive.
Investigate real differences, especially NULL joins, tie-breaking, range
boundaries, and view-to-table changes, rather than increasing tolerance until
the candidate passes.

## 8. Evaluate the affected workload and deliver

After an isolated candidate passes correctness, test the affected query group
or dbt job if the claimed improvement concerns that workload. Preserve its
invocation, starting state, output contract, and tests. One comparable baseline
and candidate job run is the default; repeat only on request. If a job run is
not performed, label the evidence as isolated-model evidence.

Inspect changed and unchanged downstream models. A faster producer can change
physical layout, repeated work, scheduling, and which branch is critical.
Measure combined accepted suggestions rather than adding isolated savings.
Keep public mart relations available and preserve their contracts when
experimenting with materializations.

Deliver the [report](templates/report.md), all diffs, commands, profiles,
query-history exports, validation results, decisions, and artifacts for rejected
or inconclusive ideas. Separate client time, server query time, critical-path
evidence, and whole-job wall time. Explicitly record failures and skipped work;
an incomplete selector is not a successful whole-job result.

## 9. Clean up and resume safely

Put cleanup in the runner's `finally` block or exit trap. Drop only the
experiment's disposable relations and clones with its token. A slot remains
occupied until this has completed and been checked. Retained correctness tables
remain on the resource ledger until comparison and deletion are verified.

At campaign end, drop campaign-owned clones first, revoke scoped tokens, delete
slot accounts, and independently verify absence through the API. Remove only a
source share created by this campaign, after its consumers are gone. Keep
pre-existing shares. Record cleanup evidence; a driver's "cleaned" message or
a failed listing request is not independent verification. Treat authentication
and network errors differently from a verified `404`.

On interruption, preserve checkpoints and partial evidence. At the next session:

1. Read campaign, batch, experiment, and resource records before provisioning.
2. Check recorded processes and remote resources; reconcile uncertain operations.
3. Finish failed cleanup and account for every occupied slot.
4. Reuse a snapshot or workspace only if its identity and revision still match.
5. Mark incomplete commands accordingly, preserve their evidence, and resume
   from the recorded next step using a new attempt directory.

Do not blindly retry a resource-creation request after a timeout: the operation
may have succeeded. Reconcile its unique recorded name and ownership first.
Never adopt, terminate, or delete a resource whose ownership is ambiguous.
