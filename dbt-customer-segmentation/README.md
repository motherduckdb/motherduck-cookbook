---
title: Segment Retail Customers With dbt and Python
id: dbt-customer-segmentation
description: >-
  Build a retail customer segmentation workflow with dbt SQL models, a dbt
  Python K-Means model, and reviewable MotherDuck tables. Use when a user wants
  promotion-response segments, customer feature engineering, or an activation
  mart backed by MotherDuck.
type: example
category: analytics
features: []
tags: [dbt, python, pandas, scikit-learn]
---

# Segment Retail Customers With dbt and Python

This example builds a promotion-response segmentation workflow for a grocery
retailer. It loads sample retail data, engineers household features in dbt,
fits a Python K-Means model, scores households against named business segment
centroids, and publishes a `mart_customer_segments` table for BI, reverse ETL,
or application reads.

The sample data is intentionally small, but the pattern is production-shaped:
replace the seed files with landed retail, marketplace, subscription, or
loyalty tables, then keep the dbt graph as the auditable feature and scoring
layer in MotherDuck.

## How it works

The project uses dbt SQL models for deterministic transformation work and a dbt
Python model for clustering:

```text
raw retail data
  -> typed staging models
  -> promotion feature engineering
  -> standardized household feature vectors
  -> Python K-Means clustering
  -> weighted centroid segment scoring
  -> segment profiles and activation mart
```

`dbt seed` loads sample transactions, products, households, campaign targeting,
coupons, coupon redemptions, and segment configuration tables. Staging models
type those inputs. Intermediate models normalize discount sign conventions and
flag campaign-targeted, coupon, private-label, and in-store-discount behavior.

The marts layer produces two complementary segmentation outputs:

- `fct_household_kmeans_segments` fits K-Means over standardized household
  features with scikit-learn. It returns the discovered cluster, cluster size,
  distance to the assigned K-Means center, per-household silhouette score, and
  overall silhouette score.
- `fct_household_segments` assigns each household to the nearest configured
  business segment from `seeds/segment_centroids.csv`. This keeps segment labels
  stable and reviewable even when K-Means cluster ids change after data or
  feature updates.

The final `mart_customer_segments` table keeps both outputs so analysts can
compare discovered clusters with activation-ready segment definitions.

## Questions to answer

- Which production tables will replace the sample seed files?
- What is the observation window for transactions, campaigns, and coupon
  redemptions?
- Which customer behavior should define the segment strategy: promotion
  response, private-label affinity, brand loyalty, engagement, margin, churn, or
  another business goal?
- Which features should be binary signals and which should be continuous
  standardized signals?
- How many exploratory K-Means clusters should be tested before picking stable
  business segments?
- Who owns the segment centroid definitions and activation playbook?
- Which downstream tools will consume `mart_customer_segments`?

## Caveats

- Campaign attribution is simplified. If a household was targeted for a
  campaign product, purchases of that product count as campaign-targeted
  purchases. Revisit this for production attribution windows and holdouts.
- Coupon redemptions are not joined directly back to individual transaction
  lines. Transaction-line discount fields drive the feature engineering.
- Missing continuous promotion features are mean-imputed after standardization
  by setting missing standardized values to zero. Binary `has_*` flags preserve
  whether the behavior occurred.
- K-Means cluster ids are not durable business labels. Use the centroid layer
  and playbook seeds for stable activation labels.
- Demographic residuals are for profiling and explainability. Do not use them
  for protected-class targeting, exclusion, or automated eligibility decisions.

## What you'll adjust

