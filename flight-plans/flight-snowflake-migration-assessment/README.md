---
title: Run a Snowflake Migration Assessment From a Flight
id: flight-snowflake-migration-assessment
description: >-
  A reusable Flight that runs the md-assess migration collector against a
  Snowflake account on MotherDuck compute, then loads a redacted inventory of
  the account (objects, storage, spend, SQL feature usage, and query-history
  summaries) into a MotherDuck database with a dashboard Dive over it. Use it
  to size a Snowflake to MotherDuck migration without installing anything
  locally.
type: template
category: ingestion
features: [flights, dives]
tags: [snowflake, migrate]
prompt: >-
  I want to assess what migrating my Snowflake account to MotherDuck involves
  (what the account holds, what it costs, which Snowflake features it uses, and
  what its query workload looks like) without installing the collector on my
  own machine: run it on MotherDuck compute and load the results into a
  MotherDuck database with a dashboard. Help me adapt the "Run a Snowflake
  Migration Assessment From a Flight" recipe to my own account and use case,
  using it as a guide:
  https://motherduck.com/docs/cookbook/flight-snowflake-migration-assessment
published_date: 2026-09-29
---

# Run a Snowflake Migration Assessment From a Flight

A single-file Flight that runs
[`md-assess`](https://github.com/motherduckdb/md-migration-assessment), the
MotherDuck migration collector, against a Snowflake account and loads the
results into MotherDuck. It's the alternative to running the collector on your
laptop: no Python install, no local DuckDB file to pass around, and the result
is a MotherDuck database plus a dashboard Dive your team can open.

The collector takes an inventory of the Snowflake account: databases, schemas,
tables, views, functions and procedures, storage, warehouses, spend, which
Snowflake features are in use, and summaries of the query history that are
computed inside Snowflake. It never copies individual queries or their SQL
text. This Flight doesn't copy table data; to do that, see
[`flight-snowflake-ingest`](../flight-snowflake-ingest/).

## How it works

`flight.py` runs the pinned version of the collector in three steps:

1. **Collect.** Run `md-assess collect --source snowflake`, which writes the
   inventory to a DuckDB file on the run's temporary disk (`/tmp`). The
   collector gathers the inventory in 67 separate steps, called extractors, and
   records the outcome of each in `meta.extract_runs`. When the Snowflake role
   is missing the grant an extractor needs, that extractor is marked
   `unavailable` and the run carries on, so a role with fewer grants still
   produces a usable, partial assessment.
2. **Check the target.** Work out the MotherDuck database name: `TARGET_DB` if
   set, otherwise `md_assessment_<account_name>`, where `<account_name>` is the
   Snowflake account name without the organization prefix. If that database
   already exists, the Flight replaces it only when it holds an earlier
   assessment (it has a `meta.collections` table), so a mistyped `TARGET_DB`
   can't drop an unrelated database.
3. **Publish.** Run `md-assess publish`. It builds a redacted version of the
   database, which leaves out the SQL definitions of views, functions, and
   procedures and contains no query text. It uploads that with
   `CREATE DATABASE ... FROM '<file>'` and creates or updates a Dive over it.
   The end of the run log gives the database name, the Dive URL, and a count of
   the sensitive columns that were uploaded (object names, user and role names,
   and comments).

The full, unredacted collection is deleted from `/tmp` when the run ends,
whether it succeeds or fails. Only the redacted database is kept.

## Questions to answer

- Which Snowflake account, user, warehouse, and role should the Flight connect
  with? Use key-pair authentication; browser-based single sign-on (SSO) can't
  run in a Flight.
- Which grants does the collecting role have? `OBJECT_VIEWER` and
  `USAGE_VIEWER` give an overview of everything the account holds and what it
  costs in Snowflake. Adding `GOVERNANCE_VIEWER` provides an aggregate analysis
  of the workload, including concurrency, query shapes, the clients using
  Snowflake, usage of Snowflake-specific SQL, and which tables are actually
  read. See
  [Snowflake privileges](https://github.com/motherduckdb/md-migration-assessment#snowflake-privileges).
- The whole account, or only some databases or schemas (`SCOPE`)?
- How many days of query history should the collector look at
  (`HISTORY_DAYS`, default 30)?
- What should the MotherDuck database and Dive be called, and who in the
  organization should see them?
- Has whoever owns the Snowflake account agreed to run the collector on
  MotherDuck compute rather than inside their own environment? See
  [Caveats](#caveats).

## Caveats

- **Your Snowflake metadata is processed on MotherDuck compute.** Run locally,
  `md-assess` collects inside your own environment, and nothing reaches
  MotherDuck until you run `publish`. As a Flight, the Snowflake connection and
  the full, unredacted collection (including view and procedure definitions)
  exist on MotherDuck compute while the run lasts. Only the redacted database
  is kept, and the full collection is deleted at the end. If your security
  review requires the collection to stay in your own environment, follow the
  [local quickstart](https://github.com/motherduckdb/md-migration-assessment#quickstart-snowflake-local-mode)
  and run `md-assess publish` yourself instead.
- **An interrupted run starts over.** Each run starts with an empty `/tmp`, so
  the collector's `--resume` option doesn't apply. A run stopped by
  `max_runtime_sec` or by the 16 GB memory limit uploads nothing. Collection
  time grows with the number of objects and warehouses, not with query volume,
  but for a very large account, set a generous `max_runtime_sec`, narrow
  `SCOPE`, or start with `PROFILE=lite`.
- **Browser-based SSO doesn't work.** A Flight has no browser. Use key-pair
  authentication (`SNOWFLAKE_PRIVATE_KEY`) or, if you must, a password.
- **Use a key pair rather than a programmatic access token.** A Snowflake
  programmatic access token works as `SNOWFLAKE_PASSWORD`, but Snowflake only
  issues one when the account or user has a network policy, and a
  `TYPE = SERVICE` user can't use the temporary exception. Key-pair
  authentication has neither requirement.
- **Some counts only cover what the role can see.** Extractors that use `SHOW`
  commands (warehouses, streams, dynamic tables, integrations, and so on) list
  only the objects the collecting role has a privilege on. The report marks
  those counts as minimums, and no grant on the `SNOWFLAKE` database changes
  that. See
  [Role visibility](https://github.com/motherduckdb/md-migration-assessment#role-visibility-what-no-grant-on-snowflake-covers).
- **`ACCOUNT_USAGE` data is delayed.** The `standard` profile reads Snowflake's
  `ACCOUNT_USAGE` views, which can be a few hours behind. Objects created in
  the last hour or two may be missing.
- **Snowflake compute costs money.** The query-history summaries run on your
  warehouse. An X-Small is enough; the cost grows with how much query history
  the account has and with `HISTORY_DAYS`.
- **Re-runs replace the database.** With `REPLACE=true` (the default), each run
  drops and re-uploads the assessment database and updates the Dive in place, so
  the Dive always shows the latest collection. To keep an earlier assessment,
  set `REPLACE=false` and publish under a new `TARGET_DB`.
- **Expect a `dropped unexpected column(s)` warning.** Snowflake adds columns
  to its `SHOW` output over time. The redacted database only keeps columns the
  collector knows are safe to share, so it leaves newer ones out, and the run
  log lists the affected tables. The warning is informational and doesn't fail
  the run.
- **Pin the collector version.** `requirements.txt` installs a specific GitHub
  release of the collector. It's in Public Preview, and the layout of its output
  may change before version 1.0. To upgrade, change the release URL and re-run,
  which collects everything again from scratch.

## What you'll adjust

You adapt this template by setting Flight config values rather than editing
code. Credentials are the exception: they come from a MotherDuck Flights
secret.

| Setting | Where | Default | Purpose |
|---|---|---|---|
| `SNOWFLAKE_ACCOUNT` | config | (required) | Account identifier, for example `myorg-myaccount`. |
| `SNOWFLAKE_WAREHOUSE` | config | (user default) | Warehouse the collection queries run on. An X-Small is enough. Without one, the extractors that need a warehouse fail and are marked as such. |
| `SNOWFLAKE_ROLE` | config | (user default) | Role to collect with. Its grants decide what the collector can see. |
| `SNOWFLAKE_USER` | secret or config | (required) | Snowflake user to sign in as. |
| `SNOWFLAKE_PRIVATE_KEY` | secret | (unset) | The private key file's contents (PEM text, PKCS#8 format, encrypted or not). Preferred. The Flight writes it to a file on `/tmp` that only the run can read. |
| `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` | secret | (unset) | Passphrase for an encrypted private key. |
| `SNOWFLAKE_PASSWORD` | secret | (unset) | Password, if a key pair isn't an option. Either the key or the password is required. |
| `SECRET_NAME` | config | `snowflake_creds` | Name of the Flights secret. The Flight reads this secret's values by name, so a different secret attached to the same Flight can't override them. |
| `PROFILE` | config | `standard` | `standard` is the complete assessment. `lite` reads only `INFORMATION_SCHEMA` and `SHOW` output, needs no `ACCOUNT_USAGE` access, and sees only objects the role has privileges on. |
| `HISTORY_DAYS` | config | `30` | Days of query history to summarize, 1 to 365. |
| `SCOPE` | config | (whole account) | Comma-separated `DB` or `DB.SCHEMA` entries, for example `ANALYTICS,RAW.EVENTS`. |
| `TARGET_DB` | config | `md_assessment_<account_name>` | MotherDuck database that receives the redacted database. |
| `DIVE_TITLE` | config | `Snowflake → MotherDuck migration assessment · <ACCOUNT_NAME>` | Dive title. Re-running with the same title updates the Dive in place. |
| `REPLACE` | config | `true` | Replace an existing assessment database on re-run. Never replaces a database that isn't an assessment. |
| `MOTHERDUCK_TOKEN` | set by the Flight | (set by the Flight) | Authenticates to MotherDuck. Attached to the Flight automatically; never hard-code it. |

## Run it

You need a MotherDuck account, a Snowflake account, and a Snowflake user with a
key pair (or password) and at least the `OBJECT_VIEWER` and `USAGE_VIEWER`
database roles.

### Grant the collecting role

In Snowflake, create a dedicated role, give it the recommended grants, and
assign it to the collecting user. This set covers the full assessment. Drop
`GOVERNANCE_VIEWER` to skip the query-history analysis, or add
`SECURITY_VIEWER` for login history, client fingerprints, roles, grants,
shares, and listings (without it, those six extractors are marked
`unavailable`):

```sql
CREATE ROLE IF NOT EXISTS md_assess;
GRANT DATABASE ROLE SNOWFLAKE.OBJECT_VIEWER     TO ROLE md_assess;
GRANT DATABASE ROLE SNOWFLAKE.USAGE_VIEWER      TO ROLE md_assess;
GRANT DATABASE ROLE SNOWFLAKE.GOVERNANCE_VIEWER TO ROLE md_assess;
GRANT USAGE ON WAREHOUSE <any_small_warehouse>  TO ROLE md_assess;
GRANT ROLE md_assess TO USER <collecting_user>;
```

For key-pair authentication, generate a key and register its public half on
the user as described in Snowflake's
[key-pair authentication guide](https://docs.snowflake.com/en/user-guide/key-pair-auth).

### Deploy as a Flight

Store the credentials as a MotherDuck **Flights secret**. The simplest way is the
MotherDuck UI: open
[Settings > Secrets](https://app.motherduck.com/settings/secrets?action=create&type=flights&name=snowflake_creds&params=SNOWFLAKE_USER,SNOWFLAKE_PRIVATE_KEY),
which prefills a Flights secret named `snowflake_creds` with `SNOWFLAKE_USER`
and `SNOWFLAKE_PRIVATE_KEY` fields, and paste the contents of the private key
file as the value. Add `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` for an encrypted key,
or use `SNOWFLAKE_PASSWORD` in place of the key. From a write-enabled SQL
connection, the equivalent is:

```sql
CREATE SECRET snowflake_creds IN motherduck (
  TYPE flights,
  PARAMS MAP {
    'SNOWFLAKE_USER': '<collecting_user>',
    'SNOWFLAKE_PRIVATE_KEY': '<private_key_file_contents>'
  }
);
```

Then create the Flight with the `MD_CREATE_FLIGHT` SQL function (no deploy SQL
is checked in; adapt the arguments to your situation), passing:

- `name`: a Flight name, for example `snowflake_migration_assessment`
- `source_code`: the contents of [`flight.py`](flight.py)
- `requirements_txt`: the contents of [`requirements.txt`](requirements.txt)
- `config`: the non-secret settings, for example
  `{"SNOWFLAKE_ACCOUNT": "myorg-myaccount", "SNOWFLAKE_WAREHOUSE": "XSMALL_WH", "SNOWFLAKE_ROLE": "MD_ASSESS"}`
- `flight_secret_names`: `["snowflake_creds"]`
- `max_runtime_sec`: optional. Leave room for the collection: most accounts
  finish in minutes, but a large account with a long `HISTORY_DAYS` takes
  longer, and a run that hits the limit uploads nothing.

A MotherDuck token is attached to the Flight automatically and injected at run
time as `MOTHERDUCK_TOKEN`; no token argument is needed.

Trigger one run with `MD_RUN_FLIGHT(flight_id := ...)` (the id is returned by
`MD_CREATE_FLIGHT` and listed by `MD_FLIGHTS()`) and follow it with
`MD_GET_FLIGHT_RUN(flight_id := ..., run_number := ...)`. The end of the run log
looks like this:

```text
publish: database md:md_assessment_myaccount (74 tables, <row_count> rows)
publish: Dive 'Snowflake → MotherDuck migration assessment · MYACCOUNT' created: https://app.motherduck.com/dives/<dive_id>
publish: disclosed comment: 40 column(s)
publish: disclosed object_name: 162 column(s)
publish: disclosed user_identity: 27 column(s)
publish: excluded columns: action, column_default, condition, definition, function_definition, policy_body, procedure_definition, text, view_definition
publish: unclassified columns included: 313
publish: WARNING dropped <n> unexpected column(s) in <n> table(s): raw.alerts, raw.application_packages, ...
```

The `disclosed` lines count the sensitive columns in the upload, `excluded`
lists the columns left out, and `unclassified` counts the remaining columns,
which are almost all counts, sizes, timestamps, and statuses.

Open the Dive URL, or query the inventory directly:

```sql
-- Which extractors ran, and which were blocked by missing grants
SELECT extractor, status, rows_written, error_detail
FROM md_assessment_myaccount.meta.extract_runs
ORDER BY status, extractor;

-- Object counts and storage size
FROM md_assessment_myaccount.report.sizing;
```

If some extractors show `unavailable` because of missing grants, add the grants
and run the Flight again. Schedule it (for example weekly) only if you want the
Dive to track the account over time.

## Security

- **Only the redacted database is kept.** `md-assess publish` uploads a
  redacted database that leaves out view, function, and procedure definitions
  and never contains query text. It still names real databases, schemas,
  tables, users, and roles, and keeps comments and tag values. Check the counts
  at the end of the run log and read the collector's
  [data-handling guide](https://github.com/motherduckdb/md-migration-assessment/blob/main/docs/DATA_HANDLING.md)
  before sharing the database or the Dive beyond the people evaluating the
  migration.
- **The full collection never leaves the run.** It's written to a temporary
  directory on `/tmp` that's deleted when the run ends, whether it succeeds or
  fails.
- **Credentials live in a secret, not in config.** The user and the key or
  password come from a `TYPE flights` secret. The private key is written to a
  file only the run can read (mode `0600`), because the Snowflake connector
  expects a file path for key-pair authentication.
- **Read-only against Snowflake.** The collector only runs `SELECT` and `SHOW`
  statements, tagged with `QUERY_TAG = 'md-migration-assessment'` so they're easy
  to find in Snowflake's query history.
- **Safe re-runs.** `TARGET_DB` must be a plain SQL identifier, and an existing
  database is dropped only if it holds an earlier assessment.

## Learn more

- The collector: [md-migration-assessment](https://github.com/motherduckdb/md-migration-assessment),
  including a table of which grant each extractor needs, and the local
  quickstart.
- Copy table data once the assessment is done:
  [`flight-snowflake-ingest`](../flight-snowflake-ingest/).
- Flight mechanics (creating, running, scheduling, secrets): use the MotherDuck
  MCP `get_flight_guide` tool.
- Deeper MotherDuck or DuckDB questions: use the `ask_docs_question` MCP tool.
- Files in this template: [`flight.py`](flight.py) (the single-file Flight source)
  and [`requirements.txt`](requirements.txt) (the pinned collector release,
  `duckdb`, and `snowflake-connector-python[pandas]`).
