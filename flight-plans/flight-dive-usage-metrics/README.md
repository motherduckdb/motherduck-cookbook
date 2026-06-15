---
title: Map Data Usage and Relationships Across Dives
id: flight-dive-usage-metrics
description: >-
  A config-driven Flight that scans the shared Dives in your MotherDuck
  organization, parses the SQL embedded in each Dive, and refreshes tables of the
  most-referenced data objects plus the relationships between them: per-Dive
  dependency edges, table co-occurrence, and join keys mined from the SQL. Use when
  you want a scheduled, trend-aware map of which data objects your Dives lean on
  and how they connect.
type: template
features: [flights, dives]
tags: []
---

# Map Data Usage and Relationships Across Dives

A single-file Flight that turns the SQL inside your organization's Dives into
queryable metadata. Each run enumerates the Dives, extracts the SQL each one
embeds, parses that SQL with DuckDB's own parser, and records both what the Dives
reference and how those objects relate. It writes one timestamped batch per run,
so you can watch how your Dives' data footprint shifts over time.

It produces four tables (each with a `_latest` view):

- **Usage** (`dive_usage_metrics`): how many Dives reference each table, database,
  schema, and column.
- **Dependency edges** (`dive_object_edges`): one row per Dive-to-object
  reference, for impact analysis ("which Dives break if this table changes?").
- **Co-occurrence** (`dive_table_cooccurrence`): tables that appear together in the
  same statement, a signal for de-facto data domains and candidate joins.
- **Join keys** (`dive_join_edges`): column-to-column join keys mined from the SQL
  (JOIN conditions and WHERE equalities), an ERD inferred from how Dives actually
  query.

It reads Dives read-only and writes only to the target database you configure.

Everything is driven by Flight config, so you adapt it by setting config values,
not by editing `flight.py`.

## How it works

`flight.py` runs a fixed sequence; the config values only change its inputs:

1. Connect to MotherDuck (`md:`).
2. List Dives with `MD_LIST_DIVES(include_org_shares := true)` when
   `INCLUDE_ORG_SHARES` is set (otherwise `MD_LIST_DIVES()`), honoring `DIVE_LIMIT`.
3. For each Dive, call `MD_GET_DIVE(id => ?::UUID)` to get its React/JSX `content`.
4. Extract candidate SQL strings from that source: capture template-literal and
   quoted-string contents, neutralize JS `${...}` interpolations into a harmless
   placeholder literal, and keep only strings that start like SQL (`SELECT` /
   `WITH` / `FROM`).
5. Parse each candidate with `json_serialize_sql(?)` (the SQL is bound as a
   parameter), skip anything that does not parse, and walk the resulting JSON AST
   for `BASE_TABLE` nodes (database / schema / table) and `COLUMN_REF` nodes
   (column names). Tables are kept only when fully qualified
   (`database.schema.table`); bare CTE and alias names that the parser also
   reports as `BASE_TABLE` are skipped, and SQL keywords surfaced as columns (for
   example `CURRENT_DATE`) are dropped from the column metric.
6. When `BUILD_RELATIONSHIPS` is on, also analyze each statement for
   relationships: collect its fully-qualified tables (resolving CTE references and
   table aliases to real tables), emit every co-occurring table pair, and mine join
   keys from every `column = column` equality (JOIN conditions and WHERE clauses
   alike), resolving each side's qualifier through the alias and CTE maps.
7. Aggregate across all Dives: `dive_count` counts the distinct Dives referencing
   each object (an object referenced many times in one Dive counts once),
   `reference_count` is the raw total of occurrences. Co-occurrence and join edges
   track both a distinct-Dive count and a per-statement `query_count`.
8. Append one timestamped batch to each output table and refresh its
   `<table>_latest` view (the database and schema are created on first run). Writes
   use chunked bulk `INSERT`s so large edge tables stay on a fast path.

A progress line is logged every 200 Dives (and on the last one), so a long
org-wide scan reports `processed N/total` instead of going silent for minutes.

## Using the relationship tables

```sql
-- Impact analysis: which Dives reference a given table?
SELECT dive_title
FROM dive_metrics.main.dive_object_edges_latest
WHERE object_type = 'table' AND object_name = 'mdw.main.current_organizations'
ORDER BY dive_title;

-- Tables most often queried together:
SELECT table_a, table_b, dive_count, query_count
FROM dive_metrics.main.dive_table_cooccurrence_latest
ORDER BY dive_count DESC, query_count DESC
LIMIT 20;

-- Inferred join keys (an ERD mined from real usage):
SELECT left_table, left_column, right_table, right_column, dive_count
FROM dive_metrics.main.dive_join_edges_latest
ORDER BY dive_count DESC
LIMIT 20;
```

