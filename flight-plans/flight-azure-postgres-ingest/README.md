---
title: Mirror Azure Database for PostgreSQL into MotherDuck With a Flight
id: flight-azure-postgres-ingest
description: >-
  Deploy the shared Postgres ingest Flight against an Azure Database for
  PostgreSQL flexible server: a full-refresh copy of each base table into
  MotherDuck, with optional incremental tables, firewall rules for the Flight
  egress IPs, checks for Azure connection mistakes, retries, and an audit log.
  Use it to mirror Azure Postgres into MotherDuck once or on a schedule.
type: template
category: ingestion
features: [flights]
tags: [postgres, ingest]
prompt: >-
  I want to ingest my Azure Database for PostgreSQL data into MotherDuck and keep
  it in sync on a schedule. Help me adapt the "Mirror Azure Database for
  PostgreSQL into MotherDuck With a Flight" recipe to my own data and use case,
  using it as a guide:
  https://motherduck.com/docs/cookbook/flight-azure-postgres-ingest
published_date: 2026-10-20
---

# Mirror Azure Database for PostgreSQL into MotherDuck With a Flight

Copy the tables of an Azure Database for PostgreSQL flexible server into a
MotherDuck database, once or on a schedule. This recipe deploys the shared
[Postgres ingest Flight](../flight-postgres-ingest) unchanged. This page covers
what's specific to Azure: networking and firewall rules, the port and user name
to use, TLS, and Microsoft Entra ID.

## How it works