| File or setting | Purpose | Example values |
| --- | --- | --- |
| `profiles.yml` | Choose local DuckDB or MotherDuck execution. | `target: local`, `target: prod`, `MOTHERDUCK_DATABASE=customer_segmentation` |
| `.env` | Provide MotherDuck credentials for the `prod` target. | `MOTHERDUCK_TOKEN`, `MOTHERDUCK_DATABASE` |
| `dbt_project.yml` `segmentation_reference_day` | Stamp the segmentation run with the retail reference day used by the sample data. | `730` |
| `dbt_project.yml` `python_cluster_count` | Set the number of K-Means clusters for discovery-oriented analysis. | `4`, or another value from `2` through one less than the household count |
| `dbt_project.yml` `python_cluster_random_state` | Keep clustering reproducible while tuning features. | `42` |
| `seeds/raw_*.csv` | Sample source data to replace with production sources. | Transactions, products, households, campaigns, coupons |
| `models/marts/fct_household_features.sql` | Household-level metrics and normalized feature columns. | Coupon rates, discount depth, basket value, purchase frequency |
| `models/marts/fct_household_features_long.sql` | Feature list sent into standardization, K-Means, and centroid scoring. | Add or remove feature rows here |
| `seeds/segment_centroids.csv` | Named segment prototypes and feature weights for reviewable assignment. | `promotion_maximizers`, `private_label_loyalists` |
| `seeds/segment_playbook.csv` | Business labels, recommended actions, and offers for the final mart. | Coupon bundle, private-label cross-sell |

## Run it

Install dependencies:

```sh
uv sync
```

Load the sample raw tables into local DuckDB:

```sh
uv run dbt seed --profiles-dir . --full-refresh
```

Build models and run model tests:

```sh
uv run dbt build --profiles-dir . --exclude resource_type:seed
```

Run the full test suite, including seed tests:

```sh
uv run dbt test --profiles-dir .
```

Inspect the final mart:

```sh
uv run dbt show --profiles-dir . --select mart_customer_segments
```

The local target writes to `local.db`.

### Run in MotherDuck

Copy and edit the example environment file:

```sh
cp .env.example .env
```

Set:

```sh
MOTHERDUCK_TOKEN=your_token_here
MOTHERDUCK_DATABASE=customer_segmentation
```

Create the MotherDuck database once:

```sql
CREATE DATABASE IF NOT EXISTS customer_segmentation;
```

Load the environment variables:

```sh
set -a
source .env
set +a
```

Load seeds into MotherDuck native storage:

```sh
uv run dbt seed --profiles-dir . --target prod --full-refresh
```

Build the segmentation graph:

```sh
uv run dbt build --profiles-dir . --target prod --select tag:customer_segmentation+ --exclude resource_type:seed
```

Run the full MotherDuck test suite:

```sh
uv run dbt test --profiles-dir . --target prod
```

Inspect segment distribution:

```sh
uv run dbt show --profiles-dir . --target prod --inline '
select
  segment_name,
  count(*) as households,
  round(avg(segment_confidence), 3) as avg_confidence
from {{ ref("mart_customer_segments") }}
group by 1
order by 1
'
```

## Security

- Keep `MOTHERDUCK_TOKEN` in `.env` or your secret manager. Do not commit real
  tokens.
- The sample project only loads local CSV seeds. When replacing them with
  production sources, keep credentials in dbt profiles, environment variables,
  or MotherDuck secrets rather than SQL literals.
- Keep demographic residuals in profiling workflows. Do not route offers,
  eligibility, or suppression logic directly from demographic categories.
- Review segment playbook actions before activating them in marketing,
  lifecycle, sales, or support tools.

## Learn more

- [`models/_models.yml`](models/_models.yml) documents the model contracts and
  data tests.
- [`seeds/_seeds.yml`](seeds/_seeds.yml) documents the sample inputs and segment
  configuration seeds.
- [`models/marts/fct_household_kmeans_segments.py`](models/marts/fct_household_kmeans_segments.py)
  contains the dbt Python K-Means model.
- [`models/marts/mart_customer_segments.sql`](models/marts/mart_customer_segments.sql)
  publishes the activation-ready mart.
- Use `ask_docs_question` for current MotherDuck and dbt-duckdb details before
  adapting the connection profile or deployment pattern.
