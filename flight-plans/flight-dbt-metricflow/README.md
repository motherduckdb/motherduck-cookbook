---
title: Query dbt MetricFlow Metrics as a Flight
id: flight-dbt-metricflow
description: >-
  A reusable Flight that fetches a dbt + MetricFlow project from git, builds it on
  MotherDuck, and runs mf query for a metric chosen per run through Flight config.
  Each run appends the result to a snapshot table. Use when your dbt semantic model
  lives in a repo and you want one deployed Flight that answers many metric
  questions and builds a metric time series on a schedule.
type: template
category: analytics
features: [flights]
tags: [dbt, metricflow]
---

# Query dbt MetricFlow Metrics as a Flight

A single-file Flight that builds a [dbt MetricFlow](https://docs.getdbt.com/docs/build/about-metricflow)
semantic model on MotherDuck and queries it — fetching the dbt project **from git
at run time** and choosing the metric, grouping, and date window **per run through
Flight config**. Point `GIT_REPO`/`GIT_REF` at your own dbt repo to query your own
model; one deployed Flight then answers many metric questions by overriding config
when you trigger a run, no redeploy. Each run appends its result to a snapshot
table, so a scheduled Flight builds a queryable time series of metric values.

This is the Flight counterpart to the local [dbt-metricflow](../../dbt-metricflow)
example. There, `mf query` prints to your terminal; here the same semantic model
runs in a scheduled, torn-down container and its output has to land somewhere
durable.

## How it works

A Flight runs as a single `flight.py` in a fresh container. Embedding a copy of
the dbt project would drift from the canonical example, so `flight.py` **fetches
the project from git at run time** and shells out to the `dbt` and `mf` CLIs
against it:

1. Read config from the environment (Flight `config` keys arrive as env vars).
2. Connect to MotherDuck (`md:`) and `CREATE DATABASE IF NOT EXISTS` the target.
3. Fetch `REPO_SUBDIR` from `GIT_REPO`@`GIT_REF` into a temp dir — a sparse,
   blobless, shallow `git clone` (a few hundred KB), with an HTTPS tarball
   fallback if the container has no `git` binary.
4. Discover the dbt project (`dbt_project.yml`) and the profile (`profiles.yml`)
   in the checkout by globbing, so any layout works.
5. `dbt seed` and `dbt run --target motherduck` to build the models. The fetched
   project's own `profiles.yml` is used as-is; its MotherDuck path reads the
   database name from `MD_DATABASE` via dbt's `env_var()`, so nothing is written
   from scratch.
6. `mf query --metrics … --group-by … --start-time … --end-time …`, writing a CSV.
7. Append each result row to the snapshot table, tagged with `run_at` and config.

```
config (env) ── git fetch project ── dbt seed/run ── mf query ── snapshot table
  override per run    GIT_REPO@GIT_REF    build on MotherDuck     append, JSON, run_at
```

### The config-override pattern

This is the reason the Flight exists. A Flight's `config` is a `MAP(VARCHAR, VARCHAR)`
of non-secret values, injected as environment variables. You override it **per
run** without editing or re-versioning the Flight:

```sql
-- create once with default config
FROM MD_CREATE_FLIGHT(
  name := 'dbt_metricflow',
  source_code := '...flight.py...',
  requirements_txt := '...requirements.txt...',
  config := MAP {
    'METRICS': 'revenue,orders,customers',
    'GROUP_BY': 'metric_time__month',
    'START_DATE': '2024-01-01',
    'END_DATE': '2024-12-31',
    'MD_DATABASE': 'ecommerce_metrics_flight'
  }
);

-- run with a one-off override: a different metric and window, same Flight
FROM MD_RUN_FLIGHT(
  flight_id := '…',
  config := MAP {'METRICS': 'revenue_per_customer', 'START_DATE': '2024-02-01', 'END_DATE': '2024-02-29'}
);
```

The override is merged over the stored config — provided keys win, omitted keys
keep the Flight default. **Keys must already exist on the Flight**; a per-run
override changes values, it cannot introduce a new key.

### The snapshot table

`mf query` output columns change with the metric and grouping you ask for, so a
fixed-column table would break the first time someone overrides `METRICS`. Each
result row is therefore stored as a `JSON` column, keeping one table usable across
every run:

```sql
SELECT run_at, metrics, group_by, result
FROM ecommerce_metrics_flight.metric_snapshots
ORDER BY run_at DESC;

-- pull a field back out of the JSON
SELECT run_at, result->>'revenue' AS revenue
FROM ecommerce_metrics_flight.metric_snapshots
WHERE metrics = 'revenue,orders,customers';
```

Verified across two runs: a `revenue,orders,customers` run and a
`revenue_per_customer` override produce different JSON shapes yet coexist in the
one table, each tagged with the config that produced it.

### Querying your own model

The default `GIT_REPO`/`GIT_REF`/`REPO_SUBDIR` point at this cookbook's
[dbt-metricflow](../../dbt-metricflow) example so a fresh deploy runs end to end.
To query your own metrics, point these at your dbt repo. Your project needs a
`profiles.yml` with a `motherduck` target whose path resolves the database — the
example uses `path: "md:{{ env_var('MD_DATABASE', 'ecommerce_test_db') }}"`, which
the Flight feeds through `MD_DATABASE`. No code in `flight.py` changes.

## Questions to answer

- Which git repo, ref, and subdirectory hold the dbt project (your fork, or the
  default example)?
- Which metrics matter, and what dimension and date window should each run query?
- Which target database should hold the built models and the snapshot table?
- Will runs vary the metric per trigger (config override), run on a fixed
  schedule, or both?
- Does the project's time spine range cover your requested dates?

## Caveats

- **Run-time network + git.** The Flight fetches the project at run time, so the
  container needs egress to the git host. It uses `git` when present and falls
  back to the `https://<repo>/archive/<ref>.tar.gz` tarball (stdlib only) when it
  is not. A **private** repo needs an authenticated clone URL or token (use a
  Flights secret), not the public HTTPS URL.
- **`GIT_REF` is a branch or tag for the sparse clone.** The git path uses
  `--branch`, which does not accept a bare commit SHA; the tarball fallback path
  (`/archive/<ref>`) does accept a SHA. Pin to a tag for reproducible runs.
- **Override changes values, not keys.** A per-run `config` override only sets new
  values for keys that already exist on the Flight. The defaults include every key
  below; set the value you want.
- **Time-dimension queries are bounded by the spine.** The example spine generates
  `2024-01-01` to `2025-12-31`. `START_DATE`/`END_DATE` outside that window, or
  grouping by `metric_time__*` beyond it, returns no rows. Widen the spine in your
  project.
- **Build runs every time.** Each run does `dbt seed` + `dbt run` against a fresh
  checkout, so a large project makes every run slower.
- **Derived metrics reference metric names, not measures.** `revenue_per_customer`
  is `revenue / customers`, both metrics. A raw measure name in a derived `expr`
  will not resolve.
- **Keep the token out of config.** The runtime attaches a MotherDuck token and
  injects it as `MOTHERDUCK_TOKEN`; never place a token in `config`.

## What you'll adjust

Every knob is a config/env value read by `read_config()` at the top of
`flight.py`; set them as Flight config rather than by editing code. The semantic
model itself lives in the git repo you point the Flight at, not in `flight.py`.

| Config key | Default | Purpose |
|---|---|---|
| `GIT_REPO` | `…/motherduck-cookbook.git` | Repo holding the dbt project. Point at your fork. |
| `GIT_REF` | `main` | Branch or tag to fetch (tarball fallback also accepts a SHA). |
| `REPO_SUBDIR` | `dbt-metricflow` | Path within the repo to sparse-checkout. |
| `METRICS` | `revenue,orders,customers` | Comma-separated metric(s) `mf query` computes. Override per run. |
| `GROUP_BY` | `metric_time__month` | Dimension(s) to slice by, e.g. `metric_time__day`, `order_id__status`. |
| `START_DATE` | `2024-01-01` | `mf query --start-time`; must fall inside the time spine. |
| `END_DATE` | `2024-12-31` | `mf query --end-time`; must fall inside the time spine. |
| `MD_DATABASE` | `ecommerce_metrics_flight` | Target database; fed to the project's `profiles.yml` `env_var()`. Created if missing. |
| `SNAPSHOT_TABLE` | `metric_snapshots` | Append-only table of results (`run_at`, config, `result` JSON). |
| `MOTHERDUCK_TOKEN` | (Flight-injected) | Auth. Never put it in config. |

## Run it

You need a MotherDuck account and an access token. The default repo/ref make a
fresh deploy produce a successful run with no other credentials.

Smoke-test locally before deploying (this fetches the project from git, builds it
in your account, and appends one batch to `metric_snapshots`):

```bash
export MOTHERDUCK_TOKEN=your_token_here
uv run --with-requirements requirements.txt flight.py
```

Override any default inline, for example a different metric and your own repo:

```bash
METRICS=revenue_per_customer GROUP_BY=metric_time__month \
GIT_REPO=https://github.com/you/your-dbt-repo.git GIT_REF=main REPO_SUBDIR=analytics \
  uv run --with-requirements requirements.txt flight.py
```

### Deploy as a Flight

Create the Flight with `MD_CREATE_FLIGHT` (no deploy SQL is checked in; adapt the
arguments), passing:

- `name`: a Flight name, for example `dbt_metricflow`
- `source_code`: the contents of [`flight.py`](flight.py)
- `requirements_txt`: the contents of [`requirements.txt`](requirements.txt)
- `config`: `GIT_REPO`/`GIT_REF`/`REPO_SUBDIR` for your project, plus `METRICS`,
  `GROUP_BY`, `START_DATE`, `END_DATE`, `MD_DATABASE`

A MotherDuck token is attached automatically and injected at run time as
`MOTHERDUCK_TOKEN`; no token argument is needed.

Create the Flight without a schedule first, trigger one manual run with
`MD_RUN_FLIGHT(flight_id := …)` (the id is returned by `MD_CREATE_FLIGHT` and
listed by `MD_FLIGHTS()`), and confirm the database and a `metric_snapshots` row
appear. Trigger a second run with a per-run `config` override to confirm the
override reaches the query (a different metric or window shows up in the new
snapshot row). Once green, add a schedule (`0 6 * * *`, 06:00 UTC daily, is a
reasonable default) by updating `schedule_cron` with `MD_UPDATE_FLIGHT`; schedule
updates are metadata-only and do not create a new version.

## Security

- **Identifier safety.** `MD_DATABASE` and `SNAPSHOT_TABLE` flow into `CREATE`
  statements that cannot be parameterized, so each is double-quote-escaped (`_ident`)
  before any SQL runs.
- **Parameterized data.** The snapshot row (metrics, grouping, dates, and the
  result JSON) is written with bound parameters, never string-formatted into SQL.
- **Fetch a trusted ref.** The Flight runs whatever code the fetched ref contains.
  Point `GIT_REPO`/`GIT_REF` at a repo and branch/tag you control; pin a tag for
  reproducibility. A private repo's credentials belong in a Flights secret, not
  in `config`.

## Learn more

- Flight mechanics (creating, running, scheduling, secrets): the MotherDuck MCP
  `get_flight_guide` tool.
- The local, terminal-driven version of this project, with an `mf query`
  cookbook: [dbt-metricflow](../../dbt-metricflow) and its
  [EXAMPLES.md](../../dbt-metricflow/EXAMPLES.md).
- MetricFlow CLI reference: [dbt MetricFlow commands](https://docs.getdbt.com/docs/build/metricflow-commands).
- Deeper MotherDuck or DuckDB questions: the `ask_docs_question` MCP tool.
- Files in this template: [`flight.py`](flight.py) (the single-file Flight that
  fetches the project from git) and [`requirements.txt`](requirements.txt)
  (`dbt-core`, `dbt-duckdb`, `dbt-metricflow`, `duckdb`).
