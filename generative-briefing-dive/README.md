---
title: Build a Self-Learning Briefing Dive
id: generative-briefing-dive
description: >-
  A Dive that writes itself: a Flight plans each page from a semantic catalog
  with prompt(), runs follow-up queries to explain what moved, checks every
  number against the data, and remembers earlier briefings, viewer ratings and
  your Guides, so insights continue, worsen or resolve instead of repeating.
  Viewers ask questions, investigate insights and rearrange the page. Use when
  you want a daily briefing on your own data that answers new questions and
  improves as people use it.
type: example
category: analytics
features: [dives, flights]
tags: [python, typescript]
prompt: >-
  I want a generated daily briefing Dive on my own MotherDuck data that answers
  questions with new pages, drills into why numbers moved, and learns from
  earlier briefings, viewer feedback and our Guides. Help me adapt the "Build a
  Self-Learning Briefing Dive" recipe to my own data and use case, using it as a
  guide: https://motherduck.com/docs/cookbook/generative-briefing-dive
published_date: 2026-11-09
refs:
  - kind: dataset
    database: sample_data
    tables: [nyc.service_requests]
    attach: ATTACH 'md:_share/sample_data/23b0d623-1361-421d-ae77-62d701d471e6' AS sample_data
    docs: /getting-started/sample-data-queries/nyc-311-data
  - kind: doc
    path: /key-tasks/guides
  - kind: doc
    path: /key-tasks/dives/run-flights-from-dives
  - kind: skill
    id: motherduck-build-malleable-dive
  - kind: skill
    id: motherduck-dive-components
---

# Build a Self-Learning Briefing Dive

A Dive whose pages are generated, not hand-built, and that gets better the more it is used. Every morning a Flight writes a briefing. It covers the numbers that matter, the insights that need attention with how each changed since last week, and the charts that show the evidence. Viewers can ask a new question and get a new page, or click **Investigate** on an insight to get a drill-down that explains it.

The example uses NYC 311 service requests from MotherDuck's public `sample_data` share. Five weekly briefings in, it flags heat-complaint closures as *worsening since 12 December*. A follow-up query it chose found Queens at 3.67 days against the 3-day target in its Guide, and the headline shows "29/29 numbers checked".

![The NYC 311 briefing](assets/briefing.png)

## How it works

For a visual walkthrough, open [`explainer.html`](explainer.html) in a browser after cloning the repository. It shows every step from the components to the learning loop, how the Flight is triggered, which tables it writes, and where a viewer's answer goes.

```
Dive ──useRunFlight({ MODE, PROMPT, PARENT, FEEDBACK, … })──► Flight
  marts     curated tables from your sources
  plan      prompt() picks catalog measures, dimensions and components (strict JSON schema)
  compile   Python validates the plan and writes the SQL; the model never does
  probe     prompt() proposes follow-up queries that test why something moved
  narrate   prompt() writes the headline and insights, using memory, Guides and the
            follow-up results; every number is checked against the data
  publish   one JSON spec row the Dive renders, plus log rows
  learn     a memory Guide: what the briefing has learned
Dive ◄── useFlightRun ── opens the new page
```

Four things make it learn:

- **Memory.** Every run, model call, insight and viewer signal is logged in the output database. The next briefing reads back the last three briefings' insights and marks each new one as new, ongoing, worsening, improving or resolved. Viewer ratings and the chart types people switch to steer the next analysis and the default layout.
- **Guides.** The Flight reads every MotherDuck Guide under `briefings/<db>`. That includes your business context (audience, targets, known patterns) and the memory Guide it maintains itself. Edit a Guide and the next briefing follows it. The Dive's AI Chat and other agents read the same Guides.
- **Questions.** When the data shows what moved but not why, such as a spike in one week or one referrer, the briefing asks its viewers, with a few likely answers to pick from. An answer becomes a note. The note appears on every chart that shows that date or value, and later briefings use it as the explanation. Questions nobody has answered stay on the page. Viewers can also pin a note to any chart.
- **Evals.** `eval.py` scores the planner, and with `--narrate` the share of grounded numbers, on a fixed question set. Results are recorded per prompt version and model, so you can compare a prompt or model change before it ships. The Dive's **Memory** page shows the insight timeline, what viewers asked and did, and every prompt version.

