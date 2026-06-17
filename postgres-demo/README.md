---
title: Bridge Local Postgres and MotherDuck with pg_duckdb
id: postgres-demo
description: >-
  Run a local Postgres (the pg_duckdb image) wired to MotherDuck, then move data
  both ways: scan Postgres tables from the DuckDB CLI and query MotherDuck
  tables from psql. Use when you want a hybrid Postgres plus MotherDuck setup
  where one side reads the other, not a serverless app talking to MotherDuck.
type: example
category: integrations
features: [pg_duckdb]
tags: [postgres, docker]
prompt: >-
  I want a hybrid setup where local Postgres and MotherDuck can read each other through
  pg_duckdb, scanning Postgres from DuckDB and querying MotherDuck from psql. Help me
  adapt the "Bridge Local Postgres and MotherDuck with pg_duckdb" recipe to my own data
  and use case, using it as a guide:
  https://motherduck.com/docs/cookbook/postgres-demo
published_date: 2025-01-30
---

# Bridge Local Postgres and MotherDuck with pg_duckdb

This example runs a local Postgres using the `pgduckdb/pgduckdb` Docker image, which embeds DuckDB inside Postgres, and connects it to MotherDuck with a token. It shows two directions of the same hybrid pattern: from the DuckDB CLI you attach the local Postgres with the `postgres` extension (the pg scanner) and read its tables, and from inside the container you use `psql` against pg_duckdb to query MotherDuck tables. The MotherDuck pattern here is interoperability: MotherDuck and Postgres reading each other's tables in one query, with `CREATE TABLE AS SELECT` (CTAS) used to replicate data across the boundary.

Note this is the `pg_duckdb` / pg scanner path: DuckDB embedded inside Postgres, reaching MotherDuck via the `MOTHERDUCK_TOKEN`. It is not the MotherDuck Postgres wire endpoint (where a plain psql client connects directly to MotherDuck). If you only want a Postgres driver pointed at MotherDuck, you want the wire endpoint instead, not this image.

## How it works

- The `pgduckdb/pgduckdb` container embeds DuckDB inside Postgres and reaches MotherDuck through `MOTHERDUCK_TOKEN`.
- The DuckDB CLI uses the `postgres` extension to attach the local Postgres container and scan its tables.
- `CREATE TABLE AS SELECT` copies data across the boundary when you want a local replica rather than a live hybrid query.

## Questions to answer

- Which direction is needed: read Postgres from MotherDuck/DuckDB, read MotherDuck from Postgres, or both?
- Which source table(s) in Postgres or MotherDuck should be moved, and to which target database and schema?
- Is this a one-time copy (CTAS) or do you expect to query the two systems live in a hybrid query?
- Which MotherDuck region and token should the container use, and where is that token stored?
- Is the local Postgres password and host the default from this example, or have they been changed? The ATTACH string must match whatever you set.
- Does the source table already exist in the system you are reading from? If not, create and load it first.

## Caveats

- **The source table must already exist.** `winelist` is assumed to be present; this example does not create or seed it. `SELECT * FROM pg.public.winelist` (or the psql equivalent) errors with a missing-table error until you create and load it yourself. Load a table on the side you intend to read first.
- **Keep the ATTACH string in sync with the container env.** If you change `POSTGRES_PASSWORD`, remap the port (e.g. `-p 5433:5432`), or run on a different host, the `password=`, `host=`, and implied port in the ATTACH connection string must all match, or the attach fails silently from the user's point of view (it just cannot connect).
- **Use `host=127.0.0.1`, not the container name.** The DuckDB CLI runs on your host, not inside the container, so it reaches Postgres through the published port on `127.0.0.1`. On some systems `localhost` resolves to IPv6 first and the connection hangs or refuses; prefer `127.0.0.1`.
- **`pg_duckdb` is not the Postgres wire endpoint.** This image embeds DuckDB inside Postgres and reaches MotherDuck via a token. It is a different mechanism from MotherDuck's Postgres wire endpoint. Picking the wrong one is the most common confusion here. Use this image only when you actually want Postgres and MotherDuck reading each other's tables locally.
- **Forgetting `duckdb.motherduck_enabled=true` fails quietly.** Without that flag the container starts fine and ordinary Postgres works, but MotherDuck tables will not be reachable from psql.
- **Do not commit your real token or password.** `very_secur3_pw` and `your_token` are placeholders. Do not bake a real `MOTHERDUCK_TOKEN` into a committed script or image; pass it as runtime env. The example password is intentionally weak and is fine for a local throwaway container only.
- **5432 is often already in use** (a local Postgres install, another container). If `docker run` reports the port is allocated, remap the host side and update the ATTACH string accordingly.

