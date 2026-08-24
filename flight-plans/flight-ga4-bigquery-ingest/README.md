---
title: Incrementally ingest GA4 BigQuery exports into MotherDuck
id: flight-ga4-bigquery-ingest
description: >-
  A reusable Flight that incrementally copies GA4's daily BigQuery export into
  MotherDuck with a three-day healing overlap. Use when you want a scheduled,
  idempotent GA4 migration or replication path.
type: template
category: ingestion
features: [flights]
tags: [bigquery, ingest, migrate]
prompt: >-
  I want to incrementally ingest GA4's BigQuery export into MotherDuck with a
  scheduled Flight and a safe late-arrival overlap. Help me adapt the
  "Incrementally ingest GA4 BigQuery exports into MotherDuck" recipe to my own
  data and use case, using it as a guide:
  https://motherduck.com/docs/cookbook/flight-ga4-bigquery-ingest
published_date: 2026-08-24
---

# Incrementally ingest GA4 BigQuery exports into MotherDuck

This single-file Flight copies GA4's daily `events_YYYYMMDD` BigQuery export
into a MotherDuck table. It uses `bigquery_query` rather than
`bigquery_scan`: GA4 stores daily data in wildcard tables, and the Flight uses
BigQuery's `_TABLE_SUFFIX` to read only the dates it is replacing.

## How it works

Each run resolves a date window, deletes the matching MotherDuck event dates,
then inserts the corresponding BigQuery result in one transaction. A normal run
reloads the latest three event dates because GA4 can update daily exports for up
to three days after an event date. It writes one run record to
`<destination_database>.main.ga4_ingest_runs`.

## Questions to answer

- What is the GA4 source project and dataset (`analytics_<property_id>` for a
  production export)?
- Which GCP project should be billed for BigQuery query jobs?
- Which date should initialize the migration, and how often should the Flight
  run?
- Which MotherDuck database, schema, and table should receive the raw events?

## Caveats

- The BigQuery community extension runs inside the Flight's in-process DuckDB,
  not in MotherDuck cloud SQL or a read-only MCP query session.
- GA4's public sample is useful for testing, but it does not use the normal
  `analytics_<property_id>` dataset name.
- This is a raw landing table. Extract nested event parameters and model metrics
  in later staging and analytics steps.

## What you'll adjust

| Configuration | Purpose |
| --- | --- |
| `GCP_PROJECT_ID` | BigQuery billing project. |
| `GA4_SOURCE_TABLE_PATTERN` | `<project>.analytics_<property_id>.events_*`; use the public sample pattern for a smoke test. |
| `COLD_START_DATE` | First day when the destination is empty. |
| `DESTINATION_DATABASE` / `DESTINATION_SCHEMA` / `DESTINATION_TABLE` | MotherDuck raw landing relation. |
| `START_DATE` / `END_DATE` | Optional inclusive backfill range. Set both or neither. |

## Run it

For a local run, authenticate with Application Default Credentials or set
`GOOGLE_APPLICATION_CREDENTIALS` to a service-account JSON file. The principal
needs BigQuery Data Viewer on the GA4 dataset and BigQuery Job User on the
billing project.

```bash
> export MOTHERDUCK_TOKEN=<motherduck_token>
> export GCP_PROJECT_ID=<billing_project>
> export GA4_SOURCE_TABLE_PATTERN=<source_project>.analytics_<property_id>.events_*
> export COLD_START_DATE=<yyyy-mm-dd>
> uv run --with-requirements requirements.txt flight.py
```

Use Google’s public GA4 sample for an end-to-end smoke test:

```bash
> export GCP_PROJECT_ID=forward-ellipse-282119
> export GA4_SOURCE_TABLE_PATTERN=bigquery-public-data.ga4_obfuscated_sample_ecommerce.events_*
> export COLD_START_DATE=2020-12-01
> export START_DATE=2020-12-01
> export END_DATE=2020-12-01
> uv run --with-requirements requirements.txt flight.py
```

### Deploy as a Flight

Store the service-account JSON in a MotherDuck Flights secret, not Flight
config:

```sql
CREATE SECRET gcp_creds IN motherduck (
  TYPE flights,
  PARAMS MAP {
    'GOOGLE_APPLICATION_CREDENTIALS_JSON': '<service_account_json>'
  }
);
```

Create the Flight with `MD_CREATE_FLIGHT`, passing this folder's `flight.py`,
`requirements.txt`, the `gcp_creds` name in `flight_secret_names`, and the
non-secret configuration above. Run it once with `MD_RUN_FLIGHT`, inspect the
run logs and `ga4_ingest_runs`, then add a UTC cron schedule.

## Security

Keep the service-account JSON only in the `TYPE flights` secret. The Flight
writes it to a private temporary file for the BigQuery extension, then removes
that file before the run exits. Configuration identifiers are validated before
they are included in SQL; dates and BigQuery credentials are bound parameters.

## Learn more

- [GA4 BigQuery export schema](https://support.google.com/analytics/answer/7029846)
- [BigQuery Flight template](../flight-bigquery-ingest/)
- Flight mechanics, scheduling, and logs: MCP `get_flight_guide`
