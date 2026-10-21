---
title: "Flight recipe: Apache Iceberg catalog → MotherDuck copy"
id: flight-iceberg-catalog-ingest
description: >-
  A Flight that copies tables from any Iceberg REST catalog MotherDuck supports
  (Amazon S3 Tables, AWS Glue, Apache Polaris, Cloudflare R2 Data Catalog,
  Databricks Unity Catalog, Snowflake Horizon) into native MotherDuck tables,
  one CREATE OR REPLACE per table, with a CATALOG preset that fills in the
  documented options, namespace/table filters, retries, row-count checks and a
  per-table load log. Use it for a scheduled Iceberg to MotherDuck copy.
type: template
category: ingestion
features: [flights]
tags: [ingest, s3, cloudflare, databricks, snowflake]
prompt: >-
  I have Apache Iceberg tables in a REST catalog and want to use them in
  MotherDuck, either queried in place or copied into MotherDuck on a schedule.
  Help me adapt the "Flight recipe: Apache Iceberg catalog → MotherDuck copy"
  recipe to my own catalog and use case, using it as a guide. Ask me which
  catalog I have first, then only what that catalog needs; I'll put my
  credentials in a MotherDuck secret, not in chat:
  https://motherduck.com/docs/cookbook/flight-iceberg-catalog-ingest
published_date: 2026-10-20
---

# Flight recipe: Apache Iceberg catalog → MotherDuck copy

A single-file Flight that copies tables from an Apache Iceberg REST catalog into
native MotherDuck tables. It works with every catalog MotherDuck documents
(Amazon S3 Tables, AWS Glue with or without Lake Formation, Apache Polaris,
Cloudflare R2 Data Catalog, Databricks Unity Catalog and Snowflake Horizon) and
with any other Iceberg REST endpoint. One `CATALOG` setting picks a preset that
fills in the documented options and checks the settings before anything is
attached. Every run is a full refresh: each table is rebuilt with one
`CREATE OR REPLACE TABLE ... AS SELECT`.

**You may not need a Flight.** To query the tables, attach the catalog once with
`CREATE DATABASE ... (TYPE ICEBERG, ...)` and query them in place; the attached
database persists in your workspace, and reads run on MotherDuck's compute. Use
this Flight when you want a scheduled copy in native MotherDuck tables, for
example for faster repeated queries, to share the data (Iceberg catalogs can't be
shared), or to decouple dashboards from the lake.

## How it works

[`flight.py`](flight.py) runs a fixed sequence:

