---
title: Extract Structured Data From Reviews With dbt And prompt()
id: dbt-ai-prompt
description: >-
  A dbt-duckdb project that calls MotherDuck's prompt() AI function to turn
  unstructured product reviews into typed columns (sentiment, pros, cons,
  features), then aggregates them by product. Use when you want LLM extraction
  to run inside a dbt model as a normal SQL transformation, with no external
  API code.
type: example
features: [shares]
tags: [dbt]
---

# Extract Structured Data From Reviews With dbt And prompt()

This is a dbt-duckdb project that runs MotherDuck's `prompt()` AI function inside a model to extract structured fields from free-text product reviews. The pattern: a single SQL model passes each review's `title` and `text` to `prompt()` with a `struct` schema, MotherDuck returns typed columns (sentiment, pros, cons, product features, customer-service signals, and more), and downstream views aggregate those attributes per product. LLM extraction becomes just another dbt transformation, versioned and testable like any other model.

The source data is a sample Amazon "toys and games" reviews dataset that lives in the public MotherDuck share `webshop-dbt-md-ai`, so you can run the example end to end before pointing it at your own table.

## How it works

`models/reviews/reviews_attributes.sql` is the core model. It calls `prompt()` once per row with two arguments that do the heavy lifting:

- `struct` declares the output schema. Each key becomes a typed column on the result, so `prompt()` returns structured data instead of a blob of text. The example asks for 16 fields spanning sentiment, feature mentions, quality indicators (`has_size_info`, `mentions_price`, `mentions_shipping`, `mentions_packaging`), comparative analysis, usage context, reported issues, and customer-service signals.
- `struct_descr` gives a natural-language description per field. This is where you constrain values, for example telling the model that `sentiment` "can only take values `positive`, `neutral` or `negative`" and that list fields should "return empty array" when nothing matches.

The model then projects `prompt_struct_response.*` so the struct's fields land as flat columns alongside `parent_asin`:

```sql
select parent_asin, prompt_struct_response.*
from (
    select
        parent_asin,
        prompt(
            'You are a very helpful assistant. ...'
            || title
            || '...'
            || text,
            struct := {
                sentiment:'VARCHAR',
                product_features:'VARCHAR[]',
                pros:'VARCHAR[]',
                cons:'VARCHAR[]',
                mentions_price:'BOOLEAN',
                -- ...12 more fields...
            },
            struct_descr := {
                sentiment:'the sentiment of the review, can only take values `positive`, `neutral` or `negative`',
                -- ...one description per field...
            }
        ) as prompt_struct_response
    from (select * from {{ source('reviews', 'reviews_raw') }} limit 10)
)
```

Two views build on that table:

- `models/reviews/reviews_attributes_by_product.sql` unnests the array fields (`product_features`, `pros`, `cons`, `competitor_mentions`, `use_case`, `purchase_reason`, `reported_issues`, `quality_concerns`) and re-aggregates them into deduplicated arrays per product with `array_distinct(array_agg(...))`. The result is one row per `parent_asin` holding the distinct set of everything reviewers mentioned.
- `models/reviews/reviews_attributes_sentiment_by_product.sql` counts positive/neutral/negative sentiment per product and computes a normalized score, `(positive - negative) / total`, ranging from -1 to 1, for both overall reviews and customer-service interactions. `NULLIF(..., 0)` guards against divide-by-zero when a product has no scored rows.

## Questions to answer

- What is the source table of unstructured text, and which columns hold the text to extract from?
- Which target MotherDuck database and schema should the models write to?
- What structured fields are wanted out, and what are the allowed values / types for each?
- Should this run on a sample (the `limit 10` demo) or the full table?
- Is there a `MOTHERDUCK_TOKEN` with read/write access to the target database?
- Is the source database created and populated (or, for the demo, is the `webshop-dbt-md-ai` share attached)?

## Caveats

- Do not commit your `MOTHERDUCK_TOKEN`. The repo ships an `.envrc` that exported a real token during development. Treat any token visible in version control as compromised, rotate it, and load tokens from your own environment or a secret manager instead of checking them in.
- `prompt()` runs once per input row and is billed and rate-limited as an AI call. The model ships with `limit 10` for exactly this reason. Removing the limit on a large table can be slow and costly, so size your run deliberately.
- LLM output is non-deterministic. Two `dbt run` invocations can return slightly different extractions, so do not rely on byte-stable results. Materializing `reviews_attributes` as a `table` (the default) freezes one run's output for the downstream views; re-running re-extracts.
- The `accepted_values` test on `sentiment` will fail if `prompt()` ever returns a value outside `positive`/`neutral`/`negative` (for example a capitalized or hedged answer). The `struct_descr` text is guidance, not a hard constraint. Keep the test as a guardrail and tighten the prompt wording, or normalize the column, if it trips.
- Change `+database: my_db` in `dbt_project.yml` before running. The placeholder `my_db` is not a real database; leave it and `dbt run` writes to (or fails to create) a database called `my_db`.
- The source must exist and be reachable. `_sources.yml` points at the `webshop-dbt-md-ai` share; if it is not attached, or your own source table is missing, dbt fails at compile/run time rather than silently producing empty output.
- Empty arrays vs nulls: list fields are instructed to return an empty array when nothing matches. `unnest` drops empty arrays, so products whose reviews mention nothing for a given field simply do not contribute rows in `reviews_attributes_by_product`. That is expected, not a bug.

