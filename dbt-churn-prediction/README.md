---
title: Build Churn Prediction Features with dbt
id: dbt-churn-prediction
description: >-
  dbt builds customer churn feature and label tables from raw customer,
  membership, usage, and payment history, plus a Python script that trains and
  scores a churn model on top. Use when you need a SQL-first churn feature
  pipeline on MotherDuck, with model training kept as a separate step.
type: example
category: analytics
features: []
tags: [dbt, python, scikit-learn]
---

# Build Churn Prediction Features with dbt

This example splits churn prediction into two jobs: a dbt project that turns raw
customer history into a point-in-time feature matrix, churn labels, and a
warehouse-side daily score table, and a Python script that trains and calibrates
a scikit-learn model on those tables. The MotherDuck pattern it shows is a
SQL-first feature pipeline you build once and refresh (locally or in MotherDuck),
with model training as a downstream workflow rather than something baked into the
warehouse.

The order most teams actually use it: build a dataset, train a model, then
predict churn for the current customer population. The bundled IBM Telco dataset
lets the Python step run immediately; swap in your own history once you have
enough of it.

## How it works

The dbt project shapes raw history into a training dataset, not just a report.
That ordering is the whole point: features come from a snapshot date, labels come
from the future, and that time split is what makes the training setup valid.

- **Staging** (`models/staging/`, materialized as views): `stg_customers`,
  `stg_memberships`, `stg_payments`, `stg_usage_events` clean the raw seeds.
- **Marts** (`models/marts/`, materialized as tables, schema `analytics`):
  - `fct_customer_features_historical` is the **training input**: one row per
    customer per historical snapshot date, with the columns that were known on
    that date.
  - `fct_customer_churn_labels` is the **training target**: whether that same
    customer churned after the snapshot date, gated by `is_eligible_for_label`.
  - `fct_customer_features_daily` and `fct_customer_churn_scores_daily` produce
    the current-day feature rows and the warehouse-side baseline score for the
    current eligible population.
  - `fct_subscription_history`, `fct_churn_segment_rates`, and
    `mart_retention_queue_daily` support survival analysis and an actionable
    retention queue.
- Feature logic lives in `macros/churn_features.sql`. The historical snapshot
  dates are defined there in `churn_label_dates()` as a hardcoded list, and the
  segment split (`member` vs `casual`) drives the prediction window (30 vs 60
  days). Data-quality assertions are in `tests/`.

### The warehouse-side score (no Python required)

`fct_customer_churn_scores_daily` is a transparent, rule-based risk score built
entirely in SQL. It combines a segment base rate with signal-based uplift across
four risk signals, and attaches a reason and recommended action to each customer:

```sql
-- risk_score = clamp(segment base rate + sum of signal uplift) * 100
cast(
    round(
        least(
            1.0,
            coalesce(segment_rates.observed_churn_rate, 0.0)
              + coalesce(signal_summary.total_signal_rate_uplift, 0.0)
        ) * 100,
        0
    ) as integer
) as risk_score
```

The four signals are `payment_risk` (recent failed payments), `activity_risk`
(no recent events / long gap), `experience_risk` (complaints or low
satisfaction), and `membership_risk` (member with auto-renew off or prior
churned memberships). Each carries a recommended action and offer type, so the
table doubles as a retention work queue. Use this when you want explainable
scores immediately, before any model exists.

### The Python training and scoring workflow

`scripts/train_python_churn_models.py` is the model side. It:

1. loads the dataset (IBM Telco over HTTPS, or your dbt-built tables),
2. prepares the target and splits train/validation/test (a time-based split on
   `as_of_date` for the dbt source, stratified random otherwise),
3. preprocesses numeric and categorical columns,
4. trains logistic regression plus `random_forest` and `hist_gradient_boosting`
   comparison models,
5. selects the best model on validation `average_precision`,
6. calibrates the winner (`CalibratedClassifierCV`, sigmoid),
7. evaluates on the held-out test set, and
8. optionally runs Kaplan-Meier and Cox survival analysis (`--skip-survival` to
   turn it off).

