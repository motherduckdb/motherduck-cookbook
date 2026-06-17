---
title: Provision User Databases and Shares
id: flight-provision-user-databases
description: >-
  An admin Flight that reads a users control table and provisions one isolated
  MotherDuck database and restricted share per active user, revoking share access
  for inactive users. Use when an application gives each user their own database
  and share, and access should follow an active/inactive flag.
type: template
category: automation
features: [flights, shares]
tags: []
prompt: >-
  My application gives each user their own isolated MotherDuck database and share, and I
  want an admin Flight that provisions them from a control table and revokes access when
  a user goes inactive. Help me adapt the "Provision User Databases and Shares" recipe
  to my own data and use case, using it as a guide:
  https://motherduck.com/docs/cookbook/flight-provision-user-databases
published_date: 2026-06-08
---

# Provision User Databases and Shares

A single-file admin Flight that turns a control table of users into per-user
MotherDuck resources. It shows the MotherDuck pattern for tenant-style
provisioning: read `(email, segment, active)` rows, give each active user an
isolated database and a restricted share, grant read access, and revoke access
when a user is marked inactive. A ledger records what each run did.

This creates account-level databases and shares, so treat it as an admin
workflow, not a disposable demo. It defaults to **`DRY_RUN=true`**: the first run
seeds a demo control table, logs the exact provisioning plan, and writes the
ledger without creating any databases, shares, or grants. Replace the demo users
with real MotherDuck usernames and set `DRY_RUN=false` to provision for real.

Everything is driven by Flight config, so you adapt it by setting config values,
not by editing `flight.py`.

## How it works

`flight.py` runs a fixed sequence; the config values only change its inputs:

1. Connect to MotherDuck (`md:`), create `PROVISION_DATABASE` and its schema, seed
   the demo control table if it does not exist, and create the ledger table.
2. Read the users ordered by email.
3. For each **active** user, derive `DATABASE_PREFIX + slug(email)` and create the
   database, a `USER_SCHEMA` schema, and a `profile` table, then create a
   restricted share (`ACCESS RESTRICTED, VISIBILITY HIDDEN, UPDATE AUTOMATIC`) and
   grant read access to the username.
4. For each **inactive** user, revoke read access on that user's share.
5. Append one ledger row per user, including whether the run was a dry run.

When `DRY_RUN` is true, steps 3 and 4 only log the intended action; the ledger
still records the plan, so you can review exactly what a live run would do.

## Questions to answer

- Which control table lists the users, and does it have `email`, `segment`, and `active` columns (`USERS_TABLE`, `PROVISION_DATABASE`, `PROVISION_SCHEMA`)?
- Are the `email` values valid MotherDuck usernames in the same sharing scope?
- How should per-user databases and shares be named (`DATABASE_PREFIX`, `SHARE_SUFFIX`)?
- What does each user's database need beyond the demo profile table (`USER_SCHEMA` plus your own tables)?
- Which admin or service account token should own the created resources?
- Run on demand, or on a schedule once the control table is trusted?
- Validating the plan first (`DRY_RUN=true`), or ready to provision for real (`DRY_RUN=false`)?

## Caveats

- **These are account-level resources.** The databases and shares are visible
  beyond `PROVISION_DATABASE`. Treat this as an admin workflow with a scoped token.
- **`DRY_RUN` defaults to true on purpose.** A first deploy never creates shares
  for placeholder users. Replace the seeded demo users with real MotherDuck
  usernames before setting `DRY_RUN=false`.
- **Deprovisioning is revoke-only.** Inactive users lose share access, but their
  database is not dropped. Dropping user databases is a separate policy decision.
- **Usernames must be valid.** A grant or revoke for an address that is not a
  MotherDuck user in the same sharing scope is skipped and logged, not fatal, so
  one bad row does not stop the run.
- **Re-running is idempotent for resources, not the ledger.** Active users use
  `IF NOT EXISTS`/`OR REPLACE`, but the ledger appends one row per user per run.
- **Keep the token out of config.** Select a token on the Flight so
  `MOTHERDUCK_TOKEN` is injected at runtime; do not place it in `config`.

## What you'll adjust

Every knob is a config/env value read at the top of `flight.py`. Set them as
Flight config, not by editing code.

