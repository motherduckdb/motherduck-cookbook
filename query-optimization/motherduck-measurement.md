# Measuring on MotherDuck

Generate profiles from the supplied workload using a normal DuckDB client
connected to MotherDuck. Use public query history for server measurements when
available. Preserve raw evidence locally so another session can reproduce the
analysis. Apply the [methodology](methodology.md), including input warm-up and
comparable experiment batches, before interpreting runtime differences.

## Two complementary sources of evidence

Profiles explain the operators, actual cardinalities, projected fields,
predicates, and work inside a query. `query_history` records the server's query
duration, wait, spill, execution identity, and transfer volume. Neither replaces
the complete job wall time or its correctness checks.

| Signal | Use | Interpretation |
| --- | --- | --- |
| Client wall time | Measure the command or consumed query result. | Includes client orchestration and result fetching; label it as client time. |
| `execution_time` | Active server duration. | Inspect expensive operators and compare the same statement shape. |
| `wait_time` | Proxy for CPU / execution-thread contention. | Test lower concurrency or a different permitted size. Wait can also include waiting for uploaded data. |
| `bytes_spilled_to_disk` | Proxy for memory pressure and allocation contention. | Investigate intermediate width/cardinality, overlapping large operators, threads, and size. A single query can also exceed memory on its own. |
| `bytes_uploaded`, `bytes_downloaded` | Detect client/cloud data movement. | Inspect upload/download operators and query placement. |
| Operator timings and cardinalities | Locate expensive work. | Parallel operator time can exceed wall time. Do not sum inclusive subtree timings. |
| First query start to last query end | Server workload span. | Preserve exactly which statements belong to the workload. |

Treat contention indicators as hypotheses to confirm, not unique diagnoses.
Keep missing values explicitly unavailable instead of substituting zero.

## Profile one query with EXPLAIN ANALYZE

Connect using the experiment token, select its isolated clone, and execute:

```sql
EXPLAIN (ANALYZE, FORMAT JSON)
SELECT account_id, total_cents: sum(amount_cents)
FROM opt_baseline.main.events
WHERE event_kind = 'payment'
GROUP BY account_id;
```

This executes the query and returns an analyzed plan. Save the JSON result,
not a truncated terminal table or a screenshot. A Python client can retrieve it
directly:

```python
import json
import os
from pathlib import Path

import duckdb

con = duckdb.connect(
    f"md:{os.environ['MOTHERDUCK_EXPERIMENT_DATABASE']}",
    config={"motherduck_token": os.environ["MOTHERDUCK_EXPERIMENT_TOKEN"]},
)
try:
    sql = Path("optimization/compiled.sql").read_text().strip().removesuffix(";")
    result = con.execute("EXPLAIN (ANALYZE, FORMAT JSON) " + sql).fetchone()
    if result is None or result[0] != "analyzed_plan":
        raise RuntimeError("No analyzed plan returned")
    profile = json.loads(result[1])
    Path("optimization/profile.json").write_text(json.dumps(profile, indent=2))
finally:
    con.close()
```

Run Python with `uv run python ...` in the environment prepared for the customer's
client versions, adapting the invocation to select that environment as needed.
The [profile helper](scripts/profile.py) adds warm-up, unique directories,
query IDs, SQL snapshots, state transitions, and cleanup to this basic shape.
The helper's input must be a single `SELECT`; use `--materialize` to include its
CTAS writer. Its captured plan is for the CTAS, not just its input query.

For actual `INSERT`, `MERGE`, or other materializations, adapt an isolated runner
to profile the real executable SQL and target state. `EXPLAIN ANALYZE` is not a
dry run: it executes writes too. Never wrap a production-targeted statement
without first repointing its write targets.

## Capture a profile through connection settings

On the same client connection that executes the target statement:

```sql
SET enable_profiling = 'json';
SET profiling_coverage = 'ALL';
SET profiling_output = '/absolute/path/to/optimization/run-001/profile.json';

SELECT account_id, total_cents: sum(amount_cents)
FROM opt_baseline.main.events
WHERE event_kind = 'payment'
GROUP BY account_id;
```

