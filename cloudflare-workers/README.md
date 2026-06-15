---
title: Query MotherDuck from Cloudflare Workers
id: cloudflare-workers
description: >-
  A Cloudflare Worker that queries MotherDuck over the Postgres wire protocol
  with the node-postgres (pg) driver, no DuckDB binary required. Use when you
  want a serverless HTTP API or edge endpoint that reads from MotherDuck and
  returns JSON.
type: example
category: integrations
features: [pg_endpoint]
tags: [cloudflare, typescript, node-postgres]
---

# Query MotherDuck from Cloudflare Workers

A single-file Cloudflare Worker (`src/index.ts`) that connects to MotherDuck through the Postgres wire protocol endpoint using the `pg` driver and a connection string, then serves query results as JSON. This shows the pattern for reaching MotherDuck from an edge or serverless runtime where you cannot ship the DuckDB binary: connect with a Postgres client, authenticate with your MotherDuck token, and run parameterized SQL. The sample queries the `nyc.taxi` table in the public `sample_data` database that every MotherDuck account has.

## Routes

- `GET /` returns the 20 most recent trips from `nyc.taxi`, ordered by pickup time, as a JSON array.
- `GET /stats?start=YYYY-MM-DD&end=YYYY-MM-DD` returns total passengers and total fare for the date range. Both `start` and `end` are required; the route returns `400` if either is missing or malformed.

Example request and response for `/stats`:

```text
/stats?start=2022-11-01&end=2022-12-01
```

```json
{
  "start": "2022-11-01",
  "end": "2022-12-01",
  "total_passengers": 1234567,
  "total_fare": 1234567.89
}
```

## Connection details

The Worker builds a Postgres connection string and connects with `pg`:

```text
postgresql://anyusername:<MOTHERDUCK_TOKEN>@<MOTHERDUCK_HOST>:5432/<MOTHERDUCK_DB>?sslmode=require
```

- The username (`anyusername`) is ignored by MotherDuck; the access token is the credential.
- `sslmode=require` is mandatory. MotherDuck only accepts TLS connections on the Postgres endpoint.
- The endpoint listens on port `5432`. Use `pg.us-east-1-aws.motherduck.com` for US organizations and `pg.eu-central-1-aws.motherduck.com` for EU organizations.
- `sample_data` is available on every MotherDuck account, so the example works without loading any data first.

## How it works

- `src/index.ts`: builds the connection string, connects with `pg`, routes on `url.pathname`, runs parameterized SQL, and always closes the client in a `finally` block. A failed `connect()` returns a `502` with the error detail.
- `wrangler.toml`: sets `compatibility_flags = ["nodejs_compat"]` and the non-secret `[vars]` `MOTHERDUCK_HOST` and `MOTHERDUCK_DB`.

## Questions to answer

- Which MotherDuck database and schema should the Worker read from (default is `sample_data` / `nyc`)?
- Which region is the MotherDuck organization in, US or EU, so the right `MOTHERDUCK_HOST` is set?
- What routes and queries does the API need to expose, and what inputs do they accept?
- How should request inputs be validated and bound (this example uses a regex check plus numbered parameters)?
- Where will the MotherDuck token live (Wrangler secret for deploy, `.dev.vars` for local)?

## Caveats

- `nodejs_compat` is required. `wrangler.toml` must set `compatibility_flags = ["nodejs_compat"]` (it does in this example). Without it the `pg` driver fails to load in the Workers runtime, and the failure is not obvious from the error message.
- `sslmode=require` is not optional. Dropping it from the connection string makes the connection fail rather than fall back to plaintext.
- Both `/stats` parameters are required and have no server-side defaults. If `start` or `end` is missing the route returns `400`; it does not silently pick a date range. Validate this expectation if you adapt the route, and do not assume defaults like `2022-01-01`.
- Bind values as numbered parameters; never string-interpolate request input into SQL. The regex is a first gate, but parameter binding is what prevents injection.
- Match the bound value's shape to the column type. This example pads dates to `YYYY-MM-DD 00:00:00` so they compare against a `TIMESTAMP`. Passing a bare date or a mismatched type can cause silent zero-row results or a type error.
- Keep the token out of `wrangler.toml` and out of source. It is a secret (`wrangler secret put` for deploy, `.dev.vars` for local, which should be gitignored). Only non-secret config (`MOTHERDUCK_HOST`, `MOTHERDUCK_DB`) belongs in `[vars]`.
- Set `MOTHERDUCK_HOST` to your org's region. A US token against the EU host (or vice versa) fails to authenticate.
- A new Postgres connection is opened and closed per request (`client.connect()` / `client.end()` in a `finally` block). That is fine for low to moderate traffic; for high request volume, consider connection reuse or pooling patterns suited to the Workers runtime.

