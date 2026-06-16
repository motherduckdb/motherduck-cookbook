# nba-box-scores — dive (frontend)

The consolidated NBA Box Scores **Dive** that replaces the legacy Next.js/Vercel
frontend. Reads `nba_box_scores_v3` directly via `useSQLQuery`. The companion
ingest pipeline lives in [`../flight`](../flight).

![NBA Box Scores Dive — schedule with an open box-score panel](assets/nba-box-scores-dive.png)

Three tabs, sharing one season/type/team/player filter bar:
- **Schedule** — games grouped by date → click-through box-score modal → per-player game log.
- **Game Quality** — player Game Quality leaderboard (drillable to game log).
- **Trends** — points/game vs. avg Game Quality scatter.

## Layout

```
nba-box-scores/dive/
├── src/            # the dive: dive.tsx (default export), components/, lib/
├── bundle.mjs      # esbuild → one self-contained dist/dive.jsx
├── package.json    # build only (esbuild)
└── dist/           # build output (git-ignored)
```

The local Vite preview harness (`.dive-preview/`) is **git-ignored** — it's a
throwaway dev scaffold (see the MotherDuck dives guide to regenerate it). The
source of record is `src/`; the deployed artifact is produced by `bundle.mjs`.

## Build

```bash
cd nba-box-scores/dive
npm install      # esbuild
npm run build    # → dist/dive.jsx
```

`bundle.mjs` inlines the local `src/` imports and keeps the runtime-provided
libraries external (`react`, `react-dom`, `recharts`, `d3`, `lucide-react`,
`@motherduck/react-sql-query`).

## Deploy

One command builds the bundle and deploys it:

```bash
export MOTHERDUCK_TOKEN=<token with nba_box_scores_v3 read+write>
./scripts/deploy-dive.sh
```

[`scripts/deploy-dive.sh`](./scripts/deploy-dive.sh) resolves the Dive by
**title** (default `NBA Box Scores`) via `MD_LIST_DIVES()` — it creates the Dive
the first time and updates its content on every run after, so no Dive id is
pinned in the repo. Override `DIVE_TITLE` to deploy a preview, or
`NBA_DIVE_DATABASE` to bind the dive's `nba_box_scores_v3` alias to a
differently-named source database. The script uses the DuckDB CLI, so use a
**1.5.2** client (MotherDuck rejects 1.5.3).

Under the hood it reads the bundle from disk and calls the MotherDuck SQL
functions — `MD_CREATE_DIVE` (first run) / `MD_UPDATE_DIVE_CONTENT` (updates):

```sql
SET VARIABLE c = (SELECT content FROM read_text('dist/dive.jsx'));
FROM MD_UPDATE_DIVE_CONTENT(
  id := '<dive-id-from-MD_LIST_DIVES>'::UUID,
  content := getvariable('c'),
  required_resources := [{'url': 'md:nba_box_scores_v3', 'alias': 'nba_box_scores_v3'}],
  api_version := 1
);
```

The MCP `save_dive` / `update_dive` tools also work but require pasting the full
content as a parameter.

## Data notes

- Aggregate by stable `entity_id`, not `player_name` — names drift across
  seasons (e.g. "Nikola Jokić" / "Nikola Jokic"). Show the latest via
  `arg_max(player_name, game_date)`.
- `game_quality = -1` is the sub-15-min sentinel; exclude it (`>= 0`) from GQ
  averages.
- The MotherDuck dive renderer ignores Tailwind responsive prefixes (`md:` etc.)
  and arbitrary bracket values (`z-[60]`); use inline `style` for custom values.