## What you'll adjust

| Setting | Purpose | Options / example |
| --- | --- | --- |
| `source('reviews', 'reviews_raw')` in `models/reviews/reviews_attributes.sql` | The input table of unstructured text to extract from | Point at your own reviews/feedback table |
| `database` / `schema` / `tables` in `models/reviews/_sources.yml` | Where the source data lives | Defaults to database `webshop-dbt-md-ai`, schema `main`, table `reviews_raw` |
| `limit 10` in `reviews_attributes.sql` | Caps how many rows are sent to `prompt()` (keeps cost and runtime small for a demo) | Raise or remove for a full run |
| The `prompt(...)` instruction text in `reviews_attributes.sql` | The extraction prompt, built by concatenating the `title` and `text` columns | Swap in your own column names and task |
| `struct` and `struct_descr` in `reviews_attributes.sql` | The output schema and per-field descriptions that shape the returned columns | Add/remove fields like `sentiment:'VARCHAR'`, `pros:'VARCHAR[]'`, `mentions_price:'BOOLEAN'` |
| `+database: my_db` under `models.dbt_ai_prompt.reviews` in `dbt_project.yml` | Target database materialized models are written to | Change `my_db` to a MotherDuck database you can write to |
| `+materialized: table` (dir default) / per-model `config(materialized=...)` | Materialization: the extraction model is a `table`, aggregates are `view` | `table`, `view`, `incremental` |
| `path: 'md:'` and `target: dev` in `profiles.yml` | Connection target for dbt | `md:` for MotherDuck, or a local `.duckdb` file path |
| `MOTHERDUCK_TOKEN` env var | Read/write auth for MotherDuck | A token from your MotherDuck account |
| `accepted_values` / `not_null` tests in `models/reviews/schema.yml` | Validate the extracted output (e.g. sentiment in positive/neutral/negative) | Adjust to your fields |

## Run it

Prerequisites: a MotherDuck account and a read/write `MOTHERDUCK_TOKEN` set in your environment, plus access to the source database.

```sh
uv venv --python 3.13
uv pip install dbt-duckdb duckdb==1.4.3
source .venv/bin/activate

export MOTHERDUCK_TOKEN="your_token_here"

dbt run
dbt test
dbt show --select reviews_attributes_by_product
```

`dbt run` builds `reviews_attributes` (the `prompt()` extraction, as a table) plus the two aggregate views; `dbt test` runs the `not_null` and `accepted_values` checks in `schema.yml`. The `duckdb==1.4.3` pin matters: MotherDuck only accepts specific DuckDB client versions, and a mismatched local DuckDB will fail to connect.

The example reads `reviews_raw` from the public `webshop-dbt-md-ai` MotherDuck share. If your account does not already have it attached, attach it once:

```sql
ATTACH 'md:_share/webshop-dbt-md-ai/a8a01cac-c4e6-4de1-93bf-bcc4c54aa77f';
```

## Files

- [`dbt_project.yml`](dbt_project.yml) - dbt project config: names the project `dbt_ai_prompt`, sets standard path layout, and defaults `models/reviews` to materialize as `table` writing to database `my_db` (change this placeholder before running).
- [`profiles.yml`](profiles.yml) - the dbt connection profile: a single `dev` target using the `duckdb` adapter with `path: 'md:'` to connect to MotherDuck.
- [`models/reviews/reviews_attributes.sql`](models/reviews/reviews_attributes.sql) - the core model: calls `prompt()` once per review row with a `struct` and `struct_descr` to extract 16 typed fields, materialized as a table (`limit 10` caps the demo run).
- [`models/reviews/reviews_attributes_by_product.sql`](models/reviews/reviews_attributes_by_product.sql) - a view that unnests the array fields and re-aggregates them into deduplicated arrays per product.
- [`models/reviews/reviews_attributes_sentiment_by_product.sql`](models/reviews/reviews_attributes_sentiment_by_product.sql) - a view that counts positive/neutral/negative sentiment per product and computes a normalized score for both reviews and customer-service interactions.
- [`models/reviews/_sources.yml`](models/reviews/_sources.yml) - declares the `reviews` source: database `webshop-dbt-md-ai`, schema `main`, with tables including `reviews_raw`.
- [`models/reviews/schema.yml`](models/reviews/schema.yml) - model documentation and data tests: column descriptions plus `not_null` and `accepted_values` checks (e.g. `sentiment` must be positive/neutral/negative).
- [`analyses/`](analyses/), [`macros/`](macros/), [`seeds/`](seeds/), [`snapshots/`](snapshots/), [`tests/`](tests/) - the standard dbt project directories, empty placeholders here (each holds a `.gitkeep`).
- [`.gitignore`](.gitignore) - ignores dbt build output: `target/`, `dbt_packages/`, and `logs/`.

## Learn more

- For `prompt()` syntax, the `struct` extraction pattern, and other MotherDuck AI functions, run the `ask_docs_question` MCP tool or see the MotherDuck docs.
- For attaching and managing MotherDuck shares like `webshop-dbt-md-ai`, see the MotherDuck data sharing docs.
