---
title: Define and Query Metrics with dbt MetricFlow on MotherDuck
id: dbt-metricflow
description: >-
  A dbt project that defines a semantic layer with MetricFlow over an orders
  fact table, then queries the metrics with the mf CLI against local DuckDB or
  MotherDuck. Use when you want one metric definition (revenue, order count,
  derived ratios) that runs identically on a local file and in the cloud.
type: example
features: []
tags: [dbt, metricflow]
---

# Define and Query Metrics with dbt MetricFlow on MotherDuck

This example defines metrics once in a dbt semantic model and queries them with the MetricFlow `mf` CLI. The same project runs against a local DuckDB file or MotherDuck just by switching the dbt target, because dbt-duckdb uses the same code path for both. It shows the MotherDuck pattern of writing portable dbt models and a portable semantic layer, then promoting from local to cloud with one environment change. MetricFlow ships a DuckDB SQL renderer, so DuckDB and MotherDuck are both fully supported.

## How it works

`mf query` reads the metrics and dimensions in `models/semantic_models.yml`, renders DuckDB SQL, and runs it through the dbt-duckdb adapter against whichever target you pick. MotherDuck uses the same connection path as local DuckDB with a different connection string (`md:<db>`), so the rendered SQL does not change.

```
Your Query -> MetricFlow -> dbt-duckdb adapter -> DuckDB / MotherDuck
                  |
            Generates DuckDB SQL
```

Note the split responsibility: `dbt seed` / `dbt run` select their target with the `--target` flag, while the `mf` CLI selects its target from the `DBT_TARGET` env var. To run the whole flow on MotherDuck you set both, as the MotherDuck block above shows.

Inspect the SQL MetricFlow generates with `--explain`:

```bash
DBT_PROFILES_DIR=.. mf query --metrics revenue --group-by order_id__status --explain
# SELECT
#   status AS order_id__status
#   , SUM(amount) AS revenue
# FROM "ecommerce_local"."main"."fct_orders" orders_src_10000
# GROUP BY status
```

### The semantic model

`models/semantic_models.yml` is the single source of truth. The `orders` semantic model wraps `fct_orders` and declares:

- **entities**: `order_id` (primary), `customer` (foreign, from `customer_id`).
- **dimensions**: `order_date` (time, day grain), `status` (categorical).
- **measures**: `order_count` (count), `total_revenue` (sum of `amount`), `average_order_value` (average), `unique_customers` (count_distinct of `customer_id`).
- **metrics**: `revenue`, `orders`, `customers`, `avg_order_value` (all simple, wrapping a measure), plus `revenue_per_customer`, a derived metric defined as `revenue / customers`.

The fact table itself is intentionally thin (`models/fct_orders.sql` casts `order_date` to a `DATE` and passes the seed columns through), so the interesting logic lives in the YAML, not the SQL.

### Adding a custom metric

Edit `models/semantic_models.yml` to add a measure and a metric, for example a cancellation rate:

```yaml
# Add under measures:
  - name: cancelled_orders
    agg: sum
    expr: "CASE WHEN status = 'cancelled' THEN 1 ELSE 0 END"

# Add under metrics:
  - name: cancellation_rate
    type: derived
    label: Order Cancellation Rate
    type_params:
      expr: cancelled_orders / orders
      metrics:
        - cancelled_orders
        - orders
```

Then rebuild and query:

```bash
DBT_PROFILES_DIR=.. uv run dbt run
DBT_PROFILES_DIR=.. uv run mf query --metrics cancellation_rate
```

## Questions to answer

- What is the source fact table and its grain (one row per order, event, session)?
- Which metrics matter (sums, counts, distinct counts, derived ratios) and what are their labels?
- What time grain and date range should the time spine cover?
- Run against local DuckDB only, MotherDuck only, or both (promote local to cloud)?
- For MotherDuck: which database name, and is a MotherDuck token available?

## Caveats