## Questions to answer

- Should the metrics cover every Dive shared in the organization (`INCLUDE_ORG_SHARES=true`), or only the token owner's own Dives?
- Where should the metrics live (`TARGET_DATABASE`, `TARGET_SCHEMA`, `METRICS_TABLE`), and is that database writable by the chosen token?
- Which service account token can read the Dives you care about and write the target database?
- How often should the snapshot refresh (daily or weekly), given how often Dives change?
- Do you need a quick bounded first run (`DIVE_LIMIT`) before scanning everything?

## Caveats

- **Tables are limited to fully-qualified names.** Only `database.schema.table`
  references are counted. CTE and alias names (which the parser also reports as
  `BASE_TABLE`) and partially-qualified references are skipped, so the table metric
  reflects real tables rather than query-local names.
- **Columns are attributed by name only.** The AST does not always resolve which
  table a bare column belongs to, so columns are counted by name across all Dives
  (a column named `id` in two unrelated tables counts as one `id`), and SQL
  keywords the parser surfaces as columns (for example `CURRENT_DATE`) are filtered
  out with a small denylist.
- **`SELECT *` is not expanded.** A `SELECT *` records the table reference but
  contributes no column rows, so column counts undercount wildcard-heavy Dives.
- **Interpolated or unparseable queries are skipped.** A statement that still
  fails `json_serialize_sql` after interpolation neutralization (heavily nested
  `${...}`, partial SQL fragments, dialect quirks) is skipped, and the run logs
  the skip count. A Dive whose source cannot be read is also counted as skipped.
- **Org-shares behavior.** Verified against live MotherDuck: `MD_LIST_DIVES`
  accepts a named `include_org_shares` argument
  (`MD_LIST_DIVES(include_org_shares := true)`), and its full signature is
  `MD_LIST_DIVES(offset, include_org_shares, limit)`. With the argument it returns
  every Dive shared in the organization; without it (or `MD_LIST_DIVES()`) it
  returns only the caller's own Dives. The Flight falls back to the no-argument
  call if a MotherDuck build does not support the argument. `MD_GET_DIVE` requires
  the id as a named UUID argument (`MD_GET_DIVE(id => ?::UUID)`) and accepts only a
  literal, so it is called once per Dive id rather than in a lateral join.
- **Co-occurrence is same-statement.** Two tables co-occur when they appear in the
  same parsed statement, a strong signal that they are joined or unioned, not
  merely that they sit in the same Dive. CTE references and aliases are resolved to
  the real tables first.
- **Join keys are mined heuristically.** Edges come from `column = column`
  equalities in JOIN conditions and WHERE clauses, with aliases and CTE references
  resolved to real tables. This captures real foreign-key-like relationships, but
  can include the occasional non-join equality (for example a filter comparing two
  columns) and misses joins expressed without an equality (ranges, function calls).
  Treat the counts as evidence of how Dives relate tables, not as declared
  constraints. Set `BUILD_RELATIONSHIPS=false` to skip the three relationship
  tables entirely.
- **Metrics reflect Dive source SQL, not executed query frequency.** A table that
  appears in many Dives ranks high even if those Dives are rarely opened. For how
  often queries actually run, see `MD_INFORMATION_SCHEMA.QUERY_HISTORY`, but note
  that query history records executed SQL and cannot be attributed back to a
  specific Dive.

## What you'll adjust

Every knob is a config/env value read at the top of `flight.py`. Set them as
Flight config, not by editing code.

| Config key | Default | Purpose |
|---|---|---|
| `TARGET_DATABASE` | `dive_metrics` | Database that holds the metrics table. Created if missing. Validated as a SQL identifier. |
| `TARGET_SCHEMA` | `main` | Schema for the metrics table. Created if missing. Validated as a SQL identifier. |
| `METRICS_TABLE` | `dive_usage_metrics` | Usage metrics table name. A companion `<table>_latest` view exposes the newest run. Validated as a SQL identifier. |
| `BUILD_RELATIONSHIPS` | `true` | Also build the dependency, co-occurrence, and join-key tables. Set false to produce only the usage metrics. |
| `EDGES_TABLE` | `dive_object_edges` | Dependency-edge table (one row per Dive-to-object reference). Validated as a SQL identifier. |
| `COOCCURRENCE_TABLE` | `dive_table_cooccurrence` | Table co-occurrence table (table pairs sharing a statement). Validated as a SQL identifier. |
| `JOIN_TABLE` | `dive_join_edges` | Mined join-key table (column-to-column edges). Validated as a SQL identifier. |
| `INCLUDE_ORG_SHARES` | `true` | When true, scan every Dive shared in the organization via `MD_LIST_DIVES(include_org_shares := true)`. When false, scan only the token owner's own Dives. |
| `DIVE_LIMIT` | (unset) | Optional cap on how many Dives to scan, useful for a quick first run. Unset or `0` means no limit. |
| `MOTHERDUCK_TOKEN` | (Flight-injected) | Auth. Use a token that can read the Dives you want counted and write `TARGET_DATABASE`. Never put it in config. |

