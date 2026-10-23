# Measuring on MotherDuck

Use generated operator profiles for diagnosis, public query history for server
measurements, and command wall time for job claims. Apply the [methodology](methodology.md).

## Read the signals

| Signal | Interpretation |
| --- | --- |
| Client wall time | Includes orchestration and consumed result fetching. |
| `execution_time` | Active server duration; compare equivalent statement shapes. |
| `wait_time` | CPU/execution-thread contention proxy; may also include upload waits. |
| `bytes_spilled_to_disk` | Memory/allocation-contention proxy; one query can also exceed memory alone. |
| `bytes_uploaded` / `bytes_downloaded` | Client/cloud movement; inspect placement and transfer operators. |
| Operator time/cardinality | Locate work; parallel/inclusive times must not be summed as wall time. |
| First start to last end | Server span of an explicitly defined workload, not complete job wall time. |

Proxies are hypotheses, not unique diagnoses. Missing metrics are unavailable,
not zero. Profile memory/temp-directory peaks may cover the instance; distinguish
them from query-attributed spill and cumulative bytes written.

## Capture profiles

`EXPLAIN ANALYZE` executes SQL, including writes; repoint targets before profiling.

```sql
EXPLAIN (ANALYZE, FORMAT JSON)
SELECT account_id, total_cents: sum(amount_cents)
FROM opt_baseline.main.events
WHERE event_kind = 'payment'
GROUP BY account_id;
```

Save the full analyzed-plan JSON, not terminal truncation. Alternatively, configure
the connection executing the target:

```sql
SET enable_profiling = 'json';
SET profiling_coverage = 'ALL';
SET profiling_output = '/absolute/path/to/optimization/run-001/profile.json';
```

Execute/consume the target, preserve its profile before any other statement,
then disable with `PRAGMA disable_profiling;`. Use unique files per execution:
introspection/hooks overwrite shared output. `ALL` includes CTAS; use
`profiling_mode = 'detailed'` for optimizer/planning metrics. Do not combine
connection profiling and EXPLAIN in one capture.

[profile.py](scripts/profile.py) implements separate `explain`, `profiling`, and
`timing` modes, warm-up, SQL snapshots, IDs, checkpoints, and cleanup.
See [commands/options](README.md#run-it). Input: one SELECT; `--materialize`
profiles its CTAS writer too. Real INSERT/MERGE/materializations need an adapted
isolated runner. Time without profiling.

Require nonempty operator JSON, including remote work. A local hybrid wrapper
does not explain remote scans/joins; inspect upload/download placement. If client
profiling omits remote operators, try EXPLAIN and record remaining limitations.

## Capture the actual dbt workload

Use the customer's environment, isolated sources/outputs, and distinct profiles,
target/log directories, and processes per account.

- Resolve/compile the confirmed scope with matching selectors, vars, and target;
  build missing ancestors in the clone first.
- `target/compiled/<project>/...` commonly contains SELECTs, not actual
  materializations. CTAS includes table writes; actual run/per-batch SQL is
  required for incremental predicates, event windows, and state-dependent behavior.
- [benchmark.py](scripts/benchmark.py) with `--materialize --keep-results`
  retains unique CTAS tables in a persistent experiment database. Both inputs
  must read identical upstream state. Validate recorded `run.json` relations,
  then delete them. This is not a full dbt invocation; incremental comparisons
  require sibling starting targets and actual incremental operations.
- Full dbt profiling must instrument each adapter connection and preserve every
  target statement's profile. One shared `profiling_output` is overwritten.
  Start with focused compiled-SQL profiles, unprofiled jobs, and query history.

## Collect query history

Requires Business-plan access, View query history permission (Admin/Builder),
and a read/write connection. Use a separate observer credential, not the measured
workload token. Without access, retain profiles/client timings; mark server
metrics unavailable.

Immediately after the target, preserve its profile and retrieve
`MD_LAST_QUERY_ID()` before metadata/cleanup. Helpers also tag SQL with
`md_opt_run:<id>` and phase markers.

```sql
SET VARIABLE md_opt_marker = 'md_opt_run:<run_id>';
```

Run [collect_history.sql](scripts/collect_history.sql) on that observer connection;
export full rows locally. INTERVAL durations need `epoch(duration)` for total
seconds; `millisecond(duration)` returns only a component. Retain raw timestamps,
identities, and errors.

History lags: reconcile expected IDs/counts, save partial/error evidence, and
collect again later. Never treat absent records as zero-duration successes.
`md_information_schema.recent_queries` can expose ongoing work; preserve final
history when available.

## Attribute dbt statements and job time

Save manifest/run-results JSON, compiled SQL, stdout/stderr, exit code, and command
wall time. Match queries using dbt node/invocation IDs, account, connection, and
time window; retain ambiguity. Models can issue multiple statements/batches.

In the isolated project's query comment:

```yaml
query-comment:
  comment: >-
    {"md_opt_batch":"{{ env_var('MD_OPT_BATCH', 'unlabelled') }}",
     "invocation_id":"{{ invocation_id }}",
     "node_id":"{{ node.unique_id if node is not none else 'internal' }}"}
  append: true
```

Verify tags survive before running. Filter history by batch/invocation; remove
the helper-only `md_opt_phase:measure` predicate and exclude observer queries.

Separate principal builds from hooks, discovery, backup renames, validation,
setup, and cleanup. Report server span, full command wall time/overhead, and
weighted dependency critical path; summed model durations are not job elapsed
time. Check downstream regressions. Verify exit code, selected-node coverage,
errors, skips, and tests, including pre-existing failures.