## What you'll adjust

| Setting | Purpose | Options / example |
| --- | --- | --- |
| `MOTHERDUCK_TOKEN` (container env) | Lets pg_duckdb authenticate to MotherDuck | Your MotherDuck access token, passed with `-e MOTHERDUCK_TOKEN="..."` |
| `POSTGRES_PASSWORD` (container env) | Password for the local Postgres `postgres` user | `very_secur3_pw`; change it and reuse the same value in the ATTACH string |
| `-p 5432:5432` (docker run) | Host port the local Postgres listens on | Remap the left side if 5432 is taken, e.g. `-p 5433:5432`, then use that port in ATTACH |
| `duckdb.motherduck_enabled=true` (container flag) | Turns on the MotherDuck integration in pg_duckdb | Keep `true` to reach MotherDuck from psql |
| ATTACH connection string (DuckDB CLI) | Where the DuckDB CLI finds the local Postgres | `dbname=postgres user=postgres password=... host=127.0.0.1` |
| Source table (`pg.public.winelist`) | The Postgres table you scan from DuckDB | Replace `winelist` with your own table and schema; this table must already exist (see Caveats) |
| Target database / schema | Where CTAS writes the replicated data | A MotherDuck database for PG to MD, or `public` in Postgres for MD to PG |

## Run it

Prerequisites: Docker, the [DuckDB CLI](https://duckdb.org/docs/installation/?version=stable&environment=cli&download_method=package_manager), and a MotherDuck account with an access token.

### 1. Start the pg_duckdb container, wired to MotherDuck

```bash
docker run -d -p 5432:5432 \
  -e POSTGRES_PASSWORD="very_secur3_pw" \
  -e MOTHERDUCK_TOKEN="your_token" \
  pgduckdb/pgduckdb:17-main -c duckdb.motherduck_enabled=true
```

Passing your token in `MOTHERDUCK_TOKEN` is what lets the embedded DuckDB inside this container reach MotherDuck. The `-c duckdb.motherduck_enabled=true` flag turns the MotherDuck integration on; without it, psql queries against MotherDuck tables will not resolve.

### 2. Read the local Postgres from the DuckDB CLI (the pg scanner)

Start the CLI with `duckdb` (or `./duckdb` if you downloaded the binary into the current directory rather than installing it on your PATH). Install and load the `postgres` extension (the pg scanner), then attach the running container:

```sql
INSTALL postgres;
LOAD postgres;

ATTACH 'dbname=postgres user=postgres password=very_secur3_pw host=127.0.0.1' AS pg (TYPE POSTGRES);

-- Confirm the attach worked and the table is visible
SHOW ALL TABLES;

-- Read a Postgres table from DuckDB
SELECT * FROM pg.public.winelist;
```

Replicate Postgres data into DuckDB or MotherDuck with CTAS:

```sql
-- Writes into the currently attached MotherDuck / DuckDB database
CREATE TABLE my_db.winelist AS SELECT * FROM pg.public.winelist;
```

### 3. Read MotherDuck from inside the container with pg_duckdb

```bash
docker ps                              # find the container name
docker exec -it <container_name> /bin/bash
psql
```

```sql
-- Query a MotherDuck table through pg_duckdb
SELECT * FROM public.winelist;
```

Replicate MotherDuck data into Postgres, then write a hybrid query that joins both systems in a single statement:

```sql
-- Pull MotherDuck data down into local Postgres
CREATE TABLE public.winelist_local AS SELECT * FROM <motherduck_table>;

-- Hybrid query: a single statement touching both Postgres and MotherDuck data
SELECT *
FROM public.winelist_local AS local
JOIN <motherduck_table> AS md USING (id);
```

## Learn more

- Loading data from Postgres into MotherDuck: [MotherDuck docs](https://motherduck.com/docs/key-tasks/loading-data-into-motherduck/loading-data-from-postgres/).
- pg_duckdb (DuckDB embedded in Postgres) and the MotherDuck integration flag: the [pg_duckdb project](https://github.com/duckdb/pg_duckdb).
- The DuckDB `postgres` extension used by the pg scanner: [DuckDB Postgres extension docs](https://duckdb.org/docs/extensions/postgres).
- For deeper MotherDuck or DuckDB questions, run the `ask_docs_question` MCP tool.
