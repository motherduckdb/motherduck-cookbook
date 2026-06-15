---
title: Connect to MotherDuck from Node.js with the DuckDB Neo Driver
id: nodejs-motherduck
description: >-
  Two Node.js scripts that connect to MotherDuck with the native DuckDB Neo
  driver (@duckdb/node-api): a basic query walkthrough and a generic-pool
  connection pool. Use when you want to run DuckDB SQL against MotherDuck from a
  Node.js app or service that can ship the DuckDB binary, including concurrent
  workloads that need pooled connections.
type: example
features: []
tags: [nodejs, javascript, generic-pool]
---

# Connect to MotherDuck from Node.js with the DuckDB Neo Driver

Two example scripts that connect to MotherDuck using the [DuckDB Neo driver](https://duckdb.org/docs/clients/node_neo/overview) (`@duckdb/node-api`). `src/basic.js` walks through simple queries, table creation, parameterized statements, aggregations, and querying the public `sample_data.nyc.taxi` share. `src/connection-pool.js` builds a `generic-pool` over MotherDuck connections for concurrent queries. The key MotherDuck pattern here: unlike the Python, R, JDBC, and ODBC clients, the Node.js client does not cache database instances automatically, so both scripts use `DuckDBInstance.fromCache('md:<db>', { motherduck_token })` to avoid reinitializing the MotherDuck extension and re-fetching catalog metadata on every connection.

This is the native-driver path: the process loads the DuckDB binary in-process and talks to MotherDuck over the `md:` protocol. If your runtime cannot ship a native binary (edge workers, serverless with strict bundle limits), use the Postgres wire endpoint instead: see the `cloudflare-workers` example for that pattern.

## How it works

### Basic connection (`src/basic.js`)

Connect once, then reuse the cached instance. Because the Node.js client does not auto-cache instances, `fromCache()` is what keeps you from reinitializing the MotherDuck extension and re-fetching catalog metadata on every connection.

```javascript
import { DuckDBInstance } from "@duckdb/node-api";

const token = process.env.MOTHERDUCK_TOKEN;
const instance = await DuckDBInstance.fromCache("md:my_db", {
  motherduck_token: token,
});
const connection = await instance.connect();

const reader = await connection.runAndReadAll("SELECT 42 AS answer");
console.table(reader.getRowObjects()); // [{ answer: 42 }]
```

Subsequent `fromCache()` calls with the same path reuse the existing instance, so a second connection costs nothing extra:

```javascript
// Reuses the same cached instance, no reinitialization
const instance2 = await DuckDBInstance.fromCache("md:my_db", {
  motherduck_token: token,
});
const conn2 = await instance2.connect();
```

### Parameterized queries

Use `prepare()` plus a typed `bind*` call rather than string interpolation. This avoids SQL injection and lets DuckDB plan the statement once.

```javascript
const prepared = await connection.prepare(
  "SELECT * FROM example_users WHERE id = $1"
);
prepared.bindInteger(1, 2); // pick bind* to match the column type
const reader = await prepared.runAndReadAll();
console.table(reader.getRowObjects());
```

### Connection pooling (`src/connection-pool.js`)

`MDConnectionFactory.create()` builds each pooled connection from the shared cached instance and pins `SET THREADS='1'` so pooled connections don't fight over CPU. Resources are wrapped as `{ connection, createdAt }` so `validate()` can recycle stale connections without reaching into pool internals: it returns `false` once a connection is older than `recycleTimeoutMillis`, and the pool destroys and replaces it. `createPool` from `generic-pool` then runs four queries concurrently with `Promise.all`, acquiring and releasing connections per query.

```javascript
async create() {
  const instance = await DuckDBInstance.fromCache(`md:${this.opts.database}`, {
    motherduck_token: this.opts.token,
  });
  const connection = await instance.connect();
  await connection.run("SET THREADS='1';");
  return { connection, createdAt: Date.now() };
}

async validate(resource) {
  // false => pool destroys and replaces this connection
  return Date.now() - resource.createdAt < this.recycleTimeoutMillis;
}
```

## Questions to answer

- Which MotherDuck database and schema should the scripts target? Default is `my_db`; sample queries read the `sample_data.nyc` share.
- Is a MotherDuck access token available, and where should it live? These scripts read it from `MOTHERDUCK_TOKEN` in `.env`.
- Is this a one-shot script or a long-running service: does it need the connection pool, and at what concurrency (min/max, recycle timers)?
- What queries or tables should replace the `example_users` and `nyc.taxi` samples?
- Does the runtime ship the DuckDB binary (native driver), or does it need the Postgres endpoint instead (see `cloudflare-workers` for that pattern)?

## Caveats

- The native driver loads the DuckDB binary in-process. Runtimes that cannot ship a native addon (Cloudflare Workers, some serverless platforms, browsers) will fail to load `@duckdb/node-api`. Use the Postgres endpoint there instead (`cloudflare-workers` example).
- Do not call `DuckDBInstance.create()` per request or per pooled connection: that re-loads the MotherDuck extension and re-fetches catalog metadata every time. Always go through `fromCache()` so connections share one instance.
- Keep the token out of source control. The token is read from `MOTHERDUCK_TOKEN` in `.env`, and `.gitignore` already excludes `.env`. Do not hardcode it into `basic.js`, `connection-pool.js`, or commit a populated `.env`. If `MOTHERDUCK_TOKEN` is unset, both scripts exit early with a clear error.
- ESM only: `package.json` sets `"type": "module"`, so use `import`, not `require`. Node.js below 22 may not support the syntax used here.
- `validate()` only recycles a connection on borrow (`testOnBorrow: true`). A connection that sits idle past `idleTimeoutMillis` is evicted by the eviction sweep, but staleness is checked when the connection is handed out, not continuously. Tune `recycleTimeoutMillis`, `evictionRunIntervalMillis`, and the idle timeouts together.
- `SET THREADS='1'` trades per-query speed for fair CPU sharing across the pool. If you raise `max` without watching threads, pooled connections can oversubscribe CPU; if a single query is slow and concurrency is low, raise the thread count.
- The pool example reads only from `sample_data.nyc.taxi`; the writes in `basic.js` create and drop `example_users` in your target database. Point these at a database you are allowed to write to, and note that `basic.js` drops `example_users` on exit (in its `finally` block).
- `getRowObjects()` returns DuckDB-typed values; large integers come back as `BigInt`, not `Number`. Handle that when serializing results (for example `JSON.stringify` on a `BigInt` throws).
- Always close connections (`connection.closeSync()`) and drain the pool (`pool.drain()` then `pool.clear()`) on shutdown, or the process can hang on open handles.

## What you'll adjust

| Setting | Purpose | Options / example |
| --- | --- | --- |
| `MOTHERDUCK_TOKEN` (env, in `.env`) | MotherDuck access token, the credential passed as `motherduck_token` | Copy `.env.template` to `.env` and set the token |
| `MOTHERDUCK_DATABASE` (env, in `.env`) | Database the connection string `md:<db>` targets | Defaults to `my_db` in both scripts; set to your own database name |
| SQL in `src/basic.js` | The example queries that run | Replace the `example_users` DDL/DML and the `sample_data.nyc.taxi` reads with your own schema and tables |
| `bindInteger(1, 2)` in `src/basic.js` | Value bound to the parameterized query | Use the matching `bind*` method for your parameter type (`bindVarchar`, `bindDouble`, etc.) |
| Pool sizing in `src/connection-pool.js` | Min/max pooled connections | `min: 2`, `max: 5` |
| Eviction / recycle timers in `src/connection-pool.js` | Idle cleanup and connection recycling | `softIdleTimeoutMillis: 60000`, `idleTimeoutMillis: 120000`, `recycleTimeoutMillis: 300000` |
| `SET THREADS='1'` in the pool factory | Threads per pooled connection so they don't compete for CPU | Raise or drop depending on concurrency vs per-query speed |
| `queries` array in `src/connection-pool.js` | The concurrent queries run through the pool | Replace the `sample_data.nyc.taxi` aggregates with your own SQL |

## Run it

Prerequisites: Node.js 22+ (uses native ESM modules; `.nvmrc` pins 22), npm, and a MotherDuck account with an access token.

```bash
# Install dependencies
npm install

# Copy the env template and add your token
cp .env.template .env
# Edit .env: set MOTHERDUCK_TOKEN, optionally MOTHERDUCK_DATABASE

# Run the basic query walkthrough
npm run basic

# Run the connection-pool example with concurrent queries
npm run pool
```

## Files

- [`src/basic.js`](src/basic.js) - the basic walkthrough: connects via `fromCache()`, runs a simple query, creates and queries `example_users`, a parameterized query, an aggregate, a `sample_data.nyc.taxi` read, and a second cached connection, then drops the table on exit.
- [`src/connection-pool.js`](src/connection-pool.js) - the pooling example: an `MDConnectionFactory` over `generic-pool` that pins `SET THREADS='1'`, recycles stale connections via `validate()`, and runs four `nyc.taxi` queries concurrently with `Promise.all`.
- [`package.json`](package.json) - npm manifest: declares the `@duckdb/node-api`, `dotenv`, and `generic-pool` dependencies, sets `"type": "module"` for ESM, and defines the `basic` and `pool` run scripts.
- [`package-lock.json`](package-lock.json) - pinned dependency lockfile for reproducible installs.
- [`.env.template`](.env.template) - environment template: copy to `.env` and set `MOTHERDUCK_TOKEN` (and optionally `MOTHERDUCK_DATABASE`).
- [`.nvmrc`](.nvmrc) - pins Node.js 22 for tools like `nvm`.
- [`.gitignore`](.gitignore) - excludes `node_modules/`, `.env`, local DuckDB files, and other noise from version control.

## Learn more

- `sample_data` is a public MotherDuck data share that every account can read; `sample_data.nyc.taxi` is the NYC taxi table used in these examples.
- For the Node.js connection guide and connection pooling docs, see the [MotherDuck connection guide](https://motherduck.com/docs/key-tasks/authenticating-and-connecting-to-motherduck/connecting-to-motherduck/) and the [multithreading and parallelism guide](https://motherduck.com/docs/key-tasks/authenticating-and-connecting-to-motherduck/multithreading-and-parallelism/).
- For creating an access token, see [Authenticating to MotherDuck](https://motherduck.com/docs/key-tasks/authenticating-and-connecting-to-motherduck/authenticating-to-motherduck/#creating-an-access-token).
- For deeper MotherDuck or DuckDB SQL questions, use the `ask_docs_question` MCP tool.
