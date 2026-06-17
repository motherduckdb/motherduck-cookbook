---
title: Clean and Analyze a CSV in the MotherDuck UI
id: motherduck-ui
description: >-
  A step-by-step SQL walkthrough that loads a CSV, profiles it with SUMMARIZE,
  cleans messy columns, and answers an analysis question, run one query at a time
  in the MotherDuck web UI. Use when you want a hands-on intro to interactive data
  exploration and ad hoc cleaning in MotherDuck without writing application code.
type: example
category: getting-started
features: []
tags: []
prompt: >-
  I want a hands-on, query-by-query walkthrough for loading a CSV, profiling it with
  SUMMARIZE, cleaning messy columns, and answering an analysis question in the
  MotherDuck web UI. Help me adapt the "Clean and Analyze a CSV in the MotherDuck UI"
  recipe to my own data and use case, using it as a guide:
  https://motherduck.com/docs/cookbook/motherduck-ui
published_date: 2025-01-29
---

# Clean and Analyze a CSV in the MotherDuck UI

This example is a guided SQL session you run query-by-query in the MotherDuck web UI. It shows the typical interactive flow: load a CSV into a table, profile it with `SUMMARIZE`, iteratively clean columns (drop bad rows, parse unit sizes into quantity and volume, normalize a dollar-formatted price), then answer an analysis question. It demonstrates the MotherDuck pattern of exploring and reshaping raw data interactively before promoting it to a clean, reusable table.

The dataset (`winelist_sample.csv`, ~1,500 rows) is a wine merchant offer list: country, region, producer, wine name, vintage, unit size (e.g. `6x75cl`), two critic scores, quantity, and a dollar-formatted offer price. It is intentionally messy so the cleaning steps have something to do.

## How it works

The script builds up one cleaning `SELECT` incrementally. Each step adds one transformation so you can verify it before adding the next.

1. **Load.** `read_csv_auto` infers types and column names from the header.

   ```sql
   CREATE OR REPLACE TABLE winelist AS
   SELECT * FROM read_csv_auto(['winelist_sample.csv']);
   ```

2. **Profile.** `SUMMARIZE` returns per-column min/max/approx-unique/null-percentage so you can spot the messy columns at a glance.

   ```sql
   SUMMARIZE winelist;
   ```

3. **Trim the view, drop bad rows.** `SELECT * EXCLUDE("Wine Name")` hides the long name column so the rest of the table is readable, and `WHERE vintage > 1000` removes rows with junk vintages.

4. **Parse the unit size.** `"Unit size"` holds values like `6x75cl`. The `substr` + `instr` logic splits it on the `x` delimiter into a bottle count and a volume in centiliters:

   ```sql
   cast(substr("Unit size", 1, instr("Unit size", 'x') - 1) as integer) as qty,
   cast(
     substr(
       "Unit size",
       instr("Unit size", 'x') + 1,
       length("Unit size") - instr("Unit size", 'x') - 2  -- the trailing "cl" is 2 chars
     ) as integer
   ) as volume_cl
   ```

5. **Normalize the price and derive metrics.** `"Offer price"` is dollar-formatted (`$325.00`, `$1,290.00`). Strip the `$` and thousands `,`, then cast to a fixed-precision decimal, and derive comparable per-bottle and per-75cl prices:

   ```sql
   cast(replace(replace("Offer price", '$', ''), ',', '') as decimal(10, 2)) as offer_price,
   offer_price / (qty * volume_cl) * 75 as price_per_75cl,
   offer_price / qty as price_per_bottle
   ```

`script.sql` holds the full sequence with inline comments explaining each step.

## The exercise

The last block answers a concrete question: the price-per-bottle difference between the highest-rated, least-expensive bottle and the lowest-rated, most-expensive bottle, for vintages of 1990 or later.

- For the rating, it uses `coalesce(coalesce("WA score","Vinous score"),-1)` so a missing WA score falls back to the Vinous score, and a row missing both sorts last.
- For price it uses `offer_price / qty as price_per_bottle`.

The exercise query reads from a table named `winelist_clean`, **which the script never creates**. Before running it, persist your cleaned `SELECT` (the step 5 query) as that table:

```sql
CREATE OR REPLACE TABLE winelist_clean AS
SELECT * EXCLUDE("Unit size"),
  -- ... the qty / volume_cl / offer_price / price_per_bottle columns from step 5 ...
FROM winelist
WHERE vintage > 1000;
```

## Questions to answer

- What CSV (or other source) are you loading, and where does it live (local upload, S3, HTTPS)?
- Which database and schema should the resulting table live in?
- Which columns need cleaning, and what are their real formats (delimiters, currency symbols, units)?
- Do you want to keep the cleaned result as a new table (`CREATE TABLE ... AS`) or just explore?
- What analysis question are you trying to answer, and which columns drive the ranking or aggregation?

