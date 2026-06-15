---
title: Query MotherDuck from Vercel and Next.js
id: vercel-nextjs
description: >-
  Next.js API routes that query MotherDuck over the Postgres wire protocol with
  the node-postgres driver, no DuckDB binary needed. Use when building a Vercel
  (or any Node serverless) backend that reads MotherDuck data through pooled,
  parameterized SQL.
type: example
features: [pg_endpoint]
tags: [vercel, nextjs, node-postgres]
---

# Query MotherDuck from Vercel and Next.js

This is a Next.js app whose API routes connect to MotherDuck through the Postgres
wire protocol endpoint using the `pg` (node-postgres) driver. It shows the
serverless-friendly MotherDuck pattern: a module-level connection pool reused
across warm function invocations, SSL certificate verification, input validation,
and parameterized queries against the public `sample_data.nyc.taxi` dataset. No
DuckDB binary is bundled, so the deploy stays small and cold starts stay fast.

## Routes

### `GET /api/trips`

Returns the 20 most recent taxi trips from `sample_data.nyc.taxi`, ordered by
`tpep_pickup_datetime` descending. No query parameters.

### `GET /api/stats?start=YYYY-MM-DD&end=YYYY-MM-DD`

Returns total passengers and total fare for the given pickup-date range.

Parameters:

- `start`: start date, required, `YYYY-MM-DD` (inclusive lower bound)
- `end`: end date, required, `YYYY-MM-DD` (exclusive upper bound)

Example request and response:

```text
/api/stats?start=2022-11-01&end=2022-12-01
```

```json
{
  "start": "2022-11-01",
  "end": "2022-12-01",
  "total_passengers": 1234567,
  "total_fare": 1234567.89
}
```

Both parameters are required and validated. A missing parameter or a value that
does not match `YYYY-MM-DD` returns HTTP 400 before any query runs.

## Connection details