Start with logistic regression: churn is a binary target and you want a
probability, not a yes/no, so you can rank customers by risk. The metrics that
matter are `roc_auc` (how well churners rank above non-churners),
`average_precision` (useful when churn is imbalanced), and `brier_score`
(whether the probabilities are calibrated).

Outputs land under `artifacts/python_models/`: `model_metrics.csv`,
`test_predictions.csv`, `top_feature_importance.csv`, `run_summary.json`,
`best_model.joblib`, validation/test plots, and (for `--source dbt`)
`current_scores.csv`. Passing `--database` also writes result tables back into
the database under `--write-schema` (`python_churn_model_metrics`,
`python_churn_test_predictions`, `python_churn_feature_importance`,
`python_churn_current_scores`, `python_churn_survival_summary`). Read the script
before adapting the model side.

## Questions to answer

- How is churn defined for this business (cancellation window, inactivity window)? This sets the label and `member_churn_grace_period_days`. Write this down first: it becomes the target the model learns.
- What are the source tables for customers, subscriptions/memberships, usage/activity, and payments, and where do they live? You need four kinds of source data: one customer row per customer, a subscription/contract table, an activity/usage table, and a payment/billing table.
- Target MotherDuck database and schema for the feature tables (default `subscription_churn`).
- Full refresh each run, or incremental? Current models rebuild as tables; seeds use `--full-refresh`.
- What "as of" date should the daily score table use (`churn_as_of_date`), and which historical snapshot dates should labels and features cover (`churn_label_dates()`)?
- Should the feature tables refresh on a schedule, and at what cadence?
- MotherDuck token / credentials for cloud runs.
- Train on the bundled IBM Telco benchmark first, or straight on your own dbt-built history?

## Caveats

- **`--source dbt` refuses the bundled sample on purpose.** The script raises if
  the training matrix has fewer than 50 rows or fewer than 10 positive labels.
  The bundled seeds are intentionally tiny: too small for useful machine
  learning. Replace the seeds with real history before using `--source dbt`, or
  stick to `--source ibm_telco` for benchmarking.
- **The time-based split needs history.** `--source dbt` splits on `as_of_date`
  and requires at least 3 distinct snapshot dates, with non-empty train,
  validation, and test partitions. One snapshot date will not train.
- **Snapshot dates are hardcoded.** `churn_label_dates()` in
  `macros/churn_features.sql` lists fixed dates (Dec 2025 through Mar 2026), and
  `vars.churn_as_of_date` defaults to `2026-04-15`. For your own data, edit both
  so the snapshot dates and the "as of" date line up with your history;
  otherwise the feature/label join produces empty or stale tables.
- **`--database` is required for `--source dbt`.** Omitting it raises. The
  script auto-discovers the schema holding `fct_customer_features_historical`
  (preferring an `analytics` schema), so the dbt build must have run first
  against the same database.
- **Don't put your token in config.** `MOTHERDUCK_TOKEN` is a secret: keep it in
  `.env` locally (gitignored), not as a committed file.

## What you'll adjust

| Setting | Purpose | Options / example |
| --- | --- | --- |
| `MOTHERDUCK_DATABASE` (env / profiles.yml) | Target MotherDuck database for the `prod` build | `subscription_churn` (default) |
| `MOTHERDUCK_TOKEN` (env / `.env`) | Auth for MotherDuck runs | your account token |
| profiles.yml target | Where dbt builds: local DuckDB file vs MotherDuck | `local` (`local.db`) or `prod` (`md:<db>`) |
| `vars.churn_as_of_date` (dbt_project.yml) | The "today" the daily score table is computed against | `'2026-04-15'` |
| `vars.member_churn_grace_period_days` (dbt_project.yml) | Days of inactivity before a member counts as churned | `30` |
| `churn_label_dates()` (macros/churn_features.sql) | The historical snapshot dates labels and features are built for | hardcoded list of dates; change for your own history |
| dbt model selector | Which models the build/refresh touches | `tag:churn_daily+` (staging + marts) or `--exclude resource_type:seed` for all |
| seeds (`seeds/raw_*.csv`) | Sample raw inputs to swap for your own customer, membership, usage, payment data | `raw_customers`, `raw_memberships`, `raw_usage_events`, `raw_payments` |
| `--source` (training script) | Training data source | `ibm_telco` (runs immediately) or `dbt` (your built tables) |
| `--write-schema` (training script) | Schema for Python prediction/metric tables written back | `science` (default) |

