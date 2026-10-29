# Optimization methodology

Use experiment copies only; original-source edits and commits require explicit
authorization. Deliver evidence, including rejected/inconclusive experiments.

## 1. Resolve scope

Use only the five [README inputs](README.md#questions-to-answer). Record source
revision/hash, SQL parameters/session settings, query-group order/dependencies,
client/engine/dbt/adapter versions, and region:
`SELECT region FROM md_user_info();`. Match the customer's environment.

For dbt, confirm the focus with the user: incremental runs, full refreshes,
initial builds/backfills, or multiple modes; which jobs/models? Never infer mode
from materializations or assume full refresh. Evaluate each scoped mode separately.

Save the exact command, selectors/exclusions, vars, target, and matching
`dbt ls` node IDs, dependencies, materializations, and tests. Include incremental
models when testing their full-refresh path, not unrelated repository models.
For incremental/microbatch work, preserve starting targets, windows, checkpoints,
late-arrival behavior, and actual per-batch compiled SQL. Empty/populated targets
are different workloads; `build` flags need not be valid `compile` flags.

## 2. Create the durable record before provisioning

Adapt [experiment.json](templates/experiment.json), [conditions.json](templates/conditions.json),
and [report.md](templates/report.md). Use stable campaign/batch/experiment IDs:

```text
optimization/
  campaign.json
  resources.json
  batches/<batch>.json
  experiments/<id>/
    experiment.json
    source-revision.json
    hypothesis.md
    candidate.diff
    baseline/
    candidate/
    validation/
    report.md
  report.md
```

Keep immutable source exports, including uncommitted changes; exclude secrets,
environments, and generated outputs. Save every candidate diff before execution.
Atomically checkpoint before/after each phase: inventory, provisioning, warming,
profiling, baseline, candidate, validating, decided, cleaning.

Record owner session/process, timestamps, commands/cwd/exit codes, snapshots,
resource IDs, artifact paths, errors, decisions, and next step. A stale
`running` record is incomplete until exit status/artifacts are recovered.

## 3. Isolate resources

- One service account per concurrent slot; reuse sequentially. Each experiment
  gets separate clones, code, dbt profiles/targets/logs, environments, and local
  DuckDB paths. Separate OS processes; no shared connections/writable local files.
- Claim slots atomically with session/process ownership. Never reset or clean
  another active experiment. Count occupied slots until cleanup, accounts until
  verified deletion; enforce total/per-size limits.
- Verify management capability and source grants separately. Separate provisioning,
  scoped experiment, and query-history observer credentials.

Public API base: `https://api.motherduck.com`.

| Action | Request |
| --- | --- |
| Create account | `POST /v1/users`, `{"username":"<campaign_slot>"}` |
| Mint token | `POST /v1/users/{username}/tokens`, `{"name":"<campaign>","token_type":"read_write","ttl":21600}` |
| Read/resize Ducklings | `GET` / `PUT /v1/users/{username}/instances` |
| Revoke token | `DELETE /v1/users/{username}/tokens/{token_id}` |
| Delete account | `DELETE /v1/users/{username}` |

Resize only idle slots: GET first; preserve existing settings and PUT both
configuration blocks. Example shape, not values to overwrite blindly:

```json
{"config": {
  "read_write": {"instance_size": "jumbo", "cooldown_seconds": 60},
  "read_scaling": {"instance_size": "standard", "flock_size": 0, "cooldown_seconds": 60}
}}
```

Verify returned config and actual `instance_type` in query history.
After resets such as `SHUTDOWN TERMINATE`, probe readiness and reassess warm-up.

Pin one source-share incarnation. Reuse an approved snapshot or create a
restricted manual-update share through its authorized owner; grant slot access.
With the slot token, attach and clone:

```sql
CREATE SHARE opt_source_snapshot FROM source_db
    (ACCESS RESTRICTED, UPDATE MANUAL);
-- Following statements run with the slot token:
ATTACH '<restricted-share-url>' AS opt_source;
CREATE DATABASE opt_baseline FROM opt_source;
CREATE DATABASE opt_candidate FROM opt_source;
```

Use campaign-specific names. Verify all required sources; repoint reads/writes.
Audit hard-coded qualified names, hooks, seeds, snapshots, and external effects:
an experiment dbt profile alone does not prevent production writes.

### Read scaling: standalone read-only SQL only

Not for dbt campaigns (including compiled SELECTs) or write-query optimization.
Enable/expand pools only when explicitly permitted by the compute envelope.

- Record pool size, replica sizes, concurrency, and `session_name` affinity
  mapping/absence. Keep fixed unless deliberately testing that variable.
  Include replicas in the compute envelope, not as extra uncounted slots.
- Prepare/attach inputs with a scoped read/write setup token; measure with a
  separate read-scaling token. No CTAS, `--materialize`, or writes, even for warm-up.
- Verify placement via `session_name`/`duckling_id` in history; otherwise mark
  unverified. Warm the actual serving replicas with fully consumed SELECTs.
- Replicas are eventually consistent: verify the same pinned snapshot on readers.
  Follow the public [freshness procedure](https://motherduck.com/docs/key-tasks/authenticating-and-connecting-to-motherduck/read-scaling/#ensuring-data-freshness)
  on experiment resources if needed. Reassess warm-up/comparability after replica
  or pool changes.

## 4. Warm inputs

Before measuring either variant, warm their combined relations, columns, and
ranges if any last verified read is unknown or at least 24 hours old. Cold-storage
reads can be slower; 24 hours is a conservative trigger, not cache expiry.

Use unmeasured actual read paths and consume results; disposable remote CTAS
can avoid downloading large model outputs. Metadata-only counts, LIMIT previews,
or single-column reads are insufficient. Build/warm required ancestors.
Record SQL, coverage, snapshot, slot, timestamps, and completion. Reuse warm-up
only for identical inputs/comparable conditions; reassess after input, size, or
slot changes. Exclude warm-up from measured samples.

## 5. Diagnose and experiment

Use [profiles/history](motherduck-measurement.md), then applicable
[patterns](duckdb-patterns.md). Inspect scans/pushdown, width, join fanout,
repeated work, sorts/windows, writes, wait, and spill. For dbt, reconstruct the
weighted dependency graph; target critical-path work, including setup/materialization.

Save a hypothesis, expected plan change, affected consumers, correctness checks,
and diff. Explain the experiment so the user can steer, then proceed within the
envelope; coherent combined changes are allowed. Default: one unprofiled
baseline/candidate observation from equivalent starting states. Reject clear
regressions early with evidence; still clean up.

Repeated measurements require a user request; warm-up is separate. Compare
absolute runtimes only across similar snapshots, windows, warm-up, client
environments, sizes, dbt threads, and actual batch concurrency/occupancy.
A draining batch is a changed condition; never pool unlike batches into speedups.
For size/thread experiments, change that variable only; use wait/spill as guides.
If repetitions are requested, preserve all samples and report count, median,
min/max, and variability. One sample establishes no variance/confidence.

## 6. Validate

Use freshly built equivalent inputs/starting targets, not stale production outputs.

- Match column names/order/types before comparison; implicit casts must not hide
  type changes. Match counts and duplicates using bidirectional `EXCEPT ALL`;
  see [compare_results.sql](scripts/compare_results.sql).
- Run relevant dbt data/unit/downstream tests. No selected tests means
  `not_applicable_no_selected_tests`, not passed.
- Keep non-floating values and multiplicity exact. For finite DOUBLE results,
  start with documented per-column tolerances, e.g.
  `abs(a-b) <= 1e-9 + 1e-9 * greatest(abs(a), abs(b))`; handle NULL/NaN/infinity.
  Never hide changed keys/missing rows with tolerance.
- Prove key uniqueness/equality before numeric joins; duplicate groups need
  multiplicity-safe comparison, not arbitrary pairing. Unchanged controls expose
  existing float variation. Record comparison SQL, tolerances, maximum deltas,
  and affected rows. Hashes can reject, not prove equality; large outputs may
  use complete deterministic partitions.
- Freeze/normalize time-dependent inputs identically. Investigate NULL joins,
  ties, boundaries, and materialization semantics; do not inflate tolerance to
  pass. Unproven equivalence is inconclusive.

## 7. Evaluate and report

Optimize pragmatically with profiles and targeted experiments, not exhaustive
searches or full-pipeline benchmarks after every change. A faster isolated model
can consume more memory, causing concurrent models to spill and slowing the job.
Use broader runs selectively when they could change the recommendation, especially
for resource-intensive changes or promising combinations. Distinguish model-level
evidence from verified pipeline improvements, and state which broader effects
remain untested.

When running broader checks, include unchanged concurrent/downstream models and
public mart contracts. Never add isolated savings; report scoped dbt modes separately.

Deliver the report, diffs, commands, profiles, history, validation, and decisions
for every candidate. Separate client time, server time, critical path, and job wall
time. Preserve failures/skips: useful profiles from a failed job are not successful
benchmark samples, and incomplete selectors do not establish whole-job gains.

## 8. Clean up and resume

Use `finally`/exit traps. Drop only owned disposable outputs/clones; retain output
tables on the ledger until validated/deleted. At campaign end revoke tokens,
delete accounts, then independently verify absence. Remove campaign-created source
shares only after consumers; preserve pre-existing shares. Record cleanup errors;
failed authentication/listing is not proof of deletion (unlike a verified 404).

On resume: read checkpoints, reconcile processes/resources and uncertain API
operations, finish cleanup, verify snapshot/workspace identities, then continue
the recorded next step in a new attempt directory. Do not blindly retry timed-out
creation or adopt/terminate/delete ambiguously owned resources.
