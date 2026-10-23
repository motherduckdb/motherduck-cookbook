---
title: Optimize SQL Queries and dbt Projects on MotherDuck
id: query-optimization
description: >-
  Optimize queries and dbt workloads using generated query profiles, isolated
  experiments, correctness checks, and auditable measurements. Use when a user
  supplies a query, a query group, or a dbt project to optimize on MotherDuck.
type: example
category: getting-started
features: [admin_api, shares]
tags: [dbt, python]
prompt: >-
  I want to optimize my SQL queries or dbt project on MotherDuck. Help me adapt
  the "Optimize SQL Queries and dbt Projects on MotherDuck" recipe to my own
  data and use case, using it as a guide. Generate profiles, propose and test
  changes in isolated experiments, and preserve reports, diffs, and resumable
  evidence. Leave my original source unchanged unless I explicitly request
  edits: https://motherduck.com/docs/cookbook/query-optimization
published_date: 2026-10-23
---

# Optimize SQL Queries and dbt Projects on MotherDuck

Point an agent at this recipe with a SQL query, query group, or dbt project.
It uses MotherDuck's public SQL and REST interfaces to find bottlenecks, test
rewrites, and validate their outputs. Profiles are generated from the supplied
queries; no existing profiles or internal tooling are needed.

Always deliver reports, candidate diffs, and experiment artifacts. Keep the
original source unchanged and make no commits unless explicitly instructed.

## How it works

Start with the [methodology](methodology.md). It governs the optimization loop,
isolation, warm-up, validation, experiment records, and cleanup. Consult the
[DuckDB patterns](duckdb-patterns.md) after identifying a bottleneck, and use
[measuring on MotherDuck](motherduck-measurement.md) to collect the evidence.

| Guide | Purpose |
| --- | --- |
| [Methodology](methodology.md) | Resolve the workload, propose experiments, compare results, and resume interrupted work. |
| [DuckDB patterns](duckdb-patterns.md) | Understand common opportunities in migrated SQL, model materializations, dependency graphs, and JSON handling. |
| [Measuring on MotherDuck](motherduck-measurement.md) | Generate profiles with `EXPLAIN ANALYZE` or client profiling and collect server metrics from `query_history`. |

The [helpers](scripts/) capture individual SQL profiles and paired timings. They
do not provision accounts, schedule a campaign, optimize SQL automatically, or
accept a candidate based on timing alone. For a dbt project, adapt an isolated
workspace and runner using the methodology; the [small dbt demo](demo/dbt/)
shows typed staging, dependencies, and output tests.

## Questions to answer

Only these inputs are needed; discover details from the project where possible:

1. **Goal:** optimize this query, query group, or dbt project.
2. **Workload:** SQL and parameters, or a project path and exact dbt invocation,
   including selectors, exclusions, variables, and full-refresh/incremental mode.
3. **Credentials:** an environment-variable or secret-store reference for a
   principal with permission to manage service accounts and their Ducklings,
   plus access to the relevant source data. Admin and Builder preset roles
   include these platform permissions; verify the actual API capabilities.
4. **Source data:** databases or shares, supplied directly or resolved from
   project configuration. Pin the same input snapshot for both variants.
5. **Compute envelope:** maximum concurrent service accounts and permitted
   sizes for query experiments and workload benchmarks. Include starting dbt
   threads if known. Both threads and size can be optimized within these limits.

Suggest smaller Ducklings for single-query exploration and representative sizes
for workload measurements. A query that spills heavily on the smaller size may
need a larger development slot. Use the same size for each baseline/candidate
pair unless size is the deliberate experiment variable.

## Caveats

- `EXPLAIN ANALYZE` executes its statement. Profile writes and run dbt only in
  disposable experiment databases with dedicated experiment credentials.
- Warm all relevant inputs before measurement when their last verified read
  was at least 24 hours ago or is unknown. Warm-up is unmeasured; repeat sampling
  is performed only when requested. Compare batches with similar conditions.
- The SQL helpers accept one `SELECT`, optionally measured as a disposable
  CTAS. Profile actual incremental writes and full dbt commands through an
  adapted isolated runner; a `SELECT` alone does not measure their write cost.
- `--setup` executes before each helper process. Use it for the in-memory demo;
  for persistent experiment databases, prepare inputs separately and omit it.
- Client profiling and `EXPLAIN ANALYZE` produce evidence for diagnosis. Use
  separate, unprofiled runs for clean timing observations.
- `query_history` requires the Business plan and permission to view query
  history, included in Admin and Builder. It also requires a read/write
  connection. When unavailable, keep using profiles and clearly label client
  timings; leave missing server metrics unavailable.
- Single-query gains can disappear or reverse inside a dbt job. A whole-job
  improvement requires a comparable job-level measurement.

## What you'll adjust