## What you'll adjust

| Setting | Purpose | Options / example |
| --- | --- | --- |
| `MOTHERDUCK_TOKEN` (secret) | MotherDuck access token, the password in the connection string | Set via `npx wrangler secret put MOTHERDUCK_TOKEN`; locally via `.dev.vars` |
| `MOTHERDUCK_HOST` (`[vars]` in `wrangler.toml`) | Postgres endpoint host | `pg.us-east-1-aws.motherduck.com` (US), `pg.eu-central-1-aws.motherduck.com` (EU) |
| `MOTHERDUCK_DB` (`[vars]` in `wrangler.toml`) | Database the connection targets | `sample_data`; change to your own database name |
| `name` (`wrangler.toml`) | Worker / deployment name | `motherduck-taxi-stats` |
| SQL in `src/index.ts` | The queries served by each route | Replace `nyc.taxi` queries with your own schema, table, and columns |
| Routes in `src/index.ts` | URL paths the Worker handles | `/` (recent trips), `/stats?start=&end=` (aggregates); add or rename to fit your API |
| Query parameters | Inputs accepted on a route | `start`, `end` (validated as `YYYY-MM-DD`, passed as numbered `$1`/`$2` params) |

## Run it

Prerequisites: Node.js 18+, a Cloudflare account, and a MotherDuck account with an access token.

```sh
npm install

# Set the token as a Worker secret (creates the Worker if it does not exist).
# Wrangler prompts you to sign in to Cloudflare if needed, then asks for the token value.
npx wrangler secret put MOTHERDUCK_TOKEN

# Local development: put the token in a .dev.vars file first, then start the dev server.
#   MOTHERDUCK_TOKEN="ey...MY_TOKEN"
npx wrangler dev

# Deploy to Cloudflare once the Worker is adapted to your needs.
npx wrangler deploy
```

## Security

Always sanitize inputs whenever your application accepts them. The two routes that take a date range do both of the following.

### 1. Validate inputs

A regex enforces `YYYY-MM-DD` before the values reach SQL. Anything else returns a `400`:

```js
const datePattern = /^\d{4}-\d{2}-\d{2}$/;
if (!datePattern.test(startDate) || !datePattern.test(endDate)) {
  return Response.json(
    { error: "Invalid date format. Use YYYY-MM-DD." },
    { status: 400 }
  );
}
```

### 2. Use parameterized queries

Bind validated values as numbered parameters (`$1`, `$2`) instead of interpolating them into the SQL string. This is the defense against SQL injection:

```js
const result = await client.query(
  `SELECT
    sum(passenger_count)::INTEGER AS total_passengers,
    round(sum(fare_amount), 2) AS total_fare
  FROM nyc.taxi
  WHERE tpep_pickup_datetime >= $1
    AND tpep_pickup_datetime < $2`,
  [`${startDate} 00:00:00`, `${endDate} 00:00:00`]
);
```

Here the validated date strings are widened to timestamp literals (`YYYY-MM-DD 00:00:00`) so they compare correctly against the `TIMESTAMP` column `tpep_pickup_datetime`. The range is half-open (`>= start`, `< end`), so the `end` day is excluded.

## Files

- [`src/index.ts`](src/index.ts): the Worker itself. Builds the Postgres connection string, connects with `pg`, routes on `url.pathname`, validates date inputs, runs parameterized SQL against `nyc.taxi`, and always closes the client in a `finally` block.
- [`wrangler.toml`](wrangler.toml): Wrangler config. Sets the Worker `name`, `main` entrypoint, `compatibility_flags = ["nodejs_compat"]`, and the non-secret `[vars]` `MOTHERDUCK_HOST` and `MOTHERDUCK_DB`.
- [`package.json`](package.json): dependencies (`pg`) and dev dependencies (`wrangler`, `@cloudflare/workers-types`), plus `dev` and `deploy` npm scripts.
- [`tsconfig.json`](tsconfig.json): TypeScript compiler settings for the Worker, including the Cloudflare Workers type definitions.
- [`package-lock.json`](package-lock.json): the npm lockfile pinning exact dependency versions.

## Learn more

- For the connection-string format, supported regions, and endpoint behavior, see the MotherDuck Cloudflare Workers interface docs at https://motherduck.com/docs/getting-started/interfaces/serverless/cloudflare-workers/ or ask the `ask_docs_question` MCP tool for deeper MotherDuck or DuckDB questions.
