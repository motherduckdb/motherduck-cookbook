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

Optimize a query, query group, or dbt project using public interfaces.
Always deliver reports, diffs, and resumable artifacts; leave original sources
unchanged and make no commits unless explicitly authorized.

## How it works

Read the [methodology](methodology.md) first. Use [measurement](motherduck-measurement.md)
to generate profiles and collect query history; consult [DuckDB patterns](duckdb-patterns.md)
only for observed bottlenecks. No existing profiles or internal tooling required.

The [helpers](scripts/) capture SQL profiles and paired timings, not resource
provisioning or campaign orchestration. Adapt an isolated runner for dbt jobs.

## Questions to answer

Discover what the project provides; clarify only missing inputs:

1. Goal: query, query group, or dbt project.
2. Workload: SQL and parameters, or project and exact dbt invocation, selectors,
   exclusions, variables, and target. Confirm dbt focus: incremental runs, full
   refreshes, initial builds/backfills, or multiple modes. Never assume full refresh.
3. Credentials: secret references with service-account/Duckling management
   capability and source-data access. Admin/Builder include platform permissions;
   verify actual capabilities and data grants.
4. Sources: databases/shares; use the same pinned snapshot for both variants.
5. Compute envelope: maximum service accounts, per-size limits, allowed query/job
   Duckling sizes, and starting dbt threads. Size and threads remain tunable.

Suggest smaller query-exploration slots and representative job-sized slots;
increase within the envelope if memory pressure makes exploration unrepresentative.

## Caveats

- `EXPLAIN ANALYZE` executes writes. Use dedicated credentials and disposable targets.
- Helpers accept one `SELECT`, optionally as CTAS; these do not measure a full
  dbt job or its actual incremental writes.
- Profile for diagnosis; measure timing without profiling. Follow methodology
  for warm-up, comparable batches, validation, and opt-in repetitions.
- `--setup` reruns per process; prepare persistent experiment inputs separately.

## What you'll adjust

| Helper option | Meaning |
| --- | --- |
| `--database` | `md:<experiment_clone>` or an isolated local DuckDB path. |
| `--sql` / `--baseline` / `--candidate` | SQL files, including compiled model SELECTs. |
| `--mode` | `explain` (default), `profiling`, or `timing`. |
| `--materialize` | Include CTAS into a unique disposable `main.__mdopt_*` table. |
| `--keep-result` / `--keep-results` | Retain CTAS outputs for validation; persistent databases only; track cleanup. |
| `--conditions` | Adapt [conditions.json](templates/conditions.json); required for MotherDuck. |
| `--inputs-read-at` | Verified full-input read timestamp; omit if unknown to trigger warm-up. |
| `--output` | New artifact directory; existing runs are never overwritten. |
| `--token-env` | Experiment token variable; default `MOTHERDUCK_EXPERIMENT_TOKEN`. |

## Run it

Use `uv` to reuse/recreate the customer's Python, DuckDB, dbt, and adapter
environment; record versions. No recipe dependency pins or original dependency
edits/upgrades. Helpers require Python 3.10+; adapt them for older runtimes.
Commands assume the prepared environment and this recipe's working directory.

Provision the isolated slot/clone, prepare conditions, and expose its token
through the environment. Profile the customer's SQL, then measure one pair:

```bash
uv run python scripts/profile.py \
  --database md:opt_baseline --sql optimization/experiment-001/baseline.sql \
  --conditions optimization/conditions.json \
  --output optimization/experiment-001/profile

uv run python scripts/benchmark.py \
  --database md:opt_baseline \
  --baseline optimization/experiment-001/baseline.sql \
  --candidate optimization/experiment-001/candidate.sql \
  --conditions optimization/conditions.json \
  --output optimization/experiment-001/pair
```

Use `--mode profiling` for connection profiles; add `--materialize` to include
table-model writes. Never use it with a read-scaling token.

## Security

Separate provisioning, experiment, and observer credentials. Keep secrets out of
SQL, artifacts, logs, and diffs. Clone only approved sources through restricted
shares; clean only campaign-owned resources. Review SQL/profile artifacts for
sensitive names and literals before sharing.

## Learn more

- [Experiment checkpoint](templates/experiment.json) and [report](templates/report.md).
- [Read-scaling benchmark](../motherduck-performance-benchmark/) for read-only load tests.