| Setting | Purpose | Example |
| --- | --- | --- |
| `--database` | Isolated database for the SQL helpers. | `:memory:` for the demo; `md:opt_candidate` for an experiment clone. |
| `--sql`, `--baseline`, `--candidate` | Single-query inputs or compiled dbt `SELECT`s. | `optimization/experiment-001/candidate.sql` |
| `--mode` | Profile format or unprofiled timing. | `explain` (default), `profiling`, `timing` |
| `--materialize` | Include CTAS writes into a unique disposable `main.__mdopt_*` table. | Use for table-model diagnosis. |
| `--keep-result` / `--keep-results` | Retain measured CTAS outputs for correctness validation. | Persistent experiment databases only; record and clean retained relations. |
| `--conditions` | Actual batch conditions saved alongside measurements. | Adapt [conditions.json](templates/conditions.json); required for MotherDuck. |
| `--inputs-read-at` | Timestamp of a verified read of all relevant inputs. | Omit when unknown; the helper warms inputs automatically. |
| `--output` | New artifact directory; existing runs are never overwritten. | `optimization/experiment-001/baseline-profile` |
| `--token-env` | Experiment token environment-variable name. | `MOTHERDUCK_EXPERIMENT_TOKEN` (default) |
| Compute envelope | Account slots and allowed sizes for experiments and jobs. | Record in [experiment.json](templates/experiment.json). |

## Run it

Let the agent inspect the customer's Python, DuckDB client, dbt, and adapter
versions, then reuse that environment or recreate it in an isolated experiment
workspace. Configure missing dependencies there using `uv` and record resolved
versions. This recipe supplies no dependency manifest or version pins. Do not
upgrade clients or edit the original project's dependency files just to run it.

Commands below assume that environment is ready and run from this recipe's
directory; adapt the `uv` invocation to select the prepared environment. The
helpers require Python 3.10+; adapt them if the customer's runtime is older.

Generate a JSON operator profile using only synthetic local data:

```bash
uv run python scripts/profile.py \
  --database :memory: --setup demo/setup.sql --sql demo/baseline.sql \
  --materialize --output optimization/demo/baseline-profile
```

Try connection-level profiling with a different artifact directory:

```bash
uv run python scripts/profile.py \
  --database :memory: --setup demo/setup.sql --sql demo/candidate.sql \
  --mode profiling --materialize --output optimization/demo/candidate-profile
```

Capture one unprofiled pair after warm-up:

```bash
uv run python scripts/benchmark.py \
  --database :memory: --setup demo/setup.sql \
  --baseline demo/baseline.sql --candidate demo/candidate.sql \
  --output optimization/demo/pair
```

This demo compares recurring consumers of JSON text and typed staging columns.
Its setup creates the typed staging table before measurement. Include the cost
of that staging table when measuring a full pipeline; the consumer comparison
alone does not establish a whole-project improvement. The small dataset teaches
the mechanics rather than predicting a production speedup.

Build the synthetic dbt project locally, including its output-equivalence test:

```bash
mkdir -p optimization
uv run dbt build --project-dir demo/dbt --profiles-dir demo/dbt --target local
```

For MotherDuck, prepare an experiment slot and clone using the methodology,
adapt the conditions file, and supply its token through the environment. For
example, profile an already compiled model against that clone:

```bash
uv run python scripts/profile.py \
  --database md:opt_baseline --sql optimization/experiment-001/compiled.sql \
  --conditions optimization/conditions.json --materialize \
  --output optimization/experiment-001/baseline-profile
```

Use [compare_results.sql](scripts/compare_results.sql) for stored outputs and
[collect_history.sql](scripts/collect_history.sql) for measured query records.
Both files explain their connection variables. Finish the
[report template](templates/report.md) with correctness, decisions, and cleanup.

## Security

Keep the provisioning credential separate from experiment tokens. Use it for
resource administration and the necessary source-share lifecycle; execute model
work with the slot's scoped token. Keep secret values outside the artifact
directory and never place them in SQL, profiles committed to git, diffs, or logs.

Only clone approved sources. Use restricted snapshot shares and verify access
as each experiment account. Track and remove only resources created by this
campaign. SQL text and profiles can contain sensitive names and literal values;
review artifacts before distributing them.

## Learn more

- [Experiment checkpoint](templates/experiment.json) and [report template](templates/report.md).
- [MotherDuck query-performance guide](https://motherduck.com/docs/key-tasks/query-performance/).
- [MotherDuck query history](https://motherduck.com/docs/sql-reference/motherduck-sql-reference/md_information_schema/query_history/).
- [DuckDB profiling](https://duckdb.org/docs/stable/dev/profiling.html).
- [Public service-account lifecycle](https://motherduck.com/docs/key-tasks/service-accounts-guide/create-and-configure-service-accounts/).
- [Benchmark read scaling](../motherduck-performance-benchmark/) for a separate read-scaling load test.
- For client-specific questions, the MotherDuck MCP server's `ask_docs_question`
  tool can retrieve documentation. This workflow does not depend on MCP.