- **Build before you query.** `mf query` reads materialized tables, not the YAML alone. Run `dbt seed` and `dbt run` first, or you get empty or error results. After changing `semantic_models.yml`, re-run `dbt run`.
- **`DBT_PROFILES_DIR=..` is mandatory** when running from `ecommerce_metrics/`, because `profiles.yml` lives one level up in `metricflow-example/`. Without it dbt looks in `~/.dbt/` and fails to find the profile.
- **dbt and `mf` pick targets differently.** `dbt` uses `--target motherduck`; the `mf` CLI ignores `--target` and reads `DBT_TARGET`. Setting only one runs half your flow against the wrong database, often silently.
- **Time-dimension queries are bounded by the spine.** `metricflow_time_spine.sql` only generates dates from `2024-01-01` to `2025-12-31`. Grouping by `metric_time__*` outside that window returns no rows. Widen the `generate_series` range to query other periods.
- **Only the `day` grain is declared.** The time spine and `dbt_project.yml` define a `day` granularity; `metric_time__month`, `__week`, and `__year` roll up from it. If you need a coarser native grain you must add it to the spine config.
- **MotherDuck token env var naming.** dbt-duckdb accepts `MOTHERDUCK_TOKEN` or `motherduck_token`. The token is read from the environment, not from `profiles.yml` (do not paste secrets into the profile). A missing or invalid token fails at connection time, not at parse time.
- **Create the MotherDuck database first.** `md:ecommerce_test_db` must exist before `dbt run` writes into it; the one-time `CREATE DATABASE` step above handles this. dbt will not create the database for you.
- **Derived metrics reference metric names, not measures.** `revenue_per_customer` uses `revenue / customers`, both of which are metrics. Referencing a raw measure name in a derived `expr` will not resolve. Metric declaration order in the file does not matter; MetricFlow resolves the whole graph.

## What you'll adjust

| Setting | Purpose | Options / example |
| --- | --- | --- |
| `metricflow-example/profiles.yml` `local.path` | Local DuckDB file the project builds into | `ecommerce_local.duckdb` (default) |
| `metricflow-example/profiles.yml` `motherduck.path` | MotherDuck database name to build into | `md:ecommerce_test_db`, change to your own `md:<db>` |
| `MOTHERDUCK_TOKEN` (or `motherduck_token`) env var | Auth for the MotherDuck target | your MotherDuck access token |
| `DBT_TARGET` env var | Selects which `profiles.yml` output the `mf` CLI uses | unset (uses `local`) or `motherduck` |
| `DBT_PROFILES_DIR` env var | Where dbt finds `profiles.yml` | `..` when run from `ecommerce_metrics/` |
| `mf query --metrics` | Which metric(s) to compute | `revenue`, `orders`, `customers`, `avg_order_value`, `revenue_per_customer` |
| `mf query --group-by` | Dimension(s) to slice by | `metric_time__month`, `metric_time__day`, `order_id__status` |
| `models/semantic_models.yml` | Semantic model: entities, dimensions, measures, metrics | add/edit measures and metrics here to define new KPIs |
| `models/fct_orders.sql` + `seeds/raw_orders.csv` | The fact table and its source rows | swap the seed and fact model for your own grain/columns |
| `models/metricflow_time_spine.sql` | Date spine backing time dimensions | `generate_series` range, currently `2024-01-01` to `2025-12-31` |

## Run it