![The Memory page](assets/memory.png)

## Questions to answer

Ask these before adapting the recipe; the answers go into a Guide (see `business-context.sql`), the recipe and `questions.json`:

1. Who reads the briefing, how often, and what do they decide after reading it?
2. Which 3 to 5 numbers matter most, how exactly are they defined, and which are better when lower?
3. What are the targets, and what size of change is worth reporting?
4. Compare with what: the previous period, last year, or the target?
5. Which breakdowns explain a change, and which should the briefing ignore?
6. Which known patterns, events or data quirks matter?
7. Who acts on which kind of insight, and what does a useful one look like?
8. Which questions do you expect to ask it?

After the first briefing, ask which insight they would act on, which is noise, and what is missing, and update the Guide.

## What you'll adjust

Copy `recipes/nyc-311` to `recipes/<name>` and replace:

- **`domain.py`:**
  - `DB` (a new output database) and `ATTACH` (your sources).
  - `MARTS`: curated tables, pre-aggregated where you can.
  - `CATALOG`: datasets, dimensions and measures, with descriptions the planner reads. Mark measures where lower is better with `"down"`, and include the dimensions that explain why a number moves.
  - `WATCH`: metrics for the change scan.
  - `BRIEFING_PLAN`: the fixed daily page.
  - Context for the model, and the learning settings: `GUIDE_TOPICS`, `ANALYST_PROBES`, `DEFAULT_MODEL`.
