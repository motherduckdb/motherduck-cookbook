# StatsBomb 360 Dive — replay + Passes & Shots

A single-file MotherDuck Dive ([`src/dive.tsx`](./src/dive.tsx)) with two views,
switched by a tab and a searchable match picker in the header:

- **Replay** — an animated pitch: players (sky = home, watermelon = away), the
  ball, the pass/carry/shot vector, a fading ball trail, a scrubbable timeline
  with goal/card/restart markers, play/pause and 1x/2x/4x speeds. 360 tracking is
  anonymous, so dot identity is invented frame-to-frame (greedy nearest-neighbor
  with name hints from the event stream).
- **Passes & Shots** — per-team shot maps (dot area = xG, goals in sun-yellow,
  lines styled by outcome) and per-player side-on pass/shot flight rows.

It reads the `marts.*` tables (`replay_events`, `replay_dots`, `replay_markers`,
`pass_flights`) and `core.*` (`matches`, `stg_events` for shot xG) live, under the
resource alias `statsbomb`. Build the data first with the [`../flight/`](../flight/) Flights.

## Why D3 inside React

Dives are Recharts-first, but an animated match replay and side-on flight rows
are bespoke SVG that Recharts can't express. So the Dive runs D3 inside a
`useEffect` against `useRef` containers (D3 owns the SVG subtree, React owns the
shell and the SQL), reconstructs a per-match `{ match, frames, markers }` object
from the SQL rows, and injects its CSS via a `<style>` block. It's a useful
pattern whenever a Dive needs visuals beyond the chart library.

## Develop

Edit [`src/dive.tsx`](./src/dive.tsx) and re-run the deploy script to publish the
change. To iterate with a live local preview against MotherDuck, drop the file
into MotherDuck's Dive local-preview scaffold (a small Vite harness that renders a
single `.tsx` against live data) pointed at `src/dive.tsx` — see the MotherDuck
Dives documentation.

## Deploy

```bash
export MOTHERDUCK_TOKEN=<token with read on statsbomb>
./scripts/deploy-dive.sh
```

The script resolves the Dive by **title** via `MD_LIST_DIVES()` — creating it the
first time and updating its content after — and binds the `statsbomb` alias to
`md:statsbomb`. Override with `DIVE_TITLE` and `SB_DATABASE`. It needs a DuckDB
**1.5.3** CLI on `PATH` and prints the Dive URL.

To run the Dive against the prebuilt **public share** instead of a database you
built yourself, set `SB_RESOURCE_URL` to the share URL — see _Try it without
building_ in the [top-level README](../README.md#try-it-without-building).

## Caveats

- The Dive reads your private `statsbomb` database, so it works for you out of
  the box. To let teammates open it, share the database with your organization
  (in the MotherDuck UI, or the Dive's "share data" action).
- `src/dive.tsx` is a single self-contained component (no local imports), so
  there is no build step — the file itself is what gets deployed.
- The default match is the 2022 World Cup Round of 16 NED–USA fixture; the picker
  searches across every match that has 360 data in your `statsbomb` database.