| Config key | Default | Purpose |
|---|---|---|
| `DRY_RUN` | `true` | When true, log the plan and write the ledger without creating databases, shares, or grants. Set `false` to provision for real. |
| `PROVISION_DATABASE` | `flights_demo` | Database holding the users control table and the ledger. Created if missing. Validated as a SQL identifier. |
| `PROVISION_SCHEMA` | `main` | Schema for the control and ledger tables. Validated as a SQL identifier. |
| `USERS_TABLE` | `flight_users` | Control table of `(email, segment, active)`. Seeded with demo rows on the first run if missing. Validated as a SQL identifier. |
| `LEDGER_TABLE` | `user_database_map` | Audit table recording what each run did per user. Validated as a SQL identifier. |
| `DATABASE_PREFIX` | `user_dw_` | Prefix for each per-user database (`<prefix><email-slug>`). Validated as a SQL identifier. |
| `SHARE_SUFFIX` | `_share` | Suffix for each per-user share (`<database><suffix>`). Validated as a SQL identifier. |
| `USER_SCHEMA` | `app` | Schema created inside each user database for the profile table. Validated as a SQL identifier. |
| `MOTHERDUCK_TOKEN` | (Flight-injected) | Auth. Use an admin or service account token allowed to create databases and shares and to grant/revoke. Never put it in config. |

## Run it

You need a MotherDuck account and a token allowed to create databases and shares
and to grant or revoke share access. Prefer a service account token so the
created resources do not depend on a person's account lifecycle.

To see the plan locally without creating anything, run the file directly. With
the default `DRY_RUN=true` it seeds the demo control table, logs the plan, and
writes the ledger:

```bash
export MOTHERDUCK_TOKEN=your_token_here
uv run --with duckdb==1.5.2 flight.py
```

Once the control table holds real usernames, provision for real:

```bash
DRY_RUN=false uv run --with duckdb==1.5.2 flight.py
```

### Deploy as a Flight

Create the Flight with the `MD_CREATE_FLIGHT` SQL function (no deploy SQL is
checked in; adapt the arguments to your situation), passing:

- `name`: a Flight name, for example `provision_user_databases`
- `source_code`: the contents of [`flight.py`](flight.py)
- `requirements_txt`: the contents of [`requirements.txt`](requirements.txt)
- `config`: the keys from [What you'll adjust](#what-youll-adjust) you want to
  override (omit any you are keeping at default)

A MotherDuck token is attached to the Flight automatically and injected at run
time as `MOTHERDUCK_TOKEN`; no token argument is needed. This Flight creates
databases and shares, so deploy it from an account allowed to do both.

Create the Flight without a schedule, trigger one manual run with
`MD_RUN_FLIGHT(flight_id := ...)` (the id is returned by `MD_CREATE_FLIGHT` and
listed by `MD_FLIGHTS()`) while `DRY_RUN` is `true`, and read the ledger and run
logs to confirm the plan. Then point `USERS_TABLE` at real usernames (or replace
the seeded demo rows), set `DRY_RUN=false`, and run again to provision. Add a
schedule by updating the Flight's `schedule_cron` with `MD_UPDATE_FLIGHT` only
once you trust the control table; schedule updates are metadata-only and do not
create a new Flight version.

## Security

- **Scoped admin token.** Use a token allowed only to create databases and shares
  and to grant/revoke, ideally a service account that owns the created resources.
- **Identifier validation.** Config-supplied names (`PROVISION_DATABASE`,
  `PROVISION_SCHEMA`, `USERS_TABLE`, `LEDGER_TABLE`, `DATABASE_PREFIX`,
  `SHARE_SUFFIX`, `USER_SCHEMA`) are checked against `^[A-Za-z_][A-Za-z0-9_]*$`
  before any SQL runs.
- **Quoted dynamic identifiers.** Per-user database, share, schema, and username
  values are derived at runtime and quoted with `ident()`, which escapes embedded
  double quotes, since they cannot be parameterized.
- **Parameterized data.** The `email` and `segment` values are bound as parameters
  in the profile table and the ledger insert, never string-formatted into SQL.

## Learn more

- Flight mechanics (creating, running, scheduling): use the MotherDuck MCP
  `get_flight_guide` tool.
- Sharing, `CREATE SHARE`, and `GRANT`/`REVOKE READ ON SHARE` semantics: use the
  `ask_docs_question` MCP tool.
- Files in this template: [`flight.py`](flight.py) (the single-file Flight source)
  and [`requirements.txt`](requirements.txt) (its one dependency, `duckdb`).