Prerequisites: Python with `uv`, and (for the cloud target) a MotherDuck account plus token from the [MotherDuck UI](https://app.motherduck.com) under Settings, Access Tokens.

Install dependencies from `requirements.txt` (`dbt-core`, `dbt-duckdb`, `dbt-metricflow`):

```bash
cd dbt-metricflow
uv venv
uv pip install -r requirements.txt
```

Local DuckDB, run from `metricflow-example/ecommerce_metrics/`:

```bash
cd metricflow-example/ecommerce_metrics
DBT_PROFILES_DIR=.. uv run dbt seed
DBT_PROFILES_DIR=.. uv run dbt run
DBT_PROFILES_DIR=.. uv run mf query --metrics revenue,orders,customers --group-by metric_time__month
```

Expected output (against the bundled 20-row seed):

```
metric_time__month      revenue    orders    customers
--------------------  ---------  --------  -----------
2024-01-01T00:00:00     1962.29        10            6
2024-02-01T00:00:00     2621.73        10           10
```

MotherDuck, same project with the cloud target:

```bash
export MOTHERDUCK_TOKEN='your_token_here'
# First time only: create the target database
uv run python -c "import duckdb; duckdb.connect('md:').execute('CREATE DATABASE ecommerce_test_db')"

DBT_PROFILES_DIR=.. uv run dbt seed --target motherduck
DBT_PROFILES_DIR=.. uv run dbt run --target motherduck
DBT_PROFILES_DIR=.. DBT_TARGET=motherduck uv run mf query --metrics revenue,orders,customers --group-by metric_time__month
```

The generated SQL is identical for both targets. MotherDuck's hybrid execution decides where the work runs.

## Files

- [`requirements.txt`](requirements.txt): Python deps to install (`dbt-core`, `dbt-duckdb`, `dbt-metricflow`), pinned to the 1.8+ line; pulls in `duckdb` and `metricflow` transitively.
- [`EXAMPLES.md`](EXAMPLES.md): a cookbook of `mf query` patterns (single/multiple metrics, time ranges, status breakdowns, multiple dimensions, weekly reports, derived metrics, `mf list`, `--explain`, exporting results) with expected output for each.
- [`metricflow-example/profiles.yml`](metricflow-example/profiles.yml): the dbt profile with two outputs, `local` (DuckDB file) and `motherduck` (`md:ecommerce_test_db`); the MotherDuck token is read from the environment, not stored here.
- [`metricflow-example/ecommerce_metrics/`](metricflow-example/ecommerce_metrics/): the dbt project (models, seed, semantic model, config), described below.
- [`metricflow-example/ecommerce_metrics/dbt_project.yml`](metricflow-example/ecommerce_metrics/dbt_project.yml): dbt project config; materializes models as tables and wires the MetricFlow time spine (`metricflow_time_spine`, `day` granularity).
- [`metricflow-example/ecommerce_metrics/models/`](metricflow-example/ecommerce_metrics/models/): three files, `fct_orders.sql` (thin orders fact table over the seed), `metricflow_time_spine.sql` (date spine backing time dimensions), and `semantic_models.yml` (the semantic model: entities, dimensions, measures, metrics).
- [`metricflow-example/ecommerce_metrics/seeds/raw_orders.csv`](metricflow-example/ecommerce_metrics/seeds/raw_orders.csv): the 20-row source order data loaded by `dbt seed`.
- [`metricflow-example/ecommerce_metrics/README.md`](metricflow-example/ecommerce_metrics/README.md): the default dbt starter README (unmodified boilerplate).
- `metricflow-example/ecommerce_metrics/` also holds the standard dbt scaffold dirs (`analyses/`, `macros/`, `snapshots/`, `tests/`), each with a `.gitkeep` placeholder and otherwise empty.

## Learn more

- [EXAMPLES.md](EXAMPLES.md): many more `mf query` patterns (single/multiple metrics, time ranges, status breakdowns, multiple dimensions, weekly reports, `mf list metrics`, exporting results, `--explain` to see the SQL).
- [models/semantic_models.yml](metricflow-example/ecommerce_metrics/models/semantic_models.yml): the full semantic model (entities, dimensions, measures, metrics) to copy from.
- For deeper MotherDuck or DuckDB questions (connection strings, hybrid execution, SQL behavior), use the `ask_docs_question` MCP tool or see the [MotherDuck docs](https://motherduck.com/docs).
- MetricFlow CLI reference: [dbt MetricFlow commands](https://docs.getdbt.com/docs/build/metricflow-commands).
