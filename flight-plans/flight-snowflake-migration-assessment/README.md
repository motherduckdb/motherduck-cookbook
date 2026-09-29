---
title: Run a Snowflake Migration Assessment From a Flight
id: flight-snowflake-migration-assessment
description: >-
  A reusable Flight that runs the md-assess migration collector against a
  Snowflake account on MotherDuck compute, then loads the reduced inventory
  (catalog, sizing, features, workload aggregates) into a MotherDuck database
  with a dashboard Dive over it. Use it to size a Snowflake to MotherDuck
  migration without installing anything locally.
type: template
category: ingestion
features: [flights, dives]
tags: [snowflake, migrate]
prompt: >-
  I want to assess what migrating my Snowflake account to MotherDuck involves
  (catalog, sizing, feature usage, and workload profile) without installing the
  collector on my own machine: run it on MotherDuck compute and land the
  inventory in a MotherDuck database with a dashboard. Help me adapt the "Run a
  Snowflake Migration Assessment From a Flight" recipe to my own account and use
  case, using it as a guide:
  https://motherduck.com/docs/cookbook/flight-snowflake-migration-assessment
published_date: 2026-09-29
---

# Run a Snowflake Migration Assessment From a Flight

A single-file Flight that runs
[`md-assess`](https://github.com/motherduckdb/md-migration-assessment), the
MotherDuck migration collector, against a Snowflake account and lands the
inventory in MotherDuck. It's the turnkey alternative to running the collector
on your laptop: no Python install, no local DuckDB file to pass around, and the
result is a MotherDuck database plus a dashboard Dive your team can open.

The collector inventories the Snowflake deployment (databases, schemas, tables,
views, routines, storage, warehouses, spend, feature usage, and server-side
aggregates over `QUERY_HISTORY`) and builds factual `report.*` summaries. It
never collects per-query rows or workload query text. This Flight doesn't move
table data; to copy tables, see
[`flight-snowflake-ingest`](../flight-snowflake-ingest/).

## How it works

`flight.py` runs the pinned collector's CLI in three steps:

1. **Collect.** Run `md-assess collect --source snowflake` into a DuckDB file on
   the run's `/tmp` scratch disk. Every extractor records its coverage in
   `meta.extract_runs`; an extractor whose Snowflake grant is missing lands as
   `unavailable` instead of failing the run, so any grant tier produces a valid,
   partial assessment.
2. **Guard.** Resolve the target database name (`TARGET_DB`, or the collector's
   default `md_assessment_<account_name>`, built from the account name without the organization prefix). If that database exists, the Flight
   replaces it only when it's an earlier assessment (it has a
   `meta.collections` table), so a mistyped `TARGET_DB` can't drop an unrelated
   database.
3. **Publish.** Run `md-assess publish`, which builds the reduced *handoff* (no
   view or routine bodies, no query text), uploads it with
   `CREATE DATABASE ... FROM '<file>'`, and creates or updates a Dive over it.
   The run log ends with the database name, the Dive URL, and a disclosure
   summary of which sensitive column classes (object names, identities,
   comments, tag values) were uploaded.

The full private collection is deleted from `/tmp` when the run ends, whether it
succeeds or fails. Only the handoff is persisted.

## Questions to answer

- Which Snowflake account, user, warehouse, and role should the Flight connect
  with? Key-pair auth is the non-interactive default; external-browser SSO can't
  run in a Flight.