## Caveats

- **The exercise depends on a table the script does not build.** Running the final `with cte_cheap_but_good ... ` block before creating `winelist_clean` fails with a "table does not exist" (Catalog) error. Persist the cleaned `SELECT` as `winelist_clean` first.
- **Column names are case- and whitespace-sensitive.** The raw CSV header has `Wine name`, `Quantity`, and a price column written as ` Offer Price ` (note the surrounding spaces). `read_csv_auto` normalizes some of this, but the script refers to identifiers like `"Wine Name"` and `"Offer price"`. If a `SELECT` errors on an unknown column, run `SUMMARIZE winelist` (or `DESCRIBE winelist`) and copy the exact column name, including spaces, into double quotes. Double quotes are for identifiers; single quotes are for string literals like `'$'`.
- **The unit-size parser assumes a fixed format.** `substr(..., length(...) - instr(..., 'x') - 2)` hard-codes a 2-character trailing unit (`cl`). A value like `1x150cl` parses, but a different unit (`ml`, `L`) or a missing `x` delimiter will silently produce a wrong number or fail the `integer` cast. Validate `qty` and `volume_cl` against the source before trusting the derived prices.
- **`price_per_75cl` divides by `qty * volume_cl`.** If either parses to `0` or `NULL`, you get a division-by-zero or `NULL`. Confirm the parse step is clean before computing the ratio.
- **`WHERE vintage > 1000` is a blunt filter,** kept only to drop obviously-junk vintages. It is not a real validity check; adjust it for your data rather than assuming it cleans everything.
- **The example is built for the UI's run-one-query-at-a-time flow.** Running the whole `script.sql` in a CLI in one go defeats the inspect-each-step purpose and surfaces the `winelist_clean` error immediately.

## What you'll adjust

| Setting | Purpose | Options / example |
| --- | --- | --- |
| `winelist_sample.csv` | Source file loaded by `read_csv_auto([...])` | Swap for your own CSV path; the UI also lets you drag-drop a file or read from S3/HTTPS |
| `winelist` table name | Target table created by the `CREATE OR REPLACE TABLE` step | Rename to your dataset, e.g. `orders`, `sales_raw` |
| Database / schema | Where the table lands; defaults to `my_db.main` in the UI | Pick the database with the UI database selector or qualify as `db.schema.table` |
| `"Unit size"` parsing | `substr` + `instr` logic that splits a value like `6x75cl` into `qty` and `volume_cl` | Adjust the delimiter (`x`) and the trailing-unit length (currently `cl`, 2 chars) for your format |
| `"Offer price"` cleanup | `replace(...)` strips `$` and `,` before casting to `decimal(10,2)` | Change the symbols stripped, the precision/scale, and the source column name |
| `WHERE vintage > 1000` | Filter that removes obvious bad rows | Replace with your own validity filter, or drop it |
| Exercise thresholds | The analysis query filters `Vintage >= 1990` and ranks on `coalesce(coalesce("WA score","Vinous score"),-1)` and `price_per_bottle` | Change the year cutoff, scoring columns, and ranking metric for your question |

## Run it

Prerequisites: a MotherDuck account. Open [app.motherduck.com](https://app.motherduck.com), sign in, and use the SQL editor.

1. Upload `winelist_sample.csv` (or your own CSV) via the UI file picker, or reference it from S3/HTTPS.
2. Open `script.sql` and run it one statement at a time, top to bottom. Read the output of each query before moving on, that is the point of the walkthrough.
3. Once the cleaning `SELECT` looks right, persist it as a clean table so the exercise can read it (see "The exercise" below).

You can also run the script from the DuckDB or MotherDuck CLI, but it is written as a UI walkthrough and is best experienced there: running it all at once skips the inspect-after-every-step loop the example is teaching.

## Files

- [`script.sql`](script.sql) - the guided SQL walkthrough: loads the CSV, runs `SUMMARIZE`, builds up the cleaning `SELECT` step by step (drop bad rows, parse `Unit size`, normalize the price), and ends with the exercise query.
- [`winelist_sample.csv`](winelist_sample.csv) - the intentionally messy source data: ~2,450 rows of wine merchant offers with country, region, producer, wine name, vintage, unit size (e.g. `6x75cl`), WA and Vinous scores, quantity, and a dollar-formatted offer price.

## Learn more

- `script.sql` is the source of truth, with inline comments on each cleaning step.
- For deeper MotherDuck or DuckDB SQL questions (CSV reading options, `SUMMARIZE`, `SELECT * EXCLUDE`, `instr`/`substr`/`replace`, casting), use the `ask_docs_question` MCP tool or the MotherDuck docs.
