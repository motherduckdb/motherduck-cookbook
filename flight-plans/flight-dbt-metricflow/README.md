---
title: Query dbt MetricFlow Metrics as a Flight
id: flight-dbt-metricflow
description: >-
  A reusable Flight that bundles a dbt + MetricFlow semantic model, builds it on
  MotherDuck, and runs mf query for a metric chosen per run through Flight config.
  Each run appends the result to a snapshot table. Use when you want one deployed
  Flight that answers many metric questions and builds a metric time series on a
  schedule.
type: template
category: analytics
features: [flights]
tags: [dbt, metricflow]
---

# Query dbt MetricFlow Metrics as a Flight

A single-file Flight that defines metrics once with [dbt MetricFlow](https://docs.getdbt.com/docs/build/about-metricflow)
and queries them on MotherDuck — with the metric, grouping, and date window
chosen **per run through Flight config**. One deployed Flight answers many metric
questions: override `METRICS`, `GROUP_BY`, `START_DATE`, or `END_DATE` when you
trigger a run and the same semantic model serves a different query, no redeploy
and no project clone per variant. Each run appends its result to a snapshot
table, so a scheduled Flight builds a queryable time series of metric values.

This is the Flight counterpart to the local [dbt-metricflow](../../dbt-metricflow)
example. There, `mf query` prints to your terminal; here the same semantic model
runs in a scheduled, torn-down container and its output has to land somewhere
durable.

## How it works

A Flight runs as a single `flight.py` in a fresh container, but a dbt project is
many files. So `flight.py` **embeds the dbt + MetricFlow project as string
constants** and materializes it to a temp working directory at run time, then
shells out to the `dbt` and `mf` CLIs:

1. Read config from the environment (Flight `config` keys arrive as env vars).
2. Connect to MotherDuck (`md:`) and `CREATE DATABASE IF NOT EXISTS` the target.
3. Write the embedded project to a temp dir and render `profiles.yml` with the
   target database, pointing dbt and `mf` at the `motherduck` target.
4. `dbt seed` and `dbt run` to build the orders fact table, time spine, and
   semantic model.
5. `mf query --metrics … --group-by … --start-time … --end-time …`, writing a CSV.
6. Append each result row to the snapshot table, tagged with `run_at` and the
   exact config used.

```
config (env) -> embedded dbt project -> dbt seed/run -> mf query -> snapshot table
   override per run                       build on MotherDuck      append, tagged with run_at
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
    'END_DATE': '2024-12-31'
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
result row is therefore stored as a `JSON` column, keeping one table usable
across every run:

```sql
SELECT run_at, metrics, group_by, result
FROM ecommerce_metrics_flight.metric_snapshots
ORDER BY run_at DESC;

-- pull a field back out of the JSON
SELECT run_at, result->>'revenue' AS revenue
FROM ecommerce_metrics_flight.metric_snapshots
WHERE metrics = 'revenue,orders,customers';
```

### The semantic model

The embedded project mirrors the local example: an `orders` semantic model over a
20-row seed (`fct_orders`) with measures (`total_revenue`, `order_count`,
`unique_customers`, …) and metrics (`revenue`, `orders`, `customers`,
`avg_order_value`, and the derived `revenue_per_customer`). Edit the
`SEMANTIC_MODELS_YML`, `FCT_ORDERS_SQL`, and `RAW_ORDERS_CSV` constants in
`flight.py` to model your own data; point `FCT_ORDERS_SQL` at an existing table
instead of the seed for a real fact source.

## Questions to answer

- Which metrics matter, and what dimension and date window should each run query?
- What is the source fact table and its grain — the bundled seed, or an existing
  MotherDuck table you point `fct_orders` at?
- Which target database should hold the built models and the snapshot table?
- Will runs vary the metric per trigger (config override), run on a fixed
  schedule, or both?
- Does the time spine range (`2024-01-01` to `2025-12-31`) cover your dates?

## Caveats

- **Override changes values, not keys.** A per-run `config` override only sets new
  values for keys that already exist on the Flight. To query a metric the Flight
  was not created with, the keys (`METRICS`, …) must exist — they do by default;
  set the metric name as the value.
- **Time-dimension queries are bounded by the spine.** The embedded
  `metricflow_time_spine` generates dates from `2024-01-01` to `2025-12-31`.
  `START_DATE`/`END_DATE` outside that window, or grouping by `metric_time__*`
  beyond it, returns no rows. Widen the `generate_series` range in `TIME_SPINE_SQL`.
- **Build runs every time.** Each run does `dbt seed` + `dbt run` before querying,
  because the container is fresh. For the small bundled project this is fast; a
  large project makes every run slower.
- **`mf query` must produce a CSV.** A misspelled metric or dimension makes `mf`
  exit non-zero (the Flight fails) or write nothing; check the metric/group-by
  names against the semantic model.
- **Derived metrics reference metric names, not measures.** `revenue_per_customer`
  is `revenue / customers`, both metrics. A raw measure name in a derived `expr`
  will not resolve.
- **Keep the token out of config.** The runtime attaches a MotherDuck token and
  injects it as `MOTHERDUCK_TOKEN`; never place a token in `config`.

## What you'll adjust

Every knob is a config/env value read by `read_config()` at the top of
`flight.py`; set them as Flight config rather than by editing code. The semantic
model itself lives in the embedded constants you edit once for your data.

| Config key | Default | Purpose |
|---|---|---|
| `METRICS` | `revenue,orders,customers` | Comma-separated metric(s) `mf query` computes. Override per run. |
| `GROUP_BY` | `metric_time__month` | Dimension(s) to slice by, e.g. `metric_time__day`, `order_id__status`. |
| `START_DATE` | `2024-01-01` | `mf query --start-time`; must fall inside the time spine. |
| `END_DATE` | `2024-12-31` | `mf query --end-time`; must fall inside the time spine. |
| `TARGET_DATABASE` | `ecommerce_metrics_flight` | MotherDuck database the models and snapshot table are built into. Created if missing. |
| `SNAPSHOT_TABLE` | `metric_snapshots` | Append-only table of results (`run_at`, config, `result` JSON). |
| `MOTHERDUCK_TOKEN` | (Flight-injected) | Auth. Never put it in config. |

Edit these constants in `flight.py` to change the model itself:

| Constant | Purpose |
|---|---|
| `SEMANTIC_MODELS_YML` | Entities, dimensions, measures, metrics — the source of truth. |
| `FCT_ORDERS_SQL` | The fact model; point it at an existing table for a real source. |
| `RAW_ORDERS_CSV` | The bundled seed so a fresh deploy runs end to end. |
| `TIME_SPINE_SQL` | Date spine backing time dimensions; widen the range as needed. |

## Run it

You need a MotherDuck account and an access token. The bundled seed makes a fresh
deploy produce a successful run with no other credentials.

Smoke-test locally before deploying:

```bash
export MOTHERDUCK_TOKEN=your_token_here
uv run --with-requirements requirements.txt flight.py
```

That builds `ecommerce_metrics_flight` in your account and appends one batch of
results to `metric_snapshots`. Override any default inline, for example:

```bash
METRICS=revenue_per_customer GROUP_BY=metric_time__month \
  uv run --with-requirements requirements.txt flight.py
```

### Deploy as a Flight

Create the Flight with `MD_CREATE_FLIGHT` (no deploy SQL is checked in; adapt the
arguments), passing:

- `name`: a Flight name, for example `dbt_metricflow`
- `source_code`: the contents of [`flight.py`](flight.py)
- `requirements_txt`: the contents of [`requirements.txt`](requirements.txt)
- `config`: `METRICS`, `GROUP_BY`, `START_DATE`, `END_DATE` and any other key
  from [What you'll adjust](#what-youll-adjust) you want to override

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

- **Identifier safety.** `TARGET_DATABASE` and `SNAPSHOT_TABLE` flow into `CREATE`
  statements that cannot be parameterized, so each is double-quote-escaped (`_ident`)
  before any SQL runs.
- **Parameterized data.** The snapshot row (metrics, grouping, dates, and the
  result JSON) is written with bound parameters, never string-formatted into SQL.
- **Config is not secret.** Flight config is for non-secret values only; metric
  names and dates are safe to put there. The token stays Flight-injected.

## Learn more

- Flight mechanics (creating, running, scheduling, secrets): the MotherDuck MCP
  `get_flight_guide` tool.
- The local, terminal-driven version of this project, with an `mf query`
  cookbook: [dbt-metricflow](../../dbt-metricflow) and its
  [EXAMPLES.md](../../dbt-metricflow/EXAMPLES.md).
- MetricFlow CLI reference: [dbt MetricFlow commands](https://docs.getdbt.com/docs/build/metricflow-commands).
- Deeper MotherDuck or DuckDB questions: the `ask_docs_question` MCP tool.
- Files in this template: [`flight.py`](flight.py) (the single-file Flight with
  the embedded dbt project) and [`requirements.txt`](requirements.txt)
  (`dbt-core`, `dbt-duckdb`, `dbt-metricflow`, `duckdb`).
