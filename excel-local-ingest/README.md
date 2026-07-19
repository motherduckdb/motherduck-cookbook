---
title: Load a Local Excel File into MotherDuck
id: excel-local-ingest
description: >-
  A standalone Python script that loads a sheet from a local Excel (.xlsx) file
  into a MotherDuck table with the DuckDB client and read_xlsx. Use when a
  workbook lives on your machine and you want it in MotherDuck without converting
  to CSV or waiting on UI upload support.
type: example
category: ingestion
features: []
tags: [python, ingest]
prompt: >-
  I have an Excel .xlsx file on my machine and I want to load a sheet into a
  MotherDuck table with the DuckDB Python client and read_xlsx, without converting
  to CSV. Help me adapt the "Load a Local Excel File into MotherDuck" recipe to my
  own data and use case, using it as a guide:
  https://motherduck.com/docs/cookbook/excel-local-ingest
published_date: 2026-07-19
---

# Load a Local Excel File into MotherDuck

A standalone Python script that loads one worksheet of a local Excel `.xlsx` file
into a MotherDuck table. DuckDB reads Excel natively with `read_xlsx` (from the
`excel` extension), so you point at the file on your machine and the rows land in
MotherDuck. No CSV conversion, and no waiting on the UI **Add data** uploader,
which supports CSV, Parquet, and JSON but not `.xlsx` today.

The local DuckDB process reads the file and writes the result to your MotherDuck
account, so the workbook never has to leave your machine as anything but table
rows. The default reads the `sample_orders.xlsx` file next to the script and
builds `excel_demo.main.excel_orders` in your account.

## How it works

`load_excel.py` runs a short sequence; environment variables change its inputs:

1. Connect to MotherDuck (`md:`), reading the token from `MOTHERDUCK_TOKEN`.
2. `CREATE DATABASE`/`CREATE SCHEMA IF NOT EXISTS` for the destination.
3. Fully replace the destination with `CREATE OR REPLACE TABLE ... AS SELECT * FROM read_xlsx(...)`,
   reading the local file and inferring column types from the sheet.
4. Print the row count and a small preview.

The `excel` extension autoloads on first use, so no `INSTALL`/`LOAD` is needed.
The default load is a `SELECT *` pass-through; to shape the data, replace the
`SELECT *` with your own projection.

## Questions to answer

- Which file (`SOURCE_XLSX`), and which worksheet (`SHEET`, default `orders`; empty for the first sheet)?
- Target MotherDuck database, schema, and table (`DESTINATION_*`); is letting the script create them acceptable?
- Should the load infer types, or read everything as text (`ALL_VARCHAR`) for a messy sheet?

## Caveats

- **This is a client-side script, not a Flight.** A Flight runs in MotherDuck's
  cloud and cannot reach your local disk. For a workbook that already lives in S3
  or at an HTTPS URL, use the [Ingest an Excel Workbook from S3 on a Schedule](https://motherduck.com/docs/cookbook/flight-excel-s3-ingest)
  Flight instead.
- **One sheet per run.** A run loads a single worksheet. Set `SHEET`, or leave it
  empty to take the first sheet. Run again with a different `DESTINATION_TABLE` to
  load another sheet.
- **Full refresh.** Each run replaces the whole table, which suits a workbook you
  re-export in full.
- **Numbers come in as `DOUBLE`.** `read_xlsx` infers types from the cells and
  reads every numeric cell as a double, so an integer-looking column like
  `order_id` loads as `1001.0`, not `1001`. Cast it downstream (for example
  `CAST(order_id AS BIGINT)`) if you need integers.
- **Type inference for messy columns.** For a column with mixed text and numbers,
  set `ALL_VARCHAR` to `true` to read everything as text, then cast the columns
  you need, or clean the sheet.

## What you'll adjust

Every knob is an environment variable read at the top of `load_excel.py`.

| Variable | Default | Purpose |
|---|---|---|
| `MOTHERDUCK_TOKEN` | (required) | Your MotherDuck access token. |
| `SOURCE_XLSX` | bundled `sample_orders.xlsx` | Path to the local workbook. |
| `SHEET` | `orders` | Worksheet to load. Leave empty to take the first sheet. |
| `ALL_VARCHAR` | `false` | Read every cell as text instead of inferring types. |
| `DESTINATION_DATABASE` | `excel_demo` | MotherDuck database to build into. Created if missing. Validated as a SQL identifier. |
| `DESTINATION_SCHEMA` | `main` | Schema for the destination. Validated as a SQL identifier. |
| `DESTINATION_TABLE` | `excel_orders` | Destination table name. Validated as a SQL identifier. |

## Run it

You need a MotherDuck account and an access token. From this directory:

```bash
export MOTHERDUCK_TOKEN=your_token_here
uv run --with duckdb==1.5.2 load_excel.py
```

That creates `excel_demo.main.excel_orders` from the `orders` sheet and prints a
preview. Override any default inline, for example to load your own file and sheet:

```bash
SOURCE_XLSX=~/data/q3_report.xlsx SHEET=Summary DESTINATION_TABLE=q3_summary \
  uv run --with duckdb==1.5.2 load_excel.py
```

## Security

- **Identifier validation.** `DESTINATION_DATABASE`, `DESTINATION_SCHEMA`, and
  `DESTINATION_TABLE` flow into `CREATE` statements that cannot be parameterized,
  so each is checked against `^[A-Za-z_][A-Za-z0-9_]*$` before any SQL runs.
- **Parameterized and escaped inputs.** The file path is a bound parameter to
  `read_xlsx`; the `SHEET` name is inlined as a literal with single quotes doubled.

## Learn more

- Deeper MotherDuck or DuckDB questions (`read_xlsx` options, sheets, type inference):
  use the `ask_docs_question` MCP tool, or see the
  [Excel file-format docs](https://motherduck.com/docs/integrations/file-formats/excel/).
- Files in this example: [`load_excel.py`](https://github.com/motherduckdb/motherduck-cookbook/blob/main/excel-local-ingest/load_excel.py)
  (the script), [`requirements.txt`](https://github.com/motherduckdb/motherduck-cookbook/blob/main/excel-local-ingest/requirements.txt)
  (its one dependency, `duckdb`), and [`sample_orders.xlsx`](https://github.com/motherduckdb/motherduck-cookbook/blob/main/excel-local-ingest/sample_orders.xlsx)
  (the default sample workbook, with `orders` and `regions` sheets).