The app connects to MotherDuck through the
[Postgres wire protocol endpoint](https://motherduck.com/docs/key-tasks/authenticating-and-connecting-to-motherduck/postgres-endpoint),
which speaks the Postgres protocol so any Postgres driver works without a DuckDB
binary. The default host is `pg.us-east-1-aws.motherduck.com` and the endpoint
listens on port `5432`. Set `MOTHERDUCK_HOST=pg.eu-central-1-aws.motherduck.com`
for an EU organization, or `pg.<region>-aws.motherduck.com` for another region.
The connection user is always the literal `user`; the access token is the
password. The `sample_data` database (with the `nyc.taxi` table) ships on every
MotherDuck account, so the example runs against real data with zero setup.

### Connection pooling

`src/lib/motherduck.ts` creates a single module-level `pg.Pool`
(`max: 10`, `idleTimeoutMillis: 5000`) so TCP connections are reused across
requests within one warm function instance instead of dialing MotherDuck on
every call.
[`attachDatabasePool`](https://vercel.com/kb/guide/connection-pooling-with-functions)
from `@vercel/functions` registers the pool so idle connections are drained
before the instance is suspended. The exported `withClient` helper checks out a
client and always releases it in a `finally` block.

### SSL

The connection uses `ssl: { rejectUnauthorized: true }`, equivalent to
PostgreSQL's `sslmode=verify-full`: Node verifies the server certificate against
the system CA bundle and checks hostname matching. MotherDuck's endpoint uses a
publicly trusted certificate, so no custom CA is needed. Do not set
`rejectUnauthorized: false`; it disables verification and exposes the connection
to man-in-the-middle attacks.

## How it works

- `src/lib/motherduck.ts`: the shared `pg.Pool`, the `attachDatabasePool`
  cleanup hook, and the `withClient` checkout/release helper.
- `src/app/api/trips/route.ts` and `src/app/api/stats/route.ts`: the two API
  handlers, including the `YYYY-MM-DD` validation and the parameterized `$1`/`$2`
  aggregate.

## Questions to answer

- Which MotherDuck database and schema should the routes read from (default is `sample_data.nyc.taxi`)?
- Which region is the account in, so the right `MOTHERDUCK_HOST` is set (US vs EU)?
- What tables and columns do the API routes need to expose, and what query parameters drive them?
- How will the token be provisioned in production: manual `vercel env add` or the MotherDuck Native Integration on Vercel?
- What concurrency is expected, so the pool `max` and idle timeout can be tuned?

## Caveats

- The token is a credential. Keep it in `.env.local` (gitignored) for local dev
  and in Vercel environment variables for deploy. Do not commit it or expose it
  in client-side code; these queries run only in server-side API routes.
- `src/lib/motherduck.ts` throws at import time if `MOTHERDUCK_TOKEN` is unset.
  Locally that surfaces immediately; on Vercel a missing variable fails the
  function at runtime, so set the env var before relying on the routes.
- The pool is module-level so it can be reused across warm invocations, but
  serverless instances are not shared. Under burst traffic many instances each
  open up to `max: 10` connections; size `max` against your MotherDuck plan's
  connection limits rather than assuming a single global pool.
- Identifiers (table and column names) cannot be parameterized with `$1`. Only
  values can. If a route needs a dynamic table or column name, validate it
  against an allow-list instead of interpolating user input.
- The stats route binds dates as timestamp strings (`YYYY-MM-DD 00:00:00`), and
  the regex enforces that shape. If you loosen the input format, keep the bound
  value a type MotherDuck can compare against `tpep_pickup_datetime`, or the
  query errors or returns nothing.
- The `nyc.taxi` data is historical, so `ORDER BY tpep_pickup_datetime DESC` in
  `/api/trips` returns the latest rows in the dataset, not today's trips. Pick a
  date range that actually exists in the data when testing `/api/stats`.

## What you'll adjust

| Setting | Purpose | Options / example |
| --- | --- | --- |
| `MOTHERDUCK_TOKEN` env var | MotherDuck access token used as the connection password | Service-account token from your MotherDuck settings |
| `MOTHERDUCK_HOST` env var | Postgres endpoint host, selects the region | `pg.us-east-1-aws.motherduck.com` (default), `pg.eu-central-1-aws.motherduck.com` for EU |
| `MOTHERDUCK_DB` env var | Database in the connection string | `sample_data` (default), or your own database |
| `connectionString` in `src/lib/motherduck.ts` | How the pool authenticates, fixed at port `5432` | `postgresql://user:${token}@${host}:5432/${db}` |
| Pool options in `src/lib/motherduck.ts` | Pooling behavior for serverless concurrency | `max: 10`, `idleTimeoutMillis: 5000` |
| `ssl` option in `src/lib/motherduck.ts` | TLS verification level | `{ rejectUnauthorized: true }` (equivalent to `sslmode=verify-full`) |
| SQL in `src/app/api/trips/route.ts` | The "recent trips" query and row limit | Change `FROM nyc.taxi`, columns, `LIMIT 20` |
| SQL in `src/app/api/stats/route.ts` | The aggregate query and its `$1`/`$2` date params | Swap the table, columns, and the `datePattern` validation regex |

## Run it

Prerequisites: Node.js v18+, a MotherDuck account and access token, and (for deploy) a Vercel account.

```sh
npm install
cp .env.local.example .env.local   # then set MOTHERDUCK_TOKEN
npm run dev                         # http://localhost:3000
```

Build and deploy to Vercel:

```sh
npm run build
npx vercel deploy
npx vercel env add MOTHERDUCK_TOKEN   # if not using the MotherDuck Native Integration
```

If you install the [MotherDuck Native Integration](https://vercel.com/marketplace/motherduck) on Vercel, the access token is injected as an environment variable automatically.

## Security

Always sanitize anything that comes from a request before it reaches SQL. This
example does two things.

### 1. Validate inputs

`src/app/api/stats/route.ts` rejects anything that is not a `YYYY-MM-DD` date
before querying:

```js
const datePattern = /^\d{4}-\d{2}-\d{2}$/;
if (!datePattern.test(startDate) || !datePattern.test(endDate)) {
  return NextResponse.json(
    { error: "Invalid date format. Use YYYY-MM-DD." },
    { status: 400 }
  );
}
```

### 2. Use parameterized queries

Never interpolate request values into the SQL string. Pass them as numbered
parameters (`$1`, `$2`) so the driver binds them safely:

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

## Files

- [`src/lib/motherduck.ts`](src/lib/motherduck.ts) - the shared `pg.Pool` (reads `MOTHERDUCK_TOKEN`/`MOTHERDUCK_HOST`/`MOTHERDUCK_DB`), the `attachDatabasePool` cleanup hook, and the `withClient` checkout/release helper.
- [`src/app/api/trips/route.ts`](src/app/api/trips/route.ts) - the `GET /api/trips` handler: queries the 20 most recent `nyc.taxi` trips.
- [`src/app/api/stats/route.ts`](src/app/api/stats/route.ts) - the `GET /api/stats` handler: validates `YYYY-MM-DD` dates and runs the parameterized `$1`/`$2` aggregate.
- [`.env.local.example`](.env.local.example) - template for the three env vars; copy to `.env.local` and set `MOTHERDUCK_TOKEN`.
- [`package.json`](package.json) - dependencies (`next`, `pg`, `@vercel/functions`, React) and the `dev`/`build`/`start` scripts.
- [`next.config.ts`](next.config.ts) - Next.js config (currently empty defaults).
- [`tsconfig.json`](tsconfig.json) - TypeScript config, including the `@/*` to `src/*` path alias.

## Learn more

- Postgres endpoint reference: [authenticating and connecting via the Postgres endpoint](https://motherduck.com/docs/key-tasks/authenticating-and-connecting-to-motherduck/postgres-endpoint).
- For deeper MotherDuck or DuckDB SQL questions, use the `ask_docs_question` MCP tool or the MotherDuck docs.