## Run it

You need a MotherDuck account and an access token. This template has a
credential-free smoke test: with only a `MOTHERDUCK_TOKEN`, a fresh run produces
real metrics from your own and your organization's shared Dives. No other
credentials or setup are needed, because Dives, `MD_LIST_DIVES`, `MD_GET_DIVE`,
and `json_serialize_sql` are all built into MotherDuck.

```bash
export MOTHERDUCK_TOKEN=your_token_here
# optional: bound the first run while you confirm the output
export DIVE_LIMIT=25
uv run --with-requirements requirements.txt flight.py
```

This enumerates the Dives, parses the SQL in each, and writes the metric rows to
`dive_metrics.main.dive_usage_metrics` (creating the database and schema on first
run). It prints how many Dives it scanned, how many SQL statements it parsed and
skipped, and the row count per object type. Query the result:

```sql
SELECT object_type, object_name, dive_count, reference_count
FROM dive_metrics.main.dive_usage_metrics_latest
WHERE object_type = 'table'
ORDER BY dive_count DESC, reference_count DESC
LIMIT 20;
```

### Deploy as a Flight

Create the Flight with the `MD_CREATE_FLIGHT` SQL function (no deploy SQL is
checked in; adapt the arguments to your situation), passing:

- `name`: a Flight name, for example `dive_usage_metrics`
- `source_code`: the contents of [`flight.py`](flight.py)
- `requirements_txt`: the contents of [`requirements.txt`](requirements.txt)
- `config`: the keys from [What you'll adjust](#what-youll-adjust) you want to
  override (omit any you are keeping at default)

A MotherDuck token is attached to the Flight automatically and injected at run
time as `MOTHERDUCK_TOKEN`; no token argument is needed. The run can only count
Dives that token can read, so deploy from an account that sees them.

Create the Flight without a schedule, trigger one manual run with
`MD_RUN_FLIGHT(flight_id := ...)` (the id is returned by `MD_CREATE_FLIGHT` and
listed by `MD_FLIGHTS()`), and read the run logs and the `_latest` view to
confirm the metrics look right. Then add a schedule by updating the Flight's
`schedule_cron` with `MD_UPDATE_FLIGHT`. A daily run
(`0 6 * * *`) or weekly run (`0 6 * * 1`) is a reasonable cadence, since Dive
source SQL changes slowly. Schedule updates are metadata-only and do not create a
new Flight version.

## Security

- **Identifier validation.** Config-supplied names (`TARGET_DATABASE`,
  `TARGET_SCHEMA`, `METRICS_TABLE`) are checked against `^[A-Za-z_][A-Za-z0-9_]*$`
  before any SQL runs, because they flow into `CREATE`/`INSERT` statements that
  cannot be parameterized.
- **Parameterized writes.** Every data value (object names, edge endpoints, counts)
  and the Dive id passed to `MD_GET_DIVE`, and each candidate SQL string passed to
  `json_serialize_sql`, are bound as parameters, never string-formatted into SQL.
  Rows are written in chunked bulk multi-row `INSERT`s.
- **Read-only against Dives.** The Flight only reads Dives (`MD_LIST_DIVES`,
  `MD_GET_DIVE`) and writes solely to `TARGET_DATABASE` (the usage, dependency,
  co-occurrence, and join-key tables). The automatically attached token needs read
  access to the Dives you want counted and write access to the target database.

## Learn more

- Flight mechanics (creating, running, scheduling): use the MotherDuck MCP
  `get_flight_guide` tool.
- Dive functions (`MD_LIST_DIVES`, `MD_GET_DIVE`) and SQL parsing
  (`json_serialize_sql`): use the `ask_docs_question` MCP tool.
- Files in this template: [`flight.py`](flight.py) (the single-file Flight source)
  and [`requirements.txt`](requirements.txt) (its one dependency, `duckdb`).