- Which grant tier does the collecting role have? `OBJECT_VIEWER` and
  `USAGE_VIEWER` size the estate and the bill; adding `GOVERNANCE_VIEWER` gives
  the decision-grade workload aggregates. See
  [Snowflake privileges](https://github.com/motherduckdb/md-migration-assessment#snowflake-privileges).
- Account-wide, or limited to some databases or schemas (`SCOPE`)?
- How far back should the workload extracts look (`HISTORY_DAYS`, default 30)?
- What should the MotherDuck database and Dive be called, and who in the
  organization should see them?
- Has whoever owns the Snowflake account agreed to run the collector on
  MotherDuck compute rather than inside their own environment? See
  [Caveats](#caveats).

## Caveats

- **This changes the collector's trust model.** Run locally, `md-assess`
  collects inside your environment and nothing reaches MotherDuck until you run
  `publish`. As a Flight, the Snowflake connection and the full private
  collection (including view and routine bodies) exist on MotherDuck compute for
  the length of the run. Only the reduced handoff is kept, and the private file
  is deleted at the end, but if your security review requires the collection to
  stay in your own environment, run the
  [local quickstart](https://github.com/motherduckdb/md-migration-assessment#quickstart-snowflake-local-mode)
  and `md-assess publish` instead.
- **No resume across runs.** Each run starts from an empty `/tmp`, so
  `md-assess collect --resume` doesn't apply. A run killed by `max_runtime_sec`
  or the 16 GB memory ceiling uploads nothing. Output size scales with catalog
  size and warehouse count rather than query volume, but on a very large
  catalog, set a generous `max_runtime_sec`, narrow `SCOPE`, or start with
  `PROFILE=lite`.
- **External-browser SSO doesn't work.** A Flight has no browser. Use key-pair
  auth (`SNOWFLAKE_PRIVATE_KEY`) or, if you must, a password.
- **Prefer key-pair over programmatic access tokens.** A Snowflake programmatic
  access token works as `SNOWFLAKE_PASSWORD`, but Snowflake only issues one
  when the account or user has a network policy, and a `TYPE = SERVICE` user
  can't take the temporary network-policy bypass. Key-pair auth has neither
  requirement.
- **Some inventories are role-visibility bound.** `SHOW`-based extracts
  (warehouses, streams, dynamic tables, integrations, and so on) list only what
  the collecting role can see. The report marks these as lower bounds; no grant
  on the `SNOWFLAKE` database changes that. See
  [Role visibility](https://github.com/motherduckdb/md-migration-assessment#role-visibility-what-no-grant-on-snowflake-covers).
- **`ACCOUNT_USAGE` lags.** The `standard` profile reads `ACCOUNT_USAGE` views,
  which trail live state by up to a few hours. Objects created in the last hour
  or two may be missing.
- **Snowflake compute costs money.** The aggregate scans over `QUERY_HISTORY`
  run on your warehouse. An X-Small is enough; cost scales with your own history
  volume and `HISTORY_DAYS`.
- **Re-runs replace the database.** With `REPLACE=true` (the default), each run
  drops and re-uploads the assessment database and updates the Dive in place, so
  the Dive always shows the latest collection. Set `REPLACE=false` to keep an
  earlier snapshot and publish under a new `TARGET_DB` instead.
- **Expect a `dropped unexpected column(s)` warning.** Snowflake adds columns
  to `SHOW` output over time. The collector's handoff keeps only the columns it
  has classified, so newer ones are dropped from the upload (fail-closed) and
  the run log names the affected tables. The warning is informational; it
  doesn't fail the run.
- **Pin the collector.** `requirements.txt` installs a versioned GitHub release
  asset. The collector is in Public Preview and output schemas may change before
  1.0; to upgrade, change the release URL and re-run, which re-collects from
  scratch.

## What you'll adjust

Every knob is read from Flight config or env, so you adapt this template by
setting config values rather than editing code. Credentials are the exception:
they come from a MotherDuck Flights secret.

| Knob | Where | Default | Purpose |
|---|---|---|---|
| `SNOWFLAKE_ACCOUNT` | config | (required) | Account identifier, for example `myorg-myaccount`. |
| `SNOWFLAKE_WAREHOUSE` | config | (user default) | Warehouse for the collection queries. An X-Small is enough. Without one, extracts that need an active warehouse fail and are recorded as such. |
| `SNOWFLAKE_ROLE` | config | (user default) | Role to collect with. Its grants decide coverage. |
| `SNOWFLAKE_USER` | secret or config | (required) | Snowflake login user. |
| `SNOWFLAKE_PRIVATE_KEY` | secret | (unset) | PEM text of an unencrypted or encrypted PKCS#8 private key. Preferred. Written to a `0600` file on `/tmp` for the connector. |
| `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` | secret | (unset) | Passphrase for an encrypted private key. |
| `SNOWFLAKE_PASSWORD` | secret | (unset) | Password auth, if key-pair isn't an option. One of the key or the password is required. |
| `SECRET_NAME` | config | `snowflake_creds` | Name of the Flights secret. The Flight reads `<SECRET_NAME>_<PARAM>` first, so another secret on the Flight can't shadow these params. |
| `PROFILE` | config | `standard` | `standard` is the complete assessment. `lite` reads only `INFORMATION_SCHEMA` and `SHOW`, needs no `ACCOUNT_USAGE` access, and sees only objects the role has privileges on. |
| `HISTORY_DAYS` | config | `30` | Lookback window for the workload extracts, 1 to 365. |
| `SCOPE` | config | (account-wide) | Comma-separated `DB` or `DB.SCHEMA` entries, for example `ANALYTICS,RAW.EVENTS`. |
| `TARGET_DB` | config | `md_assessment_<account_name>` | MotherDuck database that receives the handoff. |
| `DIVE_TITLE` | config | `Snowflake → MotherDuck migration assessment · <ACCOUNT_NAME>` | Dive title. Re-running with the same title updates the Dive in place. |
| `REPLACE` | config | `true` | Replace an existing assessment database on re-run. Never replaces a database that isn't an assessment. |
| `MOTHERDUCK_TOKEN` | Flight-injected | (Flight-injected) | Auth for MotherDuck. Attached to the Flight automatically; never hard-code it. |

## Run it

You need a MotherDuck account, a Snowflake account, and a Snowflake user with a
key pair (or password) and at least the `OBJECT_VIEWER` and `USAGE_VIEWER`
database roles.

### Grant the collecting role

In Snowflake, grant the recommended tiers to a dedicated role and assign it to
the collecting user. This is the decision-grade set; drop `GOVERNANCE_VIEWER` to
skip the workload aggregates, or add `SECURITY_VIEWER` for login history,
client fingerprints, roles, grants, shares, and listings (without it, those six
extractors land as `unavailable`):

```sql
CREATE ROLE IF NOT EXISTS md_assess;
GRANT DATABASE ROLE SNOWFLAKE.OBJECT_VIEWER     TO ROLE md_assess;
GRANT DATABASE ROLE SNOWFLAKE.USAGE_VIEWER      TO ROLE md_assess;
GRANT DATABASE ROLE SNOWFLAKE.GOVERNANCE_VIEWER TO ROLE md_assess;
GRANT USAGE ON WAREHOUSE <any_small_warehouse>  TO ROLE md_assess;
GRANT ROLE md_assess TO USER <collecting_user>;
```

For key-pair auth, generate a key and register its public half on the user as
described in Snowflake's
[key-pair authentication guide](https://docs.snowflake.com/en/user-guide/key-pair-auth).

### Deploy as a Flight

Store the credentials as a MotherDuck **Flights secret**. The simplest way is the
MotherDuck UI: open
[Settings > Secrets](https://app.motherduck.com/settings/secrets?action=create&type=flights&name=snowflake_creds&params=SNOWFLAKE_USER,SNOWFLAKE_PRIVATE_KEY),
which prefills a Flights secret named `snowflake_creds` with `SNOWFLAKE_USER`
and `SNOWFLAKE_PRIVATE_KEY` params, and paste the PEM text of the private key
as the value. Add `SNOWFLAKE_PRIVATE_KEY_PASSPHRASE` for an encrypted key, or
use `SNOWFLAKE_PASSWORD` in place of the key. From a write-enabled SQL
connection, the equivalent is:

```sql
CREATE SECRET snowflake_creds IN motherduck (
  TYPE flights,
  PARAMS MAP {
    'SNOWFLAKE_USER': '<collecting_user>',
    'SNOWFLAKE_PRIVATE_KEY': '<pem_text_of_private_key>'
  }
);
```

Then create the Flight with the `MD_CREATE_FLIGHT` SQL function (no deploy SQL
is checked in; adapt the arguments to your situation), passing:

- `name`: a Flight name, for example `snowflake_migration_assessment`
- `source_code`: the contents of [`flight.py`](flight.py)
- `requirements_txt`: the contents of [`requirements.txt`](requirements.txt)
- `config`: the non-secret knobs, for example
  `{"SNOWFLAKE_ACCOUNT": "myorg-myaccount", "SNOWFLAKE_WAREHOUSE": "XSMALL_WH", "SNOWFLAKE_ROLE": "MD_ASSESS"}`
- `flight_secret_names`: `["snowflake_creds"]`
- `max_runtime_sec`: optional. Leave room for the collection: most accounts
  finish in minutes, but a large catalog with a long `HISTORY_DAYS` takes longer,
  and a run that hits the cap uploads nothing.

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

Open the Dive URL, or query the inventory directly:

```sql
-- Which extractors ran, and which were blocked by missing grants
SELECT extractor, status, rows_written, error_detail
FROM md_assessment_myaccount.meta.extract_runs
ORDER BY status, extractor;

-- Catalog and storage sizing
FROM md_assessment_myaccount.report.sizing;
```

If extractors show `unavailable` for missing grants, widen the grants and run the
Flight again. Schedule it (for example weekly) only if you want the Dive to track
the account over time.

## Security

- **Only the handoff is persisted.** `md-assess publish` uploads the reduced
  handoff database, which drops view and routine bodies and never contains query
  text. It still names real databases, schemas, tables, users, and roles, and
  keeps comments and tag values. Review the disclosure summary in the run log and
  the collector's
  [data-handling guide](https://github.com/motherduckdb/md-migration-assessment/blob/main/docs/DATA_HANDLING.md)
  before sharing the database or the Dive beyond the people evaluating the
  migration.
- **The private collection never leaves the run.** It's written under a
  per-run temporary directory on `/tmp` and deleted in a `finally` block, along
  with everything else in that directory.
- **Credentials in a secret, not config.** The user and key or password come from
  a `TYPE flights` secret, read through the namespaced `<SECRET_NAME>_<PARAM>`
  variables. The private key is written to a `0600` file on `/tmp` because the
  Snowflake connector takes key-pair auth as a path.
- **Read-only against Snowflake.** The collector only runs `SELECT` and `SHOW`
  statements, tagged with `QUERY_TAG = 'md-migration-assessment'` so they're easy
  to find in Snowflake's query history.
- **Safe re-runs.** `TARGET_DB` is validated as an identifier, and an existing
  database is dropped only if it's an earlier assessment.

## Learn more

- The collector: [md-migration-assessment](https://github.com/motherduckdb/md-migration-assessment),
  including the least-privilege matrix of which grant each extractor needs, and
  the local quickstart.
- Copy table data once the assessment is done:
  [`flight-snowflake-ingest`](../flight-snowflake-ingest/).
- Flight mechanics (creating, running, scheduling, secrets): use the MotherDuck
  MCP `get_flight_guide` tool.
- Deeper MotherDuck or DuckDB questions: use the `ask_docs_question` MCP tool.
- Files in this template: [`flight.py`](flight.py) (the single-file Flight source)
  and [`requirements.txt`](requirements.txt) (the pinned collector release,
  `duckdb`, and `snowflake-connector-python[pandas]`).