Fully consume the result, immediately preserve the target profile, then disable
profiling with `PRAGMA disable_profiling;`. Capture any target query ID before
issuing other statements. `profiling_coverage = 'ALL'` includes materializations
such as CTAS that a SELECT-only configuration would miss. Use `profiling_mode =
'detailed'` when planning/optimizer metrics are relevant.

Each subsequently profiled statement can overwrite the output file. Use a
different path per target execution, and copy the completed target profile
before any introspection, validation, or cleanup query. The helper preserves
`profile.json` separately from its transient `profile.capture.json`.

Do not combine enabled connection profiling with `EXPLAIN ANALYZE` in the same
capture. The helper offers separate modes:

```bash
uv run python scripts/profile.py \
  --database md:opt_baseline --sql optimization/compiled.sql \
  --conditions optimization/conditions.json --mode profiling --materialize \
  --output optimization/experiment-001/connection-profile
```

On MotherDuck, inspect the complete plan for remote operators as well as the
local runner. Upload/download and hybrid-runner nodes show placement and
transfer. A local wrapper alone is not evidence about the remote scan or join.
If a client capture omits the remote operator tree, use the analyzed-plan path
and retain the limitation in the report. Check that the JSON is nonempty and
contains actual operator nodes; the helper rejects an empty operator tree.

DuckDB profile memory and temporary-directory high-water counters may cover the
instance rather than one query in isolation. Label their scope, and use query
history's query-attributed spill bytes for statement comparisons. Do not compare
a peak-directory size with cumulative bytes written as if they were the same
quantity.

## Obtain the right SQL from dbt

Work in a separate project copy and use its compatible, pinned environment.
Configure its experiment profile to read the pinned clone and write only to
experiment databases. Give every invocation distinct profiles, target, and log
directories. Never reuse one DuckDB process across account tokens.

Resolve and compile the exact workload, carrying matching variables and target
settings. For a focused full-refresh table model, the general shape is:

```bash
uv run dbt ls --select +model_name --resource-type model --output json \
  --profiles-dir /path/to/experiment-profile --target experiment
uv run dbt compile --select +model_name --full-refresh \
  --profiles-dir /path/to/experiment-profile --target experiment \
  --target-path /path/to/optimization/experiment-001/target
```

The project's environment must already contain its dbt dependencies. Build
missing ancestors in the isolated clone before profiling a compiled model. The
SQL in `target/compiled/<project>/...` is commonly the model's SELECT; use a
disposable CTAS to include its table-write cost. Actual run SQL and microbatch
compiled files can have different shapes. Use the actual per-batch artifacts
when event windows, incremental predicates, or adapter state affect compilation.

For a table-model comparison with stored outputs, the paired helper can retain
its uniquely named measured CTAS tables:

```bash
uv run python scripts/benchmark.py \
  --database md:opt_scratch \
  --baseline optimization/baseline.sql --candidate optimization/candidate.sql \
  --conditions optimization/conditions.json --materialize --keep-results \
  --output optimization/experiment-001/pair
```

This measures two compiled SELECTs as CTAS in one isolated database; it does
not execute a complete dbt invocation. Both SELECTs must read the same pinned
upstream state. Validate the retained relations using the saved names in
`run.json`, then drop those exact relations and record cleanup. For incremental
models, use sibling target states and the actual incremental operations instead.

To profile a real dbt execution through connection settings, instrumentation
must apply to every relevant adapter connection and preserve one output per
statement. A single shared `profiling_output` in `profiles.yml` will be
overwritten by model, hook, and introspection statements. Focused compiled-SQL
profiles plus unprofiled dbt runs and query history are a simpler starting path.

## Collect server measurements with query_history

