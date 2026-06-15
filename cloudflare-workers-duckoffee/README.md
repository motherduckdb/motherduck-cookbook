---
title: Cloudflare Workers Map and Live Vote on MotherDuck
id: cloudflare-workers-duckoffee
description: >-
  A single Cloudflare Worker that reads analytics from a MotherDuck share over
  the Postgres endpoint and tallies live votes in a SQLite-backed Durable Object,
  with a D3 world map, sales chart, and leaderboard frontend. Use when you want a
  full-stack edge app that queries MotherDuck without bundling DuckDB and keeps a
  small piece of real-time shared state at the edge.
type: example
category: end-to-end
features: [pg_endpoint, shares]
tags: [cloudflare, durable-objects, node-postgres, d3, typescript]
---

# Cloudflare Workers Map and Live Vote on MotherDuck

A full-stack Cloudflare example that renders a world map of imaginary Duckoffee cafes, a live sales chart, and a live vote for where the next location should open. One Worker reads analytics from a MotherDuck data share through the Postgres wire protocol (the `pg` driver, no DuckDB binary in the bundle), serves a static D3 single-page app via the Workers Assets binding, and tallies votes in a Durable Object backed by SQLite. The MotherDuck pattern it shows: attach a read-only share from a serverless edge runtime over the Postgres endpoint, parameterize every statement, and keep all mutable state out of the warehouse.

![Duckoffee](./public/assets/duckoffee.jpg)

## What it demonstrates

- Reading data from a MotherDuck share through the Postgres endpoint, with no DuckDB binary in the bundle (the `pg` Node driver runs on the Workers runtime via `nodejs_compat`).
- Serving a static D3 frontend (HTML/CSS/JS) from the same Worker via the `[assets]` binding.
- Using a Durable Object to keep a small piece of shared state: a live tally of votes across 10 candidate cities, one vote per session, changeable at any time.
- Interactive filtering: click a cafe on the map to scope the sales chart, summary tiles, and top-sellers list.
- Interactive voting: click a candidate city (or a leaderboard row) to cast or change your vote, and watch the tally update across every open tab.

## Routes

All `/api/*` paths are handled by the Worker; everything else falls through to `env.ASSETS.fetch(req)` and is served by the static SPA (`not_found_handling = "single-page-application"`).

| Route | Description |
| --- | --- |
| `GET /` | Static single-page app (map, chart, leaderboard) |
| `GET /api/locations` | All Duckoffee cafes with lifetime revenue and order counts |
| `GET /api/sales?location_id=&days=` | Daily revenue series (default 90 days, clamped to `[7, 365]`). Optional `location_id` filter |
| `GET /api/summary?location_id=` | Totals and top 5 products, optionally scoped to a single location |
| `GET /api/votes?session_id=` | Candidate cities with vote counts, the total, and the caller's current choice |
| `POST /api/votes` | Cast or change a vote. Body: `{"session_id": "...", "candidate_id": "..."}` |

## Connection details

`withClient` in `src/index.ts` builds a standard Postgres connection string against the MotherDuck Postgres endpoint, attaches the share, and always closes the client in a `finally` block:

```ts
const connectionString =
  `postgresql://anyusername:${env.MOTHERDUCK_TOKEN}@${env.MOTHERDUCK_HOST}:5432/${env.MOTHERDUCK_DB}?sslmode=require`;
const client = new Client({ connectionString });
await client.connect();
try {
  await client.query(`ATTACH IF NOT EXISTS '${env.DUCKOFFEE_SHARE}' AS duckoffee`);
  return await fn(client);
} finally {
  await client.end();
}
```

Notes that make this work on the edge:

- The username is ignored by MotherDuck (`anyusername` is fine); the token is the password.
- `sslmode=require` is mandatory for the Postgres endpoint.
- `compatibility_flags = ["nodejs_compat"]` in `wrangler.toml` is what lets the `pg` driver run on the Workers runtime. Without it the import fails.
- This is the MotherDuck Postgres wire endpoint, not the `pg_duckdb` Postgres extension; you connect to MotherDuck with an ordinary Postgres driver.

## How it works

The three read-only endpoints query the attached `duckoffee` share. They lean on DuckDB SQL niceties such as `GROUP BY ALL` and `date_trunc`:

```sql
-- handleSales: daily revenue series, windowed off the latest order date
SELECT
  date_trunc('day', ordered_at)::DATE AS day,
  round(sum(order_total), 2) AS revenue,
  count(*)::INTEGER AS orders
FROM duckoffee.orders
WHERE ordered_at >= (
  SELECT max(ordered_at) - ($1::INTEGER * INTERVAL '1 day') FROM duckoffee.orders
)
  AND ($2::BIGINT IS NULL OR location_id = $2::BIGINT)
