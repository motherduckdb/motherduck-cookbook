---
title: Re-Sort a Table for Fast Selective Queries
id: flight-re-sort-table
description: >-
  If fast selective read queries are important, use this Flight for up to 10x or more speed improvement!
  This is a reusable Flight that rewrites all or part of a MotherDuck table in sorted order
  to speed up selective read queries. The sorting takes better advantage of DuckDB's min/max
  zonemap indexes so less data needs to be read. Use this Flight when the order that data is inserted
  into a large table is different from the where clauses that are used when querying it.
  For example, if data for all customer_id's is loaded every 5 minutes, but data is always queried
  one customer_id at a time, use this Flight to resort the last week by customer_id. 
type: template
category: automation
features: [flights]
tags: []
prompt: >-
  This flight will sort a MotherDuck table in full or in part. The purpose is to improve read performance to use DuckDB's min/max indexes (also called zonemaps) (research here to learn more: https://duckdb.org/2025/05/14/sorting-for-fast-selective-queries). The flight should take as input: table_to_order, order_by_clause, where_clause. The flight should first validate the inputs do not permit SQL injection. Create dummy SQL statements and use the DuckDB tokenizer (available as a table function) to try to tokenize each input. The tokenizer should only find a single statement, and it should be a select statement. For example, the dummy statement for table_to_order would be `select * from table_to_order`, for the order by it would be `select * from table_to_order order by order_by_clause`. I want the Flight to be flexible enough to detect if there is an `ORDER BY` as the initial few tokens (with any whitespace around them) or `WHERE` as the first few tokens in the where_clause so that the user can either specify them or not and it will still work. Make sure the check is case insensitive as well. Once inputs are validated, the overall task of the flight is to: Begin a transaction, create a table (a real table, not a temporary table) that is the name of the table_to_order but with a random uuid appended to it using the SQL pseudo code of `create or replace table table_to_order_[random_uuid] as select * from table_to_order where where_clause`,  then delete the data from the original table with `delete from table_to_order where where_clause`, then re-insert the data but sorted with `insert into table_to_order select * from table_to_order_[random_uuid] order by order_by_clause`, commit the transaction.
published_date: 2026-07-06
---

# Re-Sort a Table for Fast Selective Queries

DuckDB keeps a min/max index (a *zonemap*) per row group per column. A
filtered query skips a whole row group when the filter value falls outside
that range — but only if rows are physically clustered on the filtered
column. Tables loaded by arrival time rarely are. This Flight rewrites a
table (or the slice matched by `WHERE_CLAUSE`) in `ORDER_BY_CLAUSE` order,
inside one transaction, so selective queries read only the row groups that
matter. Background:
[Sorting for Fast Selective Queries](https://duckdb.org/2025/05/14/sorting-for-fast-selective-queries).

## How it works

1. **Validate.** The three inputs are spliced into SQL, so each is validated
   first on a local in-memory DuckDB, using DuckDB itself rather than string
   matching. No SQL injection risk.
2. **Rewrite, atomically.** With a random-UUID scratch table in the same
   database and schema:

   ```sql
   BEGIN;
   CREATE OR REPLACE TABLE <table>_<uuid> AS
       SELECT * FROM <table> WHERE (<where>);
   DELETE FROM <table> WHERE (<where>);
   INSERT INTO <table> SELECT * FROM <table>_<uuid> ORDER BY <order_by>;
   DROP TABLE <table>_<uuid>;
   COMMIT;
   ```

   The scratch table is dropped inside the transaction, so a commit
   leaves nothing behind and a rollback undoes everything at once.
3. **Report.** The run logs each statement, the matched row count, and the
   total duration. `DRY_RUN=true` stops after validation and a zero-cost
   server-side bind check.

## Questions to answer

- Which table needs re-sorting, and is it filtered often enough to justify
  rewriting it?
- What sort key? Lead with the most-filtered columns, lowest cardinality
  first. For timestamps, prefer a rounded bucket (`date_trunc('month', ts),
  other_col`) over the raw value; for long VARCHARs the first 8 bytes are all
  zonemaps see, so `col[:8]` sorts cheaper with the same effect.
- Whole table, or one slice at a time (`WHERE_CLAUSE`) for tables too large to
  rewrite in one pass?
- One-shot, or on a schedule (cron, UTC) to counter ongoing unsorted inserts?

## Caveats

- **Concurrent writes to the table can conflict.** MotherDuck resolves
  write-write conflicts by failing one transaction; the Flight then rolls back
  cleanly and can be re-run. Prefer quiet windows.
- **Sorting erodes.** Trickle inserts after the rewrite are appended unsorted;
  schedule the Flight periodically if the table keeps growing.

## What you'll adjust

| Knob | Where | Example | Purpose |
|---|---|---|---|
| `TABLE_TO_ORDER` | Flight config / env | `my_db.main.trips` | Table to rewrite; optionally qualified, quoted identifiers OK. |
| `ORDER_BY_CLAUSE` | Flight config / env | `station_id, ride_date DESC` | Sort expression(s); a leading `ORDER BY` is optional, any case. |
| `WHERE_CLAUSE` | Flight config / env | `ride_date >= '2024-01-01'` | Optional slice to rewrite; a leading `WHERE` is optional. Empty = whole table. |
| `DRY_RUN` | Flight config / env | `true` | Validate inputs and bind against the real table without changing data. |

## Run it

You need a MotherDuck account and an access token that can write the target
table. Point the knobs at any table you own:

```bash
export MOTHERDUCK_TOKEN=your_token_here
TABLE_TO_ORDER='my_db.main.trips' \
ORDER_BY_CLAUSE='station_id, ride_date' \
WHERE_CLAUSE='' \
uv run --with-requirements requirements.txt flight.py
```

Start with `DRY_RUN=true` to see the exact statements and matched row count,
then run for real. A non-zero exit means validation rejected an input or the
transaction rolled back; the table is unchanged either way.

### Deploy as a Flight

Deploy through the Flight SQL surface (`MD_CREATE_FLIGHT`, then
`MD_RUN_FLIGHT`) with:

- `source_code`: [`flight.py`](flight.py), unchanged — all knobs are config
- `requirements_txt`: [`requirements.txt`](requirements.txt)
- `config`: `TABLE_TO_ORDER`, `ORDER_BY_CLAUSE`, and optionally
  `WHERE_CLAUSE` / `DRY_RUN`

The Flight runtime injects `MOTHERDUCK_TOKEN`; make sure it can write the
target database. Create the Flight without a schedule and trigger one run with
`MD_RUN_FLIGHT` to confirm it works; per-run `config` overrides make one
deployed Flight reusable for ad-hoc re-sorts of different tables. Add a
`schedule_cron` (UTC) only when the table sees ongoing inserts worth
re-clustering on a cadence.

## Security

The three inputs end up inside SQL statements, so the Flight refuses to run
until each one round-trips through DuckDB's own tokenizer and parser as
exactly one SELECT-shaped dummy statement of the expected structure (see *How
it works*).
The Flight runs with the privileges of its MotherDuck token: scope the token
to the databases it should touch, and keep the config values under operator
control.

## Learn more

- Why sorting speeds up selective queries:
  [Sorting for Fast Selective Queries](https://duckdb.org/2025/05/14/sorting-for-fast-selective-queries)
- Flight mechanics (creating, running, scheduling, config): the MotherDuck MCP
  `get_flight_guide` tool.
- Deeper MotherDuck or DuckDB questions: the `ask_docs_question` MCP tool.
- Files in this template: [`flight.py`](flight.py) (the single-file Flight)
  and [`requirements.txt`](requirements.txt) (just `duckdb`).
