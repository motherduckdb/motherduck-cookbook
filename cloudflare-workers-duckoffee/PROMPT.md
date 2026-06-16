# Recreate this project with your own theme

Copy the prompt below into your coding agent (Claude Code, Cursor, Codex, etc.) and edit the bits in `[brackets]` to point at your own dataset, brand, and voting question. The prompt is written to produce the same architecture as this example: a Cloudflare Worker that serves static assets and `/api/*` routes, reads analytics from MotherDuck over the Postgres endpoint, and tallies live votes in a Durable Object. Everything else (colors, copy, visuals, candidate list) is yours to reshape.

---

## The prompt

I want to build a full-stack Cloudflare Workers demo, deployed as a single Worker, that combines live analytics from MotherDuck with a real-time voting widget. Use this exact architecture:

1. **One Cloudflare Worker** handles both static assets (via the `ASSETS` Fetcher binding pointing at a `./public` directory) and a small set of JSON `/api/*` routes. `wrangler.toml` should set `not_found_handling = "single-page-application"` on the `[assets]` block.
2. **MotherDuck over the Postgres wire protocol** — connect with the `pg` npm package using a connection string of the form `postgresql://anyusername:${MOTHERDUCK_TOKEN}@${MOTHERDUCK_HOST}:5432/${MOTHERDUCK_DB}?sslmode=require`. After connecting, run `ATTACH IF NOT EXISTS '<share-uri>' AS <alias>` so the Worker can read from a MotherDuck share. Do **not** bundle a DuckDB binary. The token is a Wrangler secret; the host, database, and share URI are plain `[vars]` in `wrangler.toml`.
3. **A Durable Object named `VoteTracker`** holds the live tally. Use the SQLite-backed storage (`ctx.storage.sql`) and declare it in `wrangler.toml` with `[[migrations]]` using `new_sqlite_classes = ["VoteTracker"]`. Schema: one `votes` table keyed by `session_id`, with `candidate_id` and `cast_at`. Casting a vote is an UPSERT on `session_id` so each session has exactly one active vote that can be changed at any time. One global instance, named `"global"` via `idFromName`.
4. **A static single-page frontend** served from `./public/` that uses **D3 v7 and topojson-client from CDN** (no bundler, no framework). The page renders a world map, a time-series chart, a small stat grid, and a live leaderboard driven by the vote API. Each browser gets a session ID stored in `sessionStorage` so two tabs count as two voters.

### The theme

The project is called **[YOUR PROJECT NAME]**. It should feel like [describe the vibe, e.g. "a cozy indie coffee chain", "a synthwave record label", "a botanical garden network"]. Pick a small brand palette (3–5 colors) and define them as CSS custom properties at the top of `public/style.css`. Use a playful hero image and a short tagline in the header that mentions the underlying stack (MotherDuck Postgres endpoint, Cloudflare Worker, Durable Object).

### The data

Back the analytics with [describe your dataset]. For this example you can use [one of: a MotherDuck share you already own / the `sample_data` database / a synthetic dataset you generate with a small SQL script]. The shape the frontend expects is:

- A set of **locations** with an id, a display name, a city, a country, lat/lon, and aggregated metrics (e.g. revenue and order count).
- A fact table that can be grouped **per day** to produce a time series, with an optional `location_id` filter.
- A way to compute a **summary** (totals + a top-5 list of something) either globally or scoped to a single location.

Expose three read-only SQL-backed endpoints that mirror this shape:

| Route | Behavior |
| --- | --- |
| `GET /api/locations` | Every location with its lifetime totals and coordinates. |
| `GET /api/sales?location_id=&days=` | Daily time series. `days` parsed as int, clamped to `[7, 365]`, default 90. `location_id` parsed as int; reject invalid. Use `$1::INTEGER` / `$2::BIGINT` parameters. |
| `GET /api/summary?location_id=` | Totals + top 5. Reject invalid `location_id`. |