The [query-history view](https://motherduck.com/docs/sql-reference/motherduck-sql-reference/md_information_schema/query_history/)
requires Business-plan access, permission to view query history (Admin and
Builder include it), and a read/write connection. A collector can use a
separate observer credential with this permission; experiment accounts do not
each need broad query-history permissions. Do not run benchmark SQL with the
observer's token.

The SQL helpers prefix statements with `md_opt_run:<id>` and a separate phase
marker. Retrieve the exact measured query ID with `MD_LAST_QUERY_ID()` on its
connection immediately after execution, before metadata or cleanup statements.
Then match those IDs or markers from the observer connection. For example:

```sql
SELECT
    query_id,
    execution_seconds: epoch(execution_time),
    wait_seconds: epoch(wait_time),
    total_elapsed_seconds: epoch(total_elapsed_time),
    bytes_spilled_to_disk,
    instance_type,
    duckling_id,
    error_type
FROM md_information_schema.query_history
WHERE query_id = '00000000-0000-0000-0000-000000000000'::UUID;
```

The duration columns are INTERVALs: `epoch(duration)` gives total seconds.
`millisecond(duration)` extracts a component, not the complete duration. Keep
the original timestamps and identities as well as derived durations.

Adapt [collect_history.sql](scripts/collect_history.sql) by setting its marker
on the same observer connection. Export results locally, for example:

```python
import json
import os
from pathlib import Path

import duckdb

con = duckdb.connect(
    f"md:{os.environ['MOTHERDUCK_OBSERVER_DATABASE']}",
    config={"motherduck_token": os.environ["MOTHERDUCK_OBSERVER_TOKEN"]},
)
try:
    con.execute("SET VARIABLE md_opt_marker = ?", ["md_opt_run:<run_id>"])
    rows = con.execute(Path("scripts/collect_history.sql").read_text())
    columns = [column[0] for column in rows.description]
    records = [dict(zip(columns, row)) for row in rows.fetchall()]
    Path("optimization/query-history.json").write_text(
        json.dumps(records, indent=2, default=str)
    )
finally:
    con.close()
```

History appears with a delay. Check expected IDs and statement counts; collect
again later rather than recording absent queries as zero-duration successes.
The [recent-queries view](https://motherduck.com/docs/sql-reference/motherduck-sql-reference/md_information_schema/recent_queries/)
can help inspect ongoing work, but preserve the final history records when
available. Save partial history and any access or collection errors too.

## Map dbt statements and measure the job

Retain `manifest.json`, `run_results.json`, compiled SQL, stdout/stderr, and
command wall time per invocation. Match physical model builds to their principal
queries using query text's dbt node IDs, dedicated account, connection, time
window, and invocation records. A model can issue multiple statements or batches.
Preserve ambiguous mappings; do not guess.

A batch marker in the isolated project's query comment makes collection easier:

```yaml
query-comment:
  comment: >-
    {"md_opt_batch":"{{ env_var('MD_OPT_BATCH', 'unlabelled') }}",
     "invocation_id":"{{ invocation_id }}",
     "node_id":"{{ node.unique_id if node is not none else 'internal' }}"}
  append: true
```

For these dbt comments, adapt the history filter to the batch and invocation
marker; remove the helper-specific `md_opt_phase:measure` predicate. Verify the
comment survives in stored query text before launching the job. Avoid including
the observer's collection queries in the workload.

Keep model build timings separate from hooks, catalog discovery, backup renames,
validation, setup, and cleanup. For a job-level span, define its statement set
explicitly and record the earliest `start_time` to latest `end_time`. Also report
the full dbt command wall time and its startup/hook overhead. Summed model or
query durations are not a concurrent job's elapsed time.

Use per-model execution start/end and dependencies to identify the weighted
critical path. Inspect the same model in baseline and candidate jobs for
downstream regressions. Lower dbt threads can reduce wait and spill; a different
Duckling size can change that balance. Test either setting deliberately within
the compute envelope, keeping the other batch conditions comparable.

Never call an invocation successful solely because its model messages look
successful. Verify process return code, selected node coverage, errors, skips,
and tests. Record known failures separately; a workload that skipped descendants
does not establish a completed whole-job improvement.

## Further reading

- [MotherDuck EXPLAIN ANALYZE](https://motherduck.com/docs/sql-reference/motherduck-sql-reference/explain-analyze/).
- [DuckDB profiling configuration](https://duckdb.org/docs/stable/configuration/pragmas.html#profiling).
- [DuckDB profile metrics](https://duckdb.org/docs/stable/dev/profiling.html).
- [MotherDuck last query ID](https://motherduck.com/docs/sql-reference/motherduck-sql-reference/connection-management/last-query-id/).
- [MotherDuck roles and permissions](https://motherduck.com/docs/concepts/roles-and-access-control/).