- **`app.json`:** brand name, theme (see [Theme and brand](#theme-and-brand)), example questions and the Flight name.
- **`questions.json`:** 8 to 12 questions with what a good plan uses, plus one off-topic question.
- **`business-context.sql`:** your interview answers as a Guide.

The components live in `dive-components/`: KPI rows, metric cards, gauges, line, area, bar, table, calendar and matrix heatmaps, Sankey, treemap, timeline, callouts and a date picker. Add your own by copying `dive-components/examples/funnel.tsx`.

## Theme and brand

Ask which look the readers expect, and whether there is a brand colour or a style guide. Then set `theme` in `app.json` one of three ways.

**1. Pick a built-in theme.** Set `"theme"` to its key:

| Key | Look |
| --- | --- |
| `swiss` | White page, Helvetica, no cards. Greyscale series told apart by dashes, with red only for emphasis. |
| `paper` | Tufte: cream page, book serif (ET Book, falling back to Palatino or Georgia), no cards and no gridlines. Greyscale series with one muted red for emphasis. |
| `motherduck` | The default MotherDuck Dive look: flat, light grey page, MotherDuck palette. |
| `duck` | MotherDuck brand: beige page, hard borders and offset shadows, bright series. |
| `night` | Dark page with rounded panels. |

**2. Change the brand colour.** Use a theme as the base and set `accent`:

```json
"theme": { "base": "paper", "name": "Acme", "accent": "#0f766e" }
```

On `swiss` and `paper` the accent marks only emphasis: highlighted values, the critical tone and active controls. The series stay greyscale. On the other themes the accent also becomes the first series colour.

**3. Bring your own style guide.** Any theme setting can be overridden. Settings you leave out come from `base`:

```json
"theme": {
  "base": "swiss",
  "name": "Acme",
  "accent": "#ff5a00",
  "bg": "#ffffff", "panel": "#ffffff", "text": "#1a1a1a", "muted": "#6b6b6b",
  "line": "#e5e5e5", "rule": "#1a1a1a", "grid": "#f0f0f0",
  "font": "\"Acme Sans\", Inter, sans-serif", "heading": "\"Acme Display\", Inter, sans-serif",
  "series": ["#ff5a00", "#1a1a1a", "#8a8a8a", "#ffb48a", "#4a4a4a", "#c4c4c4"],
  "tones": { "positive": { "fg": "#127a3a" }, "critical": { "fg": "#c4001a", "line": "#c4001a" } },
  "radius": 0, "gridlines": true
}
```

- **Colours:** `bg` (page), `panel` (cards and popovers), `text`, `muted`, `line` (hairlines), `rule` (heavy rules), `grid` (gridlines and gauge tracks), `accent`, `accentSoft` (selected backgrounds), `highlight` (annotated points).
- **Series:** `series` holds up to six chart colours. To tell greyscale series apart by line style, set `"mono": true` and `dashes`, such as `[null, null, "6 3", "2 3"]`.
- **Tones:** `tones.critical`, `warning`, `positive`, `info` and `neutral`, each with `fg`, `bg` and `line`. Set only the parts you want to change.
- **Type:** `font` (body), `heading` (titles and headline figures), `displayWeight` and `displayTracking` (weight and letter spacing of titles and headline figures). The fonts must be installed on the viewer's machine, so end each stack with a common fallback.
- **Shape:** `flat` (no card chrome, ruled sections), `radius`, `border`, `shadow`, and `gridlines` (`false` hides chart gridlines).

All settings are listed in `THEMES` in `dive-components/kit/core.tsx`. A brand theme appears in the viewer's theme switcher under its `name`. To limit the switcher, list the themes viewers may pick in `"themes"`, such as `["brand", "paper"]`. With only one theme listed, the switcher is hidden. A board in `boards` can set its own `theme` and `themes`.

## Boards: one Flight, several dashboards

A board is a dashboard for one audience on the recipe's data. Every board shares the sources, marts, catalog, Flight and viewer notes. Each board has its own morning page, its own memory and its own Dive. Use boards when different audiences read the same data, such as a daily team view and a monthly leadership view. Use a separate recipe and Flight for different data.

```python
BOARDS = {
    "operations": {"LABEL": "Operations"},            # the first board uses the settings above
    "leadership": {
        "LABEL": "Leadership",
        "CADENCE": "monthly",                          # report on the last complete month
        "AUDIENCE": "city leadership, reading once a month",
        "BRIEFING_QUESTION": "…",
        "DEFAULT_COMPARE": "previous_year",
        "WATCH": [("requests.requests", "Requests"), …],
        "PLANNER_HINTS": PLANNER_HINTS + "\n- This is a monthly leadership view: …",
        "BRIEFING_PLAN": {"title": "…", "blocks": [{"type": "kpi", "window": "last_full_month", …}, …]},
    },
}
```

- **Settings:** a board can override `AUDIENCE`, `BRIEFING_QUESTION`, `BRIEFING_PLAN`, `WATCH`, `PLANNER_HINTS`, `DEFAULT_COMPARE`, `CADENCE` and `LABEL`. Anything it leaves out comes from the recipe.
- **Monthly boards:**
  - The windows `last_full_month` and `last_12_full_months` cover calendar months. Use them with `time_grain: "month"`.
  - A monthly comparison is month against month: the month before with `previous_period`, or the same month a year earlier with `previous_year`.
  - With `CADENCE: "monthly"`, a briefing reports on the last complete month. The scheduled run writes it once, when the month is new, and the page leaves out the weekly change table.
- **The Flight:** the `BOARD` config key picks the board. A scheduled briefing without `BOARD` writes every board in one run.
- **Tables:** `dive_specs`, `gen_runs`, `insights` and `feedback` have a `board` column, so memory, ratings and questions stay per board. `annotations` and the Guides are shared, so a note added on one board explains the data on all of them. Each board after the first writes its own memory Guide, "What the `<DB>` `<label>` briefing has learned".
- **Dives:** in `app.json`, `boards` maps each board to its label and any overrides, such as `tagline` and `examples`. `build.py` writes `dive/` for the first board and `dive-<board>/` for each of the others. `deploy.py` deploys one Dive per board, writes each Dive's id into `app.json`, and rebuilds so every Dive links to the others in its top bar.

The NYC 311 recipe has two boards: `operations`, the daily briefing, and `leadership`, a monthly view of the last complete month against the same month a year earlier.

## Run it

```bash
export MOTHERDUCK_TOKEN=...
python3 build.py recipes/nyc-311                      # writes recipes/nyc-311/flight/main.py and dive/index.tsx
cd recipes/nyc-311/flight
MODE=briefing REQUEST_ID=t1 uv run --with duckdb==1.5.6 python main.py
MODE=ask PROMPT="Which agencies take longest to close requests?" REQUEST_ID=t2 uv run --with duckdb==1.5.6 python main.py
cd ../../..
uv run --with duckdb==1.5.6 python eval.py recipes/nyc-311 --out baseline.json
```

Run the business-context Guide once (`business-context.sql`), and run several briefings with consecutive `AS_OF=YYYY-MM-DD` dates to see insights continue across weeks.

Deploy the Flight and the Dive:

```bash
uv run --with duckdb==1.5.6 python deploy.py recipes/nyc-311 --dry-run   # shows what it will create or update
uv run --with duckdb==1.5.6 python deploy.py recipes/nyc-311
```

`deploy.py` creates or updates the Flight with `MD_CREATE_FLIGHT` or `MD_UPDATE_FLIGHT`, finding it by name in `MD_LIST_FLIGHTS()`. It writes the Flight id into `app.json`, rebuilds, and creates the Dive or adds a version with `MD_CREATE_DIVE` or `MD_UPDATE_DIVE_CONTENT`. Then run the briefing once (`SELECT * FROM MD_RUN_FLIGHT(flight_id := '<id>')`) and open the Dive.

To let others use it:
- **Read access:** they need a share of the output database. Set `requiredDatabases` in `app.json` to the share.
- **Asking questions and rating:** they need run access on the Flight: `GRANT RUN ON FLIGHT '<flight_id>' TO ROLE <role>`. Every run uses the Flight owner's credentials, so grant it to the people the Dive is for.

## Caveats

- **Run time:** each generation makes two or three `prompt()` calls (plan, follow-up queries, narrative) and takes about 15 to 35 seconds. Set `ANALYST_PROBES = 0` to drop the follow-up step.
- **Writes:** a Dive can't write to a database. Viewer feedback reaches the log tables only through a Flight run, which validates every event.
- **Model:** `DEFAULT_MODEL` names the `prompt()` model. Check current model names in the AI functions docs.
- **Agent skills:** the recipe matches the `motherduck-build-malleable-dive` and `motherduck-dive-components` agent skills. With them installed, an agent can run the interview and adapt the recipe for you.

## Files

| Path | What it is |
| --- | --- |
| `template/engine.py` | The Flight engine: plan, compile, probe, narrate, publish, learn |
| `template/app.tsx` | The Dive: pages, ask bar, Investigate, Arrange mode, ratings, Memory page |
| `dive-components/` | The component kit and its catalog (`components.json`), copied into the Dive |
| `recipes/nyc-311/` | The example recipe, eval questions included |
| `build.py` | Builds a recipe into a single-file Flight and Dive |
| `eval.py` | Scores a recipe's planner and narrator |
| `deploy.py` | Creates or updates the Flight and the Dive |
| `business-context.sql` | The interview answers as a Guide |
| `explainer.html` | A visual walkthrough of the whole flow; open it in a browser |
| `LEARNING.md` | The log tables, memory, feedback, the number check and evals in detail |

## Learn more

- [Guides](https://motherduck.com/docs/key-tasks/guides/) and the [Guide SQL functions](https://motherduck.com/docs/sql-reference/motherduck-sql-reference/guides/)
- [Run Flights from Dives](https://motherduck.com/docs/key-tasks/dives/run-flights-from-dives/)
- [`prompt()`](https://motherduck.com/docs/sql-reference/motherduck-sql-reference/ai-functions/prompt/)
- [NYC 311 sample data](https://motherduck.com/docs/getting-started/sample-data-queries/nyc-311-data/)
