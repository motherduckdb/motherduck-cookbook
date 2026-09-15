---
catalog: false
---

# Style preview harness

Renders the five dashboards in [`../assets`](../assets) so the README gallery can
be regenerated after a Guide changes.

```bash
npm install
npx playwright install chromium   # first run only
npm run shoot                     # writes ../assets/<style>.png
```

`npm run dev` serves the same pages at `http://localhost:5173` with hot reload;
`/?style=paper` opens one directly.

## What is real here and what is not

Each file in `src/dives/` was written from the matching Guide in `../styles/`,
the way an agent would read it, and is valid Dive source: a default-exported
component, `REQUIRED_DATABASES`, `useSQLQuery` with fully qualified SQL, standard
Tailwind utilities with inline styles for brand colors. `motherduck-default.tsx`
follows the built-in MotherDuck design guidance instead, as the baseline.

`src/queries.ts` holds the three queries all five share, so any difference
between the screenshots comes from the Guide rather than the data.

`src/md-sdk.tsx` is a fixture stand-in for `@motherduck/react-sql-query`. It
reads the `-- @fixture <name>` tag on each query and returns rows from
`src/fixtures.ts`, pulled once from `sample_data.nyc.taxi` for November 2022.
Counts are BigInt, as the runtime returns them, so the components still have to
convert numerics the way production requires. No token is needed and the
screenshots come out byte-identical each run.

For live data, replace `src/md-sdk.tsx` with the `md-sdk.tsx` from the MotherDuck
dives guide (`get_dive_guide`), which talks to `@motherduck/wasm-client`, and put
`VITE_MOTHERDUCK_TOKEN` in a git-ignored `.env`.

## Adding a style

1. Write the Guide in `../styles/<name>.sql`.
2. Write `src/dives/<name>.tsx` from that Guide alone. Keep it inside the
   880 x 620 viewport `shoot.mjs` uses — a Dive that scrolls in the standard
   viewport is a layout the Guide should have prevented.
3. Register it in `src/main.tsx` and in the `STYLES` list in `shoot.mjs`.
4. `npm run shoot`, then add the screenshot to the gallery in `../README.md`.