## Run it

Prerequisites: a MotherDuck account and token for cloud runs. Local DuckDB runs
need no account. The project uses `uv`.

```sh
# install dbt, DuckDB, pandas, scikit-learn, lifelines, etc.
uv sync

# build the feature/label/score tables locally
uv run dbt seed --profiles-dir . --full-refresh
uv run dbt build --profiles-dir . --exclude resource_type:seed

# inspect the current warehouse-side score table
uv run dbt show --profiles-dir . --select fct_customer_churn_scores_daily

# train and evaluate a model on the IBM Telco benchmark
uv run python scripts/train_python_churn_models.py --source ibm_telco
```

To build in MotherDuck instead, copy `.env.example` to `.env`, set
`MOTHERDUCK_TOKEN` and `MOTHERDUCK_DATABASE`, then:

```sh
uv run dbt seed --profiles-dir . --target prod --full-refresh
uv run dbt build --profiles-dir . --target prod --select tag:churn_daily+ --exclude resource_type:seed
```

Once you have enough real history, train on the dbt-built feature matrix and
optionally write predictions back:

```sh
uv run python scripts/train_python_churn_models.py --source dbt --database "md:${MOTHERDUCK_DATABASE}" --write-schema science
```

## Files

- [`scripts/train_python_churn_models.py`](scripts/train_python_churn_models.py) - the Python model side: loads the dataset (IBM Telco or dbt-built tables), trains and calibrates a scikit-learn churn model, evaluates it, and optionally writes prediction tables back to MotherDuck.
- [`dbt_project.yml`](dbt_project.yml) - dbt project config: profile name, the `churn_as_of_date` and `member_churn_grace_period_days` vars, and per-folder materializations, schemas, and the `churn_daily` tag.
- [`profiles.yml`](profiles.yml) - dbt connection profile with `local` (DuckDB file) and `prod` (`md:<db>`) targets.
- [`models/staging/`](models/staging/) - 4 staging views (`stg_customers`, `stg_memberships`, `stg_payments`, `stg_usage_events`) that clean the raw seeds, plus `_sources.yml` and `_models.yml` describing sources and columns.
- [`models/marts/`](models/marts/) - 7 mart tables: the historical feature matrix and churn labels (training input and target), the current-day feature and score tables, subscription history, segment churn rates, and the daily retention queue.
- [`macros/churn_features.sql`](macros/churn_features.sql) - shared feature logic, including the hardcoded historical snapshot dates in `churn_label_dates()` and the member-vs-casual segment split.
- [`seeds/`](seeds/) - sample raw inputs (`raw_customers`, `raw_memberships`, `raw_usage_events`, `raw_payments` CSVs) plus `_seeds.yml`; swap these for your own customer, membership, usage, and payment history.
- [`tests/`](tests/) - 8 singular SQL data-quality assertions (uniqueness per customer/day, risk scores in range, subscription censoring and duration consistency, label eligibility).
- [`pyproject.toml`](pyproject.toml) - Python project deps for `uv sync` (dbt, DuckDB, pandas, scikit-learn, lifelines, matplotlib, joblib).
- [`.env.example`](.env.example) - template for `MOTHERDUCK_TOKEN` and `MOTHERDUCK_DATABASE`; copy to `.env` (gitignored) for cloud runs.
- `analyses/`, `macros/`, `snapshots/` - standard dbt scaffold dirs, currently placeholders (`.gitkeep`).
- `uv.lock` - pinned dependency lockfile for `uv`.

## Learn more

- Deeper MotherDuck or DuckDB questions: use the `ask_docs_question` MCP tool.