GROUP BY 1
ORDER BY 1
```

`VoteTracker` is the only mutable state, and it lives in the Durable Object, not in MotherDuck. It keeps one global instance (named `"global"`) with a `votes` table in Durable Object SQLite storage (`ctx.storage.sql`). `session_id` is the primary key, so each session has exactly one active vote; re-voting is an `ON CONFLICT ... DO UPDATE` upsert. The tally endpoint is a `GROUP BY candidate_id`. There is no write path from the browser to the warehouse.

```
 ┌──────────────┐  HTTPS   ┌────────────────────────┐   pg wire    ┌──────────────┐
 │   Browser    │ ───────► │    Cloudflare Worker   │ ───────────► │  MotherDuck  │
 │  (D3 + SPA)  │          │  ─ static assets       │              │  (duckoffee  │
 │              │          │  ─ /api/* SQL queries  │              │   share)     │
 │              │          │  ─ Durable Object:     │              └──────────────┘
 │              │  POST    │    VoteTracker         │
 │              │ ───────► │    (SQLite-backed)     │
 └──────────────┘          └────────────────────────┘
```

## Questions to answer

- Which MotherDuck share or database holds the analytics, and what is its share URI?
- What is the schema: a locations table, a per-day fact table, and a top-N dimension, plus the column names to use in the three queries?
- What region is the account in, so you set the right `MOTHERDUCK_HOST`?
- What is the voting question and the candidate list (8 to 12 items, kebab-case `id` values)?
- What lat/lon coordinates back each location and candidate city?
- What brand palette, copy, and assets should the frontend use?
- What Worker name and cache TTL do you want?

## Caveats

- The share URI is interpolated into the `ATTACH` statement, not parameterized. `DUCKOFFEE_SHARE` is operator-controlled config in `wrangler.toml`, not user input, so keep it that way: do not wire it to a request parameter or you reintroduce SQL injection on the attach.
- Do not put the token in `wrangler.toml`. `MOTHERDUCK_TOKEN` is a Wrangler secret (`wrangler secret put`) in production and lives in `.dev.vars` locally; `.dev.vars` should be gitignored. The `[vars]` block is for non-secret config (host, db, share) only.
- `nodejs_compat` is required and fails silently if dropped. Remove the flag and the `pg` import breaks at runtime, not at build time. If you see module-resolution errors for `pg`, this is the first thing to check.
- The Postgres host is region-specific. `pg.us-east-1-aws.motherduck.com` only works for accounts in that region. Point `MOTHERDUCK_HOST` at your own region's endpoint or connections will fail.
- Sample-data cities have no coordinates. `duckoffee.locations` stores city names but no lat/lon, so any city missing from `CITY_COORDS` returns `lon: null, lat: null` and silently will not plot on the map. Add a coordinate entry for every location you query.
- Candidate `id` is a permanent key. The `id` in `CANDIDATES` is the value stored in the Durable Object. Renaming it after votes exist orphans those votes under the old key, so pick a stable kebab-case `id` up front.
- Each browser tab is a distinct voter. The session ID is `sessionStorage`-backed, so two tabs count as two voters, and clearing storage creates a fresh voter. This is fine for a demo, not a substitute for real auth.
- One client per request. `withClient` opens and closes a `pg` connection per call rather than pooling; that is the simple, correct pattern for the Workers request model, but it is not a high-throughput connection pool.
- Edge cache hides fresh data. The three SQL endpoints are cached for `DATA_CACHE_TTL_SECONDS` (15 minutes) via the Cloudflare cache, so updates to the share will not appear until the TTL expires. Lower it while developing if you expect the data to change.

## What you'll adjust

| Setting | Purpose | Options / example |
| --- | --- | --- |
| `MOTHERDUCK_TOKEN` (Wrangler secret / `.dev.vars`) | Auth for the Postgres endpoint connection string | `npx wrangler secret put MOTHERDUCK_TOKEN`; locally `MOTHERDUCK_TOKEN="ey..."` in `.dev.vars` |
| `MOTHERDUCK_HOST` (`[vars]` in `wrangler.toml`) | Postgres endpoint host, region-specific | `pg.us-east-1-aws.motherduck.com` |
| `MOTHERDUCK_DB` (`[vars]`) | Database in the connection string | `sample_data` |
| `DUCKOFFEE_SHARE` (`[vars]`) | Share URI attached as `duckoffee` in `withClient` (`src/index.ts`) | `md:_share/duckoffee/1877e7c6-...`; or your own `md:_share/<org>/<uuid>` |
| `name` (`wrangler.toml`) | Deployed Worker name | `duckoffee-map` |
| SQL in `handleLocations` / `handleSales` / `handleSummary` (`src/index.ts`) | The three read-only queries against `duckoffee.locations`, `duckoffee.orders`, `duckoffee.order_items` | Repoint table/column names to your own share's schema |
| `CITY_COORDS` (`src/index.ts`) | Lat/lon lookup for cities in your locations table (sample data has no coordinates) | Add a `"City Name": [lon, lat]` entry per location |
| `CANDIDATES` (`src/index.ts`) | The 10 hardcoded voting candidates; `id` is the stable Durable Object key, do not rename | `{ id: "seoul", name: "Seoul", country: "South Korea", lon, lat }` |
| `DATA_CACHE_TTL_SECONDS` (`src/index.ts`) | Edge cache TTL for the three SQL endpoints | `15 * 60` (15 minutes) |
| `days` clamp in `handleSales` | Range and default for the daily series window | default 90, clamped to `[7, 365]` |
| Brand colors / assets (`public/style.css`, `public/assets/`) | Look and feel of the SPA | CSS custom properties at top of `style.css`; swap SVGs in `public/assets/` |

## Run it

Prerequisites: Node.js 18+, a Cloudflare account, and a MotherDuck account with an access token.

```sh
npm install

# Local dev: put your token in .dev.vars, then start the dev server
printf 'MOTHERDUCK_TOKEN="ey...MY_TOKEN"\n' > .dev.vars
npx wrangler dev
```

Visit http://localhost:8787. Open it in two tabs to watch votes propagate, each tab gets its own `sessionStorage` session ID, so each counts as a distinct voter.

Deploy:

```sh
# Set the token as a Worker secret (one time)
npx wrangler secret put MOTHERDUCK_TOKEN

npx wrangler deploy
```

The first deploy creates the `duckoffee-map` Worker, uploads `./public` through the `[assets]` binding, and provisions the `VoteTracker` Durable Object via the `[[migrations]]` entry (`new_sqlite_classes = ["VoteTracker"]`). Subsequent deploys just upload new code.

## Security

Sanitize input on every endpoint that accepts it:

1. Parameterize queries. Every route uses numbered parameters, e.g. `WHERE location_id = $1::BIGINT`, rather than string interpolation.
2. Validate inputs. `days` is parsed as an integer and clamped to `[7, 365]`; `location_id` is parsed and rejected with a 400 if it is not a valid integer; `candidate_id` must be one of the 10 hardcoded candidates; `session_id` must be a string of at most 64 characters.
3. Read-only warehouse workload. The Worker only issues `SELECT` statements against the attached share, so there is no path from user input to a MotherDuck write. Votes live entirely in the Durable Object.

## Files

- [`src/index.ts`](src/index.ts) - the whole Worker: the `withClient` Postgres helper, the three read-only SQL handlers, the `/api/votes` routes, the `CITY_COORDS` and `CANDIDATES` lookups, and the `VoteTracker` Durable Object class.
- [`wrangler.toml`](wrangler.toml) - Worker config: name, `nodejs_compat`, the `[assets]` binding, the non-secret `[vars]` (host, db, share), and the Durable Object binding and `[[migrations]]`.
- [`package.json`](package.json) - dependencies (`pg`, wrangler, types) and the `dev` / `deploy` / `types` npm scripts. `package-lock.json` pins the exact versions.
- [`tsconfig.json`](tsconfig.json) - TypeScript settings for the Worker (ES2022, bundler resolution, Cloudflare Workers types).
- [`.gitignore`](.gitignore) - keeps `node_modules/`, `.wrangler/`, `dist/`, and the secret `.dev.vars` out of git.
- [`public/index.html`](public/index.html) - the SPA shell: loads D3 v7 and topojson from CDN, lays out the map, stats card, and leaderboard, and references the bundled fonts and SVG duck assets.
- [`public/app.js`](public/app.js) - the D3 frontend logic: fetches the `/api/*` endpoints, draws the world map and sales chart, manages the per-tab `sessionStorage` voter ID, and polls the vote tally.
- [`public/style.css`](public/style.css) - the SPA styling, with the brand palette as CSS custom properties at the top.
- [`public/assets/`](public/assets/) - the hero image (`duckoffee.jpg`), duck and database SVGs, and the `AeonikMono-Regular.woff2` font used by the frontend.
- [`PROMPT.md`](PROMPT.md) - a self-contained prompt you can paste into a coding agent to rebuild this Worker plus Durable Object plus MotherDuck architecture with your own brand, dataset, and voting question.

## Learn more

- Recreate it with your own theme: [`PROMPT.md`](./PROMPT.md) is a self-contained prompt you can paste into a coding agent (Claude Code, Cursor, Codex, etc.) to build a variant with your own brand, dataset, and voting question on the same Worker + Durable Object + MotherDuck architecture.
- Connecting to the Postgres endpoint from other clients and regions: run the `ask_docs_question` MCP tool, or see the MotherDuck Postgres endpoint docs.
- Creating and attaching MotherDuck data shares: run the `ask_docs_question` MCP tool, or see the MotherDuck data sharing docs.
