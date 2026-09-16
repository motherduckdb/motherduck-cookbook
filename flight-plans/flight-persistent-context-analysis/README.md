---
title: Build persistent analysis context with Flights and Guides
id: flight-persistent-context-analysis
description: >-
  Analyze only changed e-commerce periods, combine SQL evidence with public and
  organization context, and publish reusable daily, weekly, and monthly Guides.
type: template
category: automation
features: [flights]
tags: [pydantic-ai, openrouter, python]
prompt: >-
  I want to analyze only changed business periods and publish persistent context that
  my team and its agents can reuse. Help me adapt the "Build persistent analysis
  context with Flights and Guides" recipe to my own data and use case, using it
  as a guide: https://motherduck.com/docs/cookbook/flight-persistent-context-analysis
published_date: 2026-09-11
---

# Build persistent analysis context with Flights and Guides

This Flight analyzes changed sales periods and stores the result as reusable
Guides. The demo uses synthetic sales from an online London shop and a physical
London shop. It reads an immutable public Parquet file, computes all numbers in
SQL, and uses a model only to propose evidence-cited narrative context.

## How it works

`flight.py` loads
[`duck_shop_sales.parquet`](https://us.data.motherduck.com/persistent-context-analysis/v1/duck_shop_sales.parquet),
then computes daily, weekly, and monthly metrics from `demo_sales`. It compares
each period with prior ranges and retains state in `persistent_analysis.main`.

Each run reads the BBC London and duck-news RSS feeds, plus London daily weather
from Open-Meteo. It also reads definition Guides and YAML-backed annotation
Guides below `GUIDE_ROOT`. The Flight hashes the trusted evidence and skips an
unchanged eligible period. Changed evidence produces a new Guide version.

Generated Guides live below the `daily`, `weekly`, and `monthly` topics. Daily
Guides use the latest completed weekly and monthly Guides as context. Weekly
Guides use completed daily Guides, and monthly Guides use completed weekly
Guides. The state tables record those links, so a changed Guide updates only the
reports that depend on it.

The model receives precomputed evidence and untrusted source text. It must cite
known IDs. The renderer validates every citation and writes metric values from
SQL objects, not model prose.

### Reading `metric_evidence`

Each metric appears once per comparison baseline, so a single day writes three
rows per metric and dimension: `prior_day`, `same_weekday` and
`trailing_28_days`. They repeat the same `current_value`, so aggregate with
`MAX`, never `SUM` — summing turns a £2,196 day into £6,589. Filter to one
`comparison_label` instead:

```sql
SELECT metric, dimensions, current_value, comparison_value, percentage_change
FROM persistent_analysis.main.metric_evidence
WHERE grain = 'day'
  AND period_start = DATE '2026-09-10'
  AND comparison_label = 'prior_day';
```

The trailing baselines behave differently by metric. For `net_revenue`,
`gross_revenue` and `orders` the comparison value is a window *sum*, so a single
day against `trailing_28_days` shows roughly -96% — arithmetic, not a collapse.
Scale it to the period before comparing:

```sql
comparison_value
  * (date_diff('day', period_start, period_end)::DOUBLE
     / date_diff('day', comparison_start, comparison_end))
```

Rates and averages (`average_order_value`, `refund_rate`, `cancellation_rate`,
`units_per_order`, `returning_customer_share`) are already computed over the
whole window, so their trailing comparison value is the average and needs no
scaling. That makes it the useful stand-in for "normal".

Analysed periods are not necessarily contiguous — a run bounded by
`RECONCILIATION_DAYS` leaves gaps. Chart them as bars; a line interpolates
across days that were never processed.

## Questions to answer

- What source table replaces the synthetic Parquet file?
- Which database and schema can hold the state tables?
- Which definition Guides describe metric names, data grain, and business rules?
- Which organization events need annotation Guides?
- Which model and Flights secret provide `OPENROUTER_API_KEY`?
- Which reconciliation window matches the expected late-arriving data?

## Caveats

- Current RSS feeds are not archives. A failed fetch preserves already stored signals, but it cannot recover an item that was never observed.
- Weather, news, and annotations provide possible context. Temporal association does not establish causation.
- The default `GUIDE_ACCESS=user` keeps generated Guides private to the Flight identity. Use `organization` only with an admin-authorized identity and an access test.
- `RETENTION_MODE=archive` moves old daily and weekly Guides. It does not delete Guides.
- The public fixture is synthetic. Do not treat it as a production benchmark or customer dataset.
- A weekly Guide has no field in which to cite a daily one. `Finding` carries `evidence_ids`, `signal_ids` and `annotation_ids` only, so parent Guides read their children as prompt context but leave no traceable link. Roll-up lineage is readable from `period_dependencies`, not from the Guide text.
- A catch-up run costs more than one model call per period. Days drain before weeks before months, so a week regenerates once its dailies land, and the days citing a regenerated month regenerate in turn. Steady-state daily runs are unaffected.

## What you'll adjust

| Config key | Default | Purpose |
| --- | --- | --- |
| `DEMO_DATA_URL` | Public synthetic Parquet URL | Replace with a Parquet URL that has the same expected sales shape. |
| `STATE_DATABASE` and `STATE_SCHEMA` | `persistent_analysis`, `main` | Store period state, metrics, signals, and dependencies. |
| `GUIDE_ROOT` | `persistent-analysis/ecommerce` | Parent topic for definitions, annotations, and generated Guides. |
| `GUIDE_ACCESS` | `user` | Set `organization` only after an organization visibility test. |
| `ANALYSIS_AS_OF` | Fixture maximum date plus one day | Bound a deterministic historical run with `YYYY-MM-DD`. |
| `RECONCILIATION_DAYS` | `7` | Recheck recent completed days for late source data. |
| `DEMO_REVISION` | `0` | Set `1` only to demonstrate bounded late-data invalidation. |
| `RETENTION_MODE` | `keep` | Set `archive` to move old daily and weekly Guides. |
| `DAILY_GUIDE_KEEP_DAYS` | `90` | Keep this many daily Guides before archival. |
| `WEEKLY_GUIDE_KEEP_WEEKS` | `52` | Keep this many weekly Guides before archival. |
| `MODEL` | `anthropic/claude-sonnet-4.6` | OpenRouter model used after evidence changes. Set as a Flight config value or local environment variable. |
| `ANALYSIS_INSTRUCTIONS` | Built-in evidence and causation rules | Operator instructions inserted before trusted and untrusted data. Set as a Flight config value or local environment variable; keep the default safety rules when overriding it. |
| `OPENROUTER_SECRET_NAME` | `openrouter` | Flights secret name. The Flight reads `<secret_name>_OPENROUTER_API_KEY` first, then `OPENROUTER_API_KEY` for local runs. |

## Run it

Install the pinned dependencies and build a local copy of the synthetic fixture.

```bash
uv run --with-requirements requirements.txt flight.py \
	--build-demo-data .context/persistent-context-analysis/duck_shop_sales.parquet
uv run --with pytest --with-requirements requirements.txt \
	python -m pytest ../../tests/test_persistent_context_analysis.py -q
```

Create definition Guides under `persistent-analysis/ecommerce/definitions`.
Create event annotations under `persistent-analysis/ecommerce/annotations` with
front matter like this:

```markdown
---
event_id: checkout-outage-142
start_at: 2026-09-10T09:12:00Z
end_at: 2026-09-10T10:04:00Z
scope: store_id=duck_shop_online
category: incident
source: INC-142
---
Checkout returned HTTP 503.
```

Timestamps can be unquoted YAML timestamps or quoted ISO 8601 strings. Every
timestamp must include a timezone. The Flight treats the body as untrusted data.

### Deploy as a Flight

First create an API key at [OpenRouter API keys](https://openrouter.ai/settings/keys).
Then, in [MotherDuck Settings > Secrets](https://app.motherduck.com/settings/secrets),
create a `TYPE flights` secret named `openrouter` with an
`OPENROUTER_API_KEY` parameter. The OpenRouter page creates the key; the
MotherDuck secret makes it available only to the Flight. Do not put the key in
source, Flight config, or a Guide.

Create the Flight with `MD_CREATE_FLIGHT`, passing a name,
[`flight.py`](flight.py), [`requirements.txt`](requirements.txt),
`flight_secret_names: ["openrouter"]`, and any config values from the table.
Set `OPENROUTER_SECRET_NAME` if the secret has a different name. A Flight
injects the secret both as `openrouter_OPENROUTER_API_KEY` and as the bare
`OPENROUTER_API_KEY`; local runs can set only the bare variable. The Flight
runtime injects `MOTHERDUCK_TOKEN` automatically.

Create the Flight without a schedule. Run it with `MD_RUN_FLIGHT`, then inspect
the returned run with `MD_GET_FLIGHT_RUN`. Confirm that the generated Guides
contain SQL evidence, public context, and the expected period hierarchy. Add a
schedule only after that run succeeds, using `MD_UPDATE_FLIGHT`.

A run that fails any period exits non-zero, so a scheduled Flight reports the
failure instead of a green run with no new Guides. The per-period error is in
the `analysis_periods` state table.

## Security

- Keep `OPENROUTER_API_KEY` in a Flights secret. Do not put it in source, config, or a Guide.
- The Flight validates database and schema identifiers before interpolating them into SQL. It binds all other values as parameters.
- RSS content, weather data, annotation bodies, and prior Guide content are untrusted data. The model has no SQL tool, and the renderer rejects unknown evidence references.
- Give the Flight identity only the database and Guide permissions it needs.

## Learn more

- [`flight.py`](flight.py) contains the fixture builder, SQL metrics, Guide storage, fingerprinting, and the Flight entrypoint.
- [`requirements.txt`](requirements.txt) pins the Flight runtime dependencies.
- For Flight deployment and Guide SQL details, use `get_flight_guide` or `ask_docs_question`.
