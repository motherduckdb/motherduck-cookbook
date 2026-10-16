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

Secrets created with `CREATE SECRET ... IN MOTHERDUCK` belong to the account
that created them, so a service account can't use an admin's S3 secret. This
Flight runs as an admin and writes the same secrets into every service account,
so a new service account or a rotated key is picked up on the next run.

The admin token is the only credential involved. The secret values are stored
once, as a Flight secret, and copied from there.

## How it works

Each run of [`flight.py`](flight.py):

1. Reads the Flight secrets named in `SECRETS`. Each one's parameters are the
   `CREATE SECRET` options, for example `TYPE`, `KEY_ID`, `SECRET`, `REGION`
   and `SCOPE`.
2. Lists the organization's active service accounts with the REST API
   (`GET /v1/users?is_service_account=true`).
3. For each service account, mints a token that expires after five minutes,
   connects as that account, runs `CREATE OR REPLACE SECRET <name> IN
   MOTHERDUCK (...)` for every secret, and revokes the token.

The secret gets the same name in every service account as the Flight secret it
came from.

## Questions to answer

- Which secrets should every service account have, and with which options
  (`TYPE`, `KEY_ID`, `SECRET`, `REGION`, `SCOPE`, ...)?
- All service accounts, or only some (`SERVICE_ACCOUNTS`)?
- How often should rotated keys propagate? Hourly (`0 * * * *`) is a good default.
- Which admin owns the Flight? Its owner's token is the one the Flight runs with.

## Caveats

- **Service accounts only.** An admin can mint tokens for service accounts but
  not for human users, and there is no API or CLI to act as another user, so
  this can't write secrets into human users' accounts.
- **Every run replaces every secret.** Secret values can't be read back, so the
  Flight can't tell whether a secret is current. Changes a service account makes
  to a synced secret are overwritten on the next run.
- **Removing a secret from `SECRETS` doesn't delete it.** Run `DROP SECRET` as
  each service account to remove it.
- **Secret names can't be prefixes of each other.** Parameters arrive as
  `<secret name>_<KEY>` environment variables, so syncing both `s3` and
  `s3_prod` would mix their options. Pick distinct names such as `s3_dev` and
  `s3_prod`.
- **The run fails if any account fails.** The other accounts are still synced,
  and the log names the ones that failed.

## What you'll adjust

| Config key | Default | Purpose |
|---|---|---|
| `SECRETS` | (required) | Comma-separated names of the Flight secrets to sync, for example `shared_s3,shared_gcs`. Each must also be attached to the Flight. |
| `SERVICE_ACCOUNTS` | (all) | Comma-separated usernames to limit the sync to. Leave empty for every active service account. |
| `DRY_RUN` | `true` | List the service accounts that would be synced without minting tokens or writing secrets. Set `false` to sync. |
| `MOTHERDUCK_TOKEN` | (Flight-injected) | The Flight owner's token. The owner must be an admin. |

## Run it

Store each secret to sync as a Flight secret whose parameters are the options
you would pass to `CREATE SECRET`. In the UI, open
[Settings > Secrets](https://app.motherduck.com/settings/secrets?action=create&type=flights&name=shared_s3&params=TYPE,KEY_ID,SECRET,REGION,SCOPE)
and fill in the values. In SQL:

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

To try it locally, export an admin token and the secret's parameters the way
the Flight runtime would. With the default `DRY_RUN=true` this only lists the
service accounts:

```bash
export MOTHERDUCK_TOKEN=your_admin_token
export SECRETS=shared_s3 shared_s3_TYPE=s3 shared_s3_KEY_ID=AKIA... shared_s3_SECRET=...
uv run --with-requirements requirements.txt flight.py
```

### Deploy as a Flight

Create the Flight with `MD_CREATE_FLIGHT`, passing:

- `name`: for example `sync_service_account_secrets`
- `source_code`: the contents of [`flight.py`](flight.py)
- `requirements_txt`: the contents of [`requirements.txt`](requirements.txt)
- `flight_secret_names`: the secrets to sync, for example `['shared_s3']`
- `config`: `{'SECRETS': 'shared_s3', 'SERVICE_ACCOUNTS': '', 'DRY_RUN': 'true'}`

Run it once with `MD_RUN_FLIGHT(flight_id := ...)` and check in the run logs
that the listed service accounts are the ones you expect. Then set `DRY_RUN` to
`false` and add the schedule with `MD_UPDATE_FLIGHT` (`schedule_cron := '0 * *
* *'`). Passing `config` to `MD_UPDATE_FLIGHT` replaces the whole map, so
include `SECRETS` and `SERVICE_ACCOUNTS` again.

## Security

- **The admin token can mint tokens for every service account.** Only the
  Flight's owner can change or run it, so treat ownership of this Flight like
  admin access.
- **Tokens are short-lived and revoked.** Each one expires after five minutes,
  the shortest lifetime the API allows, and is deleted as soon as that account
  is synced. Tokens are never printed.
- **Secret values stay out of logs.** They live in the Flight secret, and are
  masked in any error message the Flight prints.
- **SQL is built safely.** Secret and parameter names must be plain SQL
  identifiers, and values are passed as escaped string literals.

## Learn more

- Flight mechanics (secrets, config, schedules, run logs): the MotherDuck MCP
  `get_flight_guide` tool.
- `CREATE SECRET` options for each storage type, and the REST API endpoints for
  users and tokens: the `ask_docs_question` MCP tool, or the
  [CREATE SECRET](https://motherduck.com/docs/sql-reference/motherduck-sql-reference/create-secret/)
  and [REST API](https://motherduck.com/docs/sql-reference/rest-api/motherduck-rest-api/)
  reference.