1. **Resolve settings** from config and the `CATALOG` preset (see
   [Catalog presets](#catalog-presets)). A setting the preset can't use stops the
   run with a message naming it, for example an S3 Tables `WAREHOUSE` that isn't a
   table bucket ARN, a Glue `WAREHOUSE` that isn't a 12-digit account ID, or an
   `ENDPOINT` set for S3 Tables or Glue, where the endpoint is derived.
2. **Check the secret.** The MotherDuck secret named by `ICEBERG_SECRET` must
   exist and have the type the catalog needs: `S3` for S3 Tables and Glue,
   `ICEBERG` for the rest. The check reads secret names and types from
   `duckdb_secrets()`, never values. A missing secret fails with the documented
   `CREATE SECRET` shape for that catalog.
3. **Attach the catalog** as a persisted MotherDuck database named `CATALOG_DB`:

   ```sql
   CREATE OR REPLACE DATABASE iceberg_catalog (
       TYPE ICEBERG,
       "secret" my_iceberg_secret,
       endpoint 'https://catalog.example.com',   -- or endpoint_type for S3 Tables / Glue
       warehouse 'my_warehouse',
       default_schema 'analytics',
       read_only true
   );
   ```

   `default_region` and `access_delegation_mode` are added when set. The attach
   is read-only unless `READ_ONLY=false`. If the catalog comes up as an error
   catalog (`MD_ATTACHED_DATABASES().is_error_catalog`), the run stops with the
   catalog's `error_message` and a hint for that catalog, and a database created
   by this run is dropped again. An existing `CATALOG_DB` that isn't an Iceberg
   database is never replaced. The attach is re-run every time because
   `endpoint`, `warehouse`, `endpoint_type` and `read_only` can't be changed with
   `ALTER DATABASE`, so config changes take effect on the next run.
4. **Discover and filter.** Namespaces come from `duckdb_schemas()` and tables
   from `duckdb_tables()`. An included namespace or table that doesn't exist,
   or two tables that would land on the same target name, stops the run before
   anything is copied.
5. **Copy each table** with
   `CREATE OR REPLACE TABLE <TARGET_DB>.<namespace>.<table> AS SELECT * FROM <CATALOG_DB>.<namespace>.<table>`,
   retrying transient errors with jittered backoff. Authentication and
   permission errors (401, 403, Lake Formation and Unity Catalog credential
   errors) fail at once. The row count in MotherDuck is compared with a fresh
   count in the catalog; a difference is a warning.
6. **Log** one row per table to `<TARGET_DB>._meta.load_log` (flight id, run id,
   catalog, source and target table, status, rows in target and source,
   attempts, timing, error). The run exits 1 if any table failed; the other
   tables still copy.

## Questions to answer

Settle these before creating a secret or a Flight.

1. **Which catalog?** Amazon S3 Tables, AWS Glue (with Lake Formation or not),
   Apache Polaris, Cloudflare R2 Data Catalog, Databricks Unity Catalog,
   Snowflake Horizon, or another Iceberg REST catalog. This sets `CATALOG` and
   decides every question below.
2. **Query in place, or copy on a schedule?** If querying in place is enough,
   attach the catalog once with `CREATE DATABASE ... TYPE ICEBERG` and stop.
3. **The catalog's identifiers**, only those the preset needs (see the table
   below): the S3 Tables bucket ARN; the AWS account ID and a Glue database; the
   Polaris, Unity Catalog or Snowflake endpoint and catalog/warehouse name; or
   the R2 catalog URI from the bucket's R2 Data Catalog settings.
4. **Is there a MotherDuck secret with the credentials yet?** If not, create one
   of the documented type (see [Credentials](#credentials)). Enter the values in
   the MotherDuck UI or a local DuckDB session, never in chat, config or source.
5. **What to copy:** all namespaces, some namespaces, or specific tables? A
   namespace that exists is needed to open the catalog (`DEFAULT_SCHEMA`); if
   unsure, the first included namespace is used.
6. **Where it lands:** the target database (default `iceberg_copy`); tables keep
   their namespace as the schema name. Existing tables with the same names are
   replaced.
7. **Once, or on a schedule** (cron, UTC)?

## Catalog presets

Each preset only fills in documented options and checks. Everything else is the
same for all catalogs.

| `CATALOG` | Secret type | Required settings | Gotchas |
|---|---|---|---|
| `s3_tables` (Amazon S3 Tables) | `S3`: `KEY_ID`, `SECRET`, `REGION` (SigV4) | `WAREHOUSE` = table bucket ARN `arn:aws:s3tables:<region>:<account_id>:bucket/<name>`. Leave `ENDPOINT` empty: `endpoint_type 's3_tables'` is set and the endpoint is derived from the ARN. | The keys need S3 Tables catalog access (`s3tables:*`) in the bucket's account. |
| `glue` (AWS Glue) | `S3`: `KEY_ID`, `SECRET`, `REGION` (SigV4) | `WAREHOUSE` = 12-digit AWS account ID. `DEFAULT_SCHEMA` = a Glue database. Leave `ENDPOINT` empty: `endpoint_type 'glue'` is set and the endpoint comes from the secret's `REGION`. | The principal needs `glue:GetCatalog`, `GetDatabase(s)`, `GetTable(s)`, plus `s3:GetObject` (and `kms:Decrypt` for SSE-KMS) without Lake Formation. **Lake Formation is validated for reads only**: it needs four one-time settings, `lakeformation:GetDataAccess`, and `DESCRIBE`/`SELECT` grants on whole tables (column, row or cell filters fail). Writing back through Glue needs an explicit table `location`; this Flight only reads. |
| `polaris` (Apache Polaris) | `ICEBERG`: `CLIENT_ID`, `CLIENT_SECRET`, `OAUTH2_SERVER_URI` (OAuth2), or `TOKEN` | `ENDPOINT` (Polaris REST URL), `WAREHOUSE` = Polaris catalog name | The endpoint can live in the secret (`ENDPOINT`) instead. |
| `r2` (Cloudflare R2 Data Catalog) | `ICEBERG`: `TOKEN` = Cloudflare API token with R2 Data Catalog permission | `ENDPOINT` = `https://catalog.cloudflarestorage.com/<account_id>/<bucket>`. `WAREHOUSE` = `<account_id>_<bucket>`, derived from the endpoint when empty. | An R2 object-only token or an S3 key fails (`401` wrong token type, `403` missing Data Catalog permission). Enable the catalog on the bucket and create at least one namespace first; a new catalog is empty. Credential vending covers the data files, so no separate S3 secret is needed. |
| `unity` (Databricks Unity Catalog) | `ICEBERG`: `TOKEN` = personal access token | `ENDPOINT` = `https://<workspace-url>/api/2.1/unity-catalog/iceberg-rest`, `WAREHOUSE` = UC catalog name, `DEFAULT_SCHEMA` = UC schema | **External locations only**: Databricks vends credentials only for tables on external storage; managed-storage tables fail with `Missing or invalid credentials`. Delta tables need Iceberg reads enabled (`IcebergCompatV2`, no deletion vectors). `HTTP 404`/`NoSuchKey` on a `.parquet` file: run `MSCK REPAIR TABLE ... SYNC METADATA` in Databricks. |
| `horizon` (Snowflake Horizon) | `ICEBERG`: `CLIENT_ID`, `CLIENT_SECRET`, `OAUTH2_SERVER_URI`, `OAUTH2_SCOPE 'session:role:<role>'` | `ENDPOINT`, `WAREHOUSE` | **External volumes only.** The write options in the Snowflake docs (`disable_multi_table_commit`, `stage_create_tables`, ...) are for writing back and aren't needed for this read-only copy. |
| `rest` (any other, the default) | `ICEBERG`: OAuth2 or `TOKEN` | `ENDPOINT` (or `ENDPOINT` in the secret), `WAREHOUSE` if the catalog uses one | |

For every catalog, `DEFAULT_SCHEMA` must name a namespace that exists, and reads
are limited to tables on S3, S3-compatible storage (including R2), S3 Tables and
GCS.

## Credentials

The credentials live in a **MotherDuck secret** of the type in the table, created
once in **Settings → Secrets** in the MotherDuck UI or with `CREATE SECRET ... IN MOTHERDUCK`.
The Flight references it by name (`ICEBERG_SECRET`) in the `CREATE DATABASE`
options. It is not a Flights secret: don't pass it in `flight_secret_names`.
The documented shapes:

```sql
-- S3 Tables and Glue: S3 secret (SigV4)
CREATE SECRET my_iceberg_secret IN MOTHERDUCK (
    TYPE S3, KEY_ID '<aws_access_key_id>', SECRET '<aws_secret_access_key>', REGION '<region>'
);

-- Polaris, Snowflake Horizon, other REST catalogs: OAuth2 client credentials
CREATE SECRET my_iceberg_secret IN MOTHERDUCK (
    TYPE ICEBERG, CLIENT_ID '<client_id>', CLIENT_SECRET '<client_secret>',
    OAUTH2_SERVER_URI '<token_endpoint>'   -- Horizon also: OAUTH2_SCOPE 'session:role:<role>'
);

-- R2 Data Catalog, Unity Catalog, other REST catalogs: bearer token
CREATE SECRET my_iceberg_secret IN MOTHERDUCK (TYPE ICEBERG, TOKEN '<token>');
```

To keep keys out of shell history, run it from the DuckDB CLI with
`getenv('AWS_ACCESS_KEY_ID')` and similar instead of literal values. To rotate,
create a new secret and point `ICEBERG_SECRET` at it.

## Caveats

- **Ask before setting up.** Agents following this recipe should work through
  [Questions to answer](#questions-to-answer) with the user first, and only then open the
  secret form (the MCP `create_secret` tool) or create the Flight.
- **Querying in place needs no Flight.** See the note at the top. For a single
  table by path, without a catalog, `iceberg_scan` also works.
- **Full refresh re-reads every table on every run.** Cost scales with table
  size, not change volume. Scope runs with `INCLUDED_NAMESPACES` or `INCLUDED_TABLES`.
- **`CATALOG_DB` is replaced on every run** with the configured settings, so
  use a name you don't also attach by hand with different options. A database
  with that name that isn't an Iceberg catalog stops the run instead.
- **Not carried over:** partition specs, sort orders, snapshot history and table
  properties. **Not removed:** a table dropped from the catalog stays in
  MotherDuck until you drop it.
- **Row-count drift is a warning, not a failure**, because the catalog can get
  a new snapshot mid-copy.
- **Per-run overrides need stored keys.** `MD_RUN_FLIGHT` (or the MCP
  `run_flight` tool) can only override config keys already stored on the Flight,
  so create the Flight with every key below, set to `""` where the default is fine.

### Troubleshooting

| Symptom | Fix |
|---|---|
| `WAREHOUSE for CATALOG=s3_tables must be a table bucket ARN` | Use the table bucket ARN, not the bucket name or an `s3://` path |
| `WAREHOUSE for CATALOG=glue must be the 12-digit AWS account ID` | Use the account ID that owns the Glue Data Catalog |
| `No MotherDuck secret named ...` | Create the secret (type per the table) or fix `ICEBERG_SECRET`; the message lists secrets of the right type by name |
| `Secret ... is TYPE S3, but ... needs a TYPE ICEBERG secret` | S3 Tables and Glue use S3 secrets; every other catalog uses Iceberg secrets |
| `Attaching ... failed: ... Forbidden_403` | Credentials lack permission for that catalog; see the preset's gotchas |
| `Attaching ... failed: ...` naming the namespace | Set `DEFAULT_SCHEMA` to a namespace that exists |
| `INCLUDED_NAMESPACES not found` / `INCLUDED_TABLES not found` | A typo, or a namespace excluded elsewhere; the message lists what's visible |
| `CATALOG_DB ... is already a 'motherduck' database` | Pick another `CATALOG_DB`; the Flight never replaces a native database |
| Lake Formation errors (`Insufficient Lake Formation permissions`, `FULL SELECT ...`) | See the Lake Formation steps in the Iceberg docs |

## What you'll adjust

No code edits are required. Everything is read from Flight config and the
MotherDuck secret. Empty values fall back to the defaults.

| Knob | Default | Purpose |
|---|---|---|
| `CATALOG` | `rest` | Preset: `s3_tables`, `glue`, `polaris`, `r2`, `unity`, `horizon` or `rest` |
| `ICEBERG_SECRET` | (required) | Name of the MotherDuck `S3` or `ICEBERG` secret |
| `ENDPOINT` | derived for `s3_tables`/`glue` | Iceberg REST catalog URL |
| `WAREHOUSE` | see the preset table | Bucket ARN, account ID, catalog name or `<account_id>_<bucket>` |
| `DEFAULT_SCHEMA` | first included namespace, else `default` | Namespace the attach opens with; must exist |
| `DEFAULT_REGION` | your org's region | Per-catalog region override |
| `ACCESS_DELEGATION_MODE` | catalog default (`vended_credentials`) | `vended_credentials` or `none` (use the secret's credentials directly) |
| `READ_ONLY` | `true` | Attach the catalog read-only |
| `CATALOG_DB` | `iceberg_catalog` | Name of the persisted catalog database |
| `INCLUDED_NAMESPACES` | (all) | Comma-separated namespaces to copy |
| `EXCLUDED_NAMESPACES` | (none) | Comma-separated namespaces to skip; wins over includes |
| `INCLUDED_TABLES` | (all) | Comma-separated `namespace.table`, or a bare `table` (any namespace) |
| `EXCLUDED_TABLES` | (none) | Same format; wins over includes |
| `TARGET_DB` | `iceberg_copy` | MotherDuck database to copy into (created if missing) |
| `MAX_RETRIES` | `3` | Attempts per table on transient errors |
| `MOTHERDUCK_HOST` | unset | Override the MotherDuck host (for example a non-production one). Leave unset normally. |

Names are matched case-insensitively and may be double-quoted
(`"Sales"."orders"`).

## Run it

With the secret in place, a local run needs only a MotherDuck token:

```bash
export MOTHERDUCK_TOKEN=your_token_here
export CATALOG=r2
export ICEBERG_SECRET=r2_iceberg
export ENDPOINT=https://catalog.cloudflarestorage.com/<account_id>/<bucket_name>
export INCLUDED_NAMESPACES=analytics
uv run --with-requirements requirements.txt flight.py
```

### Deploy as a Flight

Create the Flight with the `MD_CREATE_FLIGHT` SQL function, passing:

- `name`: for example `iceberg_catalog_copy`
- `source_code`: [`flight.py`](flight.py)
- `requirements_txt`: [`requirements.txt`](requirements.txt)
- `config`: `CATALOG`, `ICEBERG_SECRET`, the settings the preset needs, and
  every other knob above set to its value or `""`

No `flight_secret_names` are needed: the catalog secret is a MotherDuck `S3` or
`ICEBERG` secret referenced by name, and a MotherDuck token is attached to the
Flight automatically and injected at run time as `MOTHERDUCK_TOKEN`. Run once
with `MD_RUN_FLIGHT(flight_id := ...)`, then check that every table has a
`succeeded` row in `<TARGET_DB>._meta.load_log` with `rows_in_target` equal to
`rows_in_source`. Then add a schedule if one is wanted. Use long-lived
credentials (not session tokens) for scheduled runs.

## Security

- **Credentials stay in the MotherDuck secret.** Config holds only the secret's
  name and catalog settings. The `CREATE DATABASE` statement carries no
  credential, the secret check reads names and types only, and nothing the
  Flight logs comes from a secret value.
- **Read-only attach by default.** The Flight never writes to the catalog; set
  `READ_ONLY=false` only if you reuse `CATALOG_DB` for writes.
- **Least privilege at the source.** Give the secret's principal read access to
  the tables you copy, nothing more. With Lake Formation, grant `DESCRIBE` and
  `SELECT` on exactly those tables.
- **Access control doesn't follow the data.** Catalog grants protect the lake,
  not the MotherDuck copy. Copy only what the MotherDuck readers may see.
- **Quoted identifiers and literals.** Namespace and table names are quoted, and
  config values are validated or quoted, before they reach SQL.

## Status

Not yet tested against a live Iceberg catalog. Tested so far (Oct 20, 2026):

- Offline unit tests with a stubbed `duckdb`: preset resolution and validation
  for every catalog, the generated `CREATE DATABASE` for every preset (quoting,
  no credentials), include/exclude logic, namespace auto-pick, empty-config
  defaults, and the copy loop and load log against a fake connection.
- MotherDuck staging Flight runs with deliberately bad settings: a missing
  secret, a bogus endpoint, and an S3 Tables preset with a non-ARN warehouse
  each failed early with the catalog-specific message.

Not tested: a successful attach and copy for any catalog, discovery against a
real catalog, Lake Formation, Unity Catalog external tables, and Snowflake Horizon.

## Learn more

- Iceberg catalogs, options and per-catalog setup:
  [Apache Iceberg in MotherDuck](https://motherduck.com/docs/integrations/file-formats/apache-iceberg/).
- Secret parameters: [`CREATE SECRET`](https://motherduck.com/docs/sql-reference/motherduck-sql-reference/create-secret/).
- S3 Tables only, with a sample bucket: [flight-s3tables-iceberg-ingest](../flight-s3tables-iceberg-ingest).
- Flight mechanics (create, run, schedule): MCP `get_flight_guide`. Deeper
  Iceberg or secret questions: MCP `ask_docs_question`.
- Files: [`flight.py`](flight.py), [`requirements.txt`](requirements.txt)
  (`duckdb` only; the attach runs on MotherDuck's compute, so no Iceberg
  extension is needed in the Flight).