Wrap these three endpoints in an edge cache (`caches.default`) with a 15-minute TTL keyed by URL, using `ctx.waitUntil` to populate the cache.

### The voting question

Ask users: **"[YOUR VOTING QUESTION, e.g. 'Where should we open next?']"**. Hardcode a list of **8–12 candidates** in the Worker as a `CANDIDATES` array of `{ id, name, country, lon, lat }`. Pick `id` values in kebab-case — they're the stable key stored in the Durable Object, so choose carefully (don't rename later). Expose:

| Route | Behavior |
| --- | --- |
| `GET /api/votes?session_id=` | Returns `{ candidates: [...with votes], total_votes, your_vote }`. |
| `POST /api/votes` | Body `{ session_id, candidate_id }`. Reject if `session_id` is missing, non-string, or longer than 64 chars. Reject if `candidate_id` is not in the hardcoded set. |

### The frontend

`public/index.html` should include a header, a map card, a leaderboard, and a stats card. `public/app.js` should:

- Fetch `/api/locations` and render a D3 world map (use a topojson world-atlas from a CDN). Draw existing locations as one marker style and candidate cities as another. Highlight the user's current vote with a third style.
- Fetch `/api/sales` (and re-fetch when a location is clicked) and render a bar or line chart.
- Fetch `/api/summary` and drive a 3-tile stat grid plus a top-5 list.
- Fetch `/api/votes` on load and every ~5 seconds while the tab is visible; re-fetch immediately after a successful `POST`.
- Store the session ID in `sessionStorage` (create one with `crypto.randomUUID()` on first load).
- Clicking a candidate (on the map or in the leaderboard) POSTs a vote. Clicking an existing location filters the chart/stats; add a visible "Clear filter" button to reset.

### Security, non-negotiables

- **Every SQL statement uses numbered parameters** (`$1`, `$2`, …) — never string-interpolate user input into SQL.
- **Validate inputs on every endpoint.** Reject invalid integers. Clamp ranges. Whitelist candidate IDs.
- **Only SELECT** against the attached share. The Worker must have no write path to MotherDuck. All mutable state lives in the Durable Object.
- Token is a Wrangler secret (`wrangler secret put MOTHERDUCK_TOKEN`), and a `.dev.vars` is used for local dev.

### Deliverables

- `wrangler.toml` with `name`, `main = "src/index.ts"`, `compatibility_flags = ["nodejs_compat"]`, an `[assets]` block, `[vars]`, a `[[durable_objects.bindings]]` entry, and a `[[migrations]]` with `new_sqlite_classes`.
- `src/index.ts` containing the `Env` interface, the `CANDIDATES` constant, a `withClient` helper that connects + attaches + runs a callback + always closes, the `cached` helper, per-route handlers, the default `fetch` export (routes `/api/*`, falls back to `env.ASSETS.fetch(req)`), and the `VoteTracker` Durable Object class.
- `public/index.html`, `public/app.js`, `public/style.css`, and a small `public/assets/` folder with at least one SVG logo / illustration that fits the theme.
- A `README.md` at the project root explaining prerequisites, `npm install`, `npx wrangler secret put MOTHERDUCK_TOKEN`, `npx wrangler dev`, `npx wrangler deploy`, the route table, an ASCII architecture diagram, and a short customization guide (how to add a candidate, how to swap the dataset, where to change colors).

### Deploy

The project must deploy as a single Worker with `npx wrangler deploy` using the `wrangler.toml` described above. No other hosting, no separate static-site bucket, no external database provisioning step. The first deploy should create the Worker, upload the `./public` assets through the `[assets]` binding, and provision the `VoteTracker` Durable Object via the `[[migrations]]` entry automatically. Pin `compatibility_date` and include `nodejs_compat` in `compatibility_flags` so the `pg` driver works on the Workers runtime.

Do not add a framework, a bundler, a database other than the Durable Object's SQLite, or any auth layer. Keep the total code small enough to read end-to-end in one sitting.