The Flight attaches Azure Postgres read-only through the DuckDB `postgres`
extension and copies each base table with one
`CREATE OR REPLACE TABLE ... AS SELECT *`. Tables you list in
`INCREMENTAL_TABLES` instead read only rows at or after the last watermark and
`MERGE` them on a key. Each table is retried with backoff and recorded in
`<TARGET_DATABASE>.main.flight_tracker`. See
[How it works](../flight-postgres-ingest#how-it-works) on the shared recipe for
the full sequence.

On a `*.postgres.database.azure.com` host, the Flight stops before connecting,
with a message that names the fix, when:

- The host is a private-access (VNet) host (`*.private.postgres.database.azure.com`).
  Flights can't reach it.
- The port is `6432`, the server's built-in PgBouncer. Connect directly on `5432`.
- The user name has the `user@servername` form of the retired Single Server.
  Flexible server expects the plain role name. Microsoft Entra user names such as
  `alice@contoso.com` pass this check.

TLS is required by default (`sslmode=require`), which matches flexible server's
enforced TLS.

## Questions to answer

- Is it a flexible server? Single Server is retired, and its user names stop the
  run.
- Does the server use **Public access**, and does its firewall allow the Flight
  egress IPs for your MotherDuck region? See [Networking](#networking).
- Where does the connection go? Store it in a MotherDuck Flights secret named
  `azure_pg`, either as `DATABASE_URL`
  (`postgresql://<user>:<password>@<server>.postgres.database.azure.com:5432/<database>?sslmode=require`)
  or as `PGHOST`, `PGDATABASE`, `PGUSER`, and `PGPASSWORD`. Use the plain role
  name and a read-only role. Never put the password in chat, config, or code.
- Password or Microsoft Entra ID? The Flight supports password authentication
  as-is. Entra ID needs a code change (see [Microsoft Entra ID](#microsoft-entra-id)).
- What to copy: all non-system schemas (the default), or specific schemas and
  tables? Should any large tables be incremental, on which watermark and key
  columns?
- Which MotherDuck database receives the copy? This recipe uses
  `azure_pg_mirror`. Tables with the same names in that database are replaced.
- Once, or on a schedule (cron, UTC)?

## Networking

Flights connect from a fixed set of egress IPs per MotherDuck region, listed
under
[Connecting to external systems](https://motherduck.com/docs/concepts/flights/#connecting-to-external-systems).
Find your region with `SELECT region FROM md_user_info();`.

In the Azure portal, open the server's **Networking** page, keep **Public
access** on, and add a firewall rule for each egress IP of your region. Servers
that use **Private access (VNet integration)** or only a private endpoint can't
be reached from a Flight. A connect timeout usually means a missing firewall
rule.

## Microsoft Entra ID

The shared Flight authenticates with a password. To use a Microsoft Entra ID
service principal instead, fork the shared `flight.py`:

1. Add `azure-identity` to `requirements.txt`.
2. Store the service principal's tenant id, client id, and client secret in the
   Flights secret, and set `PGUSER` to the Entra role name.
3. Before the Postgres attach, fetch a token and set it as `PGPASSWORD`:

   ```python
   from azure.identity import ClientSecretCredential

   cred = ClientSecretCredential(tenant_id, client_id, client_secret)
   os.environ["PGPASSWORD"] = cred.get_token(
       "https://ossrdbms-aad.database.windows.net/.default"
   ).token
   ```

A token lasts about an hour. Fetch one per run. A run that takes longer than
that can fail when it opens a new connection.

## Caveats

- **Settle the questions first.** An agent following this recipe should work
  through [Questions to answer](#questions-to-answer) before it opens the secret
  form (the MCP `create_secret` tool) or creates the Flight.
- **`verify-full` needs the Azure root certificate.** An `sslmode` in the URL is
  passed to libpq, but this recipe doesn't install the certificate libpq needs to
  verify the server.
- **Full refresh re-reads every table on every run.** Cost scales with table
  size, not change volume. Use `INCREMENTAL_TABLES` for large tables with a
  reliable watermark column; incremental tables don't see deletes.
- **`password authentication failed`** means a wrong password, or, with Entra
  ID, an expired token.
- **Not carried over:** primary keys, indexes, and constraints. **Not removed:**
  a table dropped in Postgres stays in MotherDuck until you drop it.
- The shared recipe's [Caveats](../flight-postgres-ingest#caveats) apply too,
  including type mapping (`json` arrives as `VARCHAR`, bare `numeric` as
  `DOUBLE`) and the lack of a cross-table snapshot.

## What you'll adjust

No code edits are required for password authentication. These are the settings
this recipe sets; the full list is in
[What you'll adjust](../flight-postgres-ingest#what-youll-adjust) on the shared
recipe.

| Knob | This recipe | Purpose |
|---|---|---|
| `azure_pg` secret | (required) | `TYPE flights` secret with `DATABASE_URL`, or `PGHOST`, `PGDATABASE`, `PGUSER`, `PGPASSWORD` (optional `PGPORT`, `PGSSLMODE`) |
| `SECRET_NAME` | `azure_pg` | Makes the Flight read `azure_pg_DATABASE_URL` when it has several secrets |
| `TARGET_DATABASE` | `azure_pg_mirror` | MotherDuck database to write into, created if missing |
| `INCLUDED_SCHEMAS` | (all non-system) | Comma-separated schemas to copy, for example `public` |
| `EXCLUDED_TABLES` | (none) | Comma-separated `table` or `schema.table` to skip |
| `INCREMENTAL_TABLES` | (none) | `schema.table:watermark_column:key_column`, with `\|` between multiple keys, for example `public.orders:updated_at:id,public.line_items:updated_at:order_id\|line_no` |

## Run it

For a local run, set the same names the secret would inject. Your own IP must
also be allowed by the server's firewall. The Flight code lives in the shared
recipe's folder:

```bash
export MOTHERDUCK_TOKEN=your_token_here
export DATABASE_URL='postgresql://reader:password@myserver.postgres.database.azure.com:5432/appdb?sslmode=require'
export TARGET_DATABASE=azure_pg_mirror
# optional: narrow scope, make large tables incremental
# export INCLUDED_SCHEMAS=public
# export INCREMENTAL_TABLES=public.orders:updated_at:id
cd ../flight-postgres-ingest
uv run --with-requirements requirements.txt flight.py
```

### Deploy as a Flight

Store the connection as a Flights secret named `azure_pg` with the key
`DATABASE_URL`: in the UI under
[Settings > Secrets](https://app.motherduck.com/settings/secrets) with type
**Flights**, or with SQL from a write-enabled connection:

```sql
CREATE SECRET azure_pg IN motherduck (
  TYPE flights,
  PARAMS MAP {'DATABASE_URL': 'postgresql://<user>:<password>@<server>.postgres.database.azure.com:5432/<database>?sslmode=require'}
);
```

Then create the Flight with `MD_CREATE_FLIGHT`, passing:

- `name`: for example `azure_postgres_mirror`
- `source_code`: the shared
  [`flight.py`](https://raw.githubusercontent.com/motherduckdb/motherduck-cookbook/main/flight-plans/flight-postgres-ingest/flight.py)
- `requirements_txt`: the shared
  [`requirements.txt`](https://raw.githubusercontent.com/motherduckdb/motherduck-cookbook/main/flight-plans/flight-postgres-ingest/requirements.txt)
- `flight_secret_names`: `['azure_pg']`
- `config`: `SECRET_NAME` = `azure_pg`, `TARGET_DATABASE` = `azure_pg_mirror`,
  plus any filters or `INCREMENTAL_TABLES`. Store keys you may override per run,
  set to `""` for the default, because `MD_RUN_FLIGHT` only overrides stored keys.

A MotherDuck token is attached to the Flight automatically and injected at run
time as `MOTHERDUCK_TOKEN`. Run it once with `MD_RUN_FLIGHT(flight_id := ...)`,
check that `azure_pg_mirror.main.flight_tracker` has one row per table, then add
a schedule if one is wanted.

## Security

- **Narrow firewall rules.** Allow only the Flight egress IPs for your region,
  not `0.0.0.0` to `255.255.255.255` or "Allow public access from any Azure
  service".
- **Use a read-only role.** Grant it `SELECT` on the tables you copy.
- **The connection lives in a secret.** The password reaches libpq through `PG*`
  environment variables, never a SQL statement or a log line.
- **Read-only source.** Postgres is attached `READ_ONLY`, so the Flight can't
  write back.

## Learn more

- Shared implementation and full settings: [flight-postgres-ingest](../flight-postgres-ingest).
- Flight egress IPs: [Connecting to external systems](https://motherduck.com/docs/concepts/flights/#connecting-to-external-systems).
- Azure flexible server networking: [learn.microsoft.com](https://learn.microsoft.com/azure/postgresql/flexible-server/concepts-networking-public).
- Flight mechanics (create, run, schedule, secrets): MCP `get_flight_guide`.
- Deeper MotherDuck/DuckDB questions: MCP `ask_docs_question`.
