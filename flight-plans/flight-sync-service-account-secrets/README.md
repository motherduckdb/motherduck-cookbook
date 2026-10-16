---
title: Sync Secrets to Service Accounts
id: flight-sync-service-account-secrets
description: >-
  An admin Flight that copies S3, GCS, R2, Azure, or other DuckDB secrets into
  every service account in the organization on a schedule. Use when service
  accounts need the same cloud storage credentials and the admin token should
  be the only credential to manage.
type: template
category: automation
features: [flights, admin_api]
tags: []
prompt: >-
  I want every service account in my MotherDuck organization to have the same
  S3 secret, kept in sync on a schedule with only my admin token. Help me adapt
  the "Sync Secrets to Service Accounts" recipe to my own secrets and service
  accounts, using it as a guide:
  https://motherduck.com/docs/cookbook/flight-sync-service-account-secrets
published_date: 2026-10-16
---

# Sync Secrets to Service Accounts

MotherDuck secrets belong to the account that created them, so a service
account can't use an admin's S3 secret. This Flight runs as an admin and writes
the same secrets into every service account. It needs only the admin token.

## How it works

For each active service account, [`flight.py`](flight.py) mints a token that
expires after five minutes, runs `CREATE OR REPLACE SECRET ... IN MOTHERDUCK` as
that account, and revokes the token. The secret values come from Flight
secrets: each parameter is a `CREATE SECRET` option.

## Questions to answer

- Which secrets should every service account have, with which options?
- How often should a rotated key reach the service accounts?

## Caveats

- Human users are not covered: admins can only mint tokens for service accounts.
- Every run replaces every secret. Removing a name from `SECRETS` does not drop
  the secret from the service accounts.
- Parameters arrive as `<secret name>_<KEY>`, so one secret name can't be a
  prefix of another (`s3` and `s3_prod`).

## What you'll adjust

| Config key | Purpose |
|---|---|
| `SECRETS` | Comma-separated Flight secret names to sync, for example `shared_s3`. |

## Run it

Create a Flight secret for each secret to sync, with the `CREATE SECRET` options
as parameters:

```sql
CREATE SECRET shared_s3 IN MOTHERDUCK (
  TYPE flights,
  PARAMS MAP {
    'TYPE': 's3',
    'KEY_ID': 'AKIA...',
    'SECRET': '...',
    'REGION': 'us-east-1',
    'SCOPE': 's3://my-bucket'
  }
);
```

### Deploy as a Flight

As an admin, call `MD_CREATE_FLIGHT` with the contents of
[`flight.py`](flight.py) and [`requirements.txt`](requirements.txt),
`flight_secret_names := ['shared_s3']`, `config := {'SECRETS': 'shared_s3'}`
and `schedule_cron := '0 * * * *'`. The run logs list each service account it
synced.

## Learn more

- Flight secrets, config and schedules: the MotherDuck MCP `get_flight_guide` tool.
- `CREATE SECRET` options per storage type: the `ask_docs_question` MCP tool.
