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

The example uses NYC 311 service requests from MotherDuck's public `sample_data` share. That data is static, so the example replays one briefing a week with `AS_OF` dates. Five briefings in, it flags heat-complaint closures as *worsening since 12 December*. A follow-up query it chose found Queens at 3.67 days against the 3-day target in its Guide, and the headline shows "29/29 numbers checked".

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
- **Questions to viewers.** When the data shows what moved but not why, such as a spike in one week or one referrer, the briefing asks its viewers, with a few likely answers to pick from. An answer becomes a note. The note appears on every chart that shows that date or value, and later briefings use it as the explanation. Questions nobody has answered stay on the page. Viewers can also pin a note to any chart.
- **Evals.** `eval.py` scores the planner, and with `--narrate` the share of grounded numbers, on a fixed question set. Results are recorded per prompt version and model, so you can compare a prompt or model change before it ships. The Dive's **Memory** page shows the insight timeline, what viewers asked and did, and every prompt version.

![The Memory page](assets/memory.png)

## Questions to answer

Most of these can be answered from the data, so investigate before asking anyone. Work in three passes, and keep the questions for a person to the few that only a person can answer: about five, each with a proposed answer to confirm or correct.

**1. Find out from the data.** Query the sources and settle these without asking:

| Question | How to find out | Where the answer goes |
| --- | --- | --- |
| Which tables hold the data, what is one row, and which column is the date? | `duckdb_tables()`, `duckdb_columns()`, a sample of rows | `ATTACH`, `MARTS`, `ANCHOR` |
| How often does the data load, and how far back does it go? | Minimum and maximum date, rows per day over recent weeks | `MARTS_REFRESH`, `AS_OF_DEFAULT`, `flight.schedule` |
| When is a period complete? | For measures that depend on a later event, such as a closing date, compare the newest weeks with older ones | The date cut-off in `MARTS` |
| Which columns can break a number down? | Distinct counts and null shares per column | `CATALOG` dimensions |
| Which patterns repeat? | Weekly and yearly rhythm in the main counts | "Known patterns" in the Guide |
| Are metrics already defined somewhere? | Existing Guides (`MD_LIST_GUIDES`), table and column comments | `GUIDE_TOPICS`, `CATALOG` descriptions |

**2. Draft, then confirm in one pass.** Propose these from what the data shows and ask for corrections, not for answers from scratch:

| Question | Where the answer goes |
| --- | --- |
| Which 3 to 5 numbers matter most, and how is each defined? | `CATALOG` measures, `WATCH`, `LABELS` |
| Which of them are better when lower? | `"down"` on the measure |
| Compare against the previous period or the same period last year? | `DEFAULT_COMPARE` |
| Which breakdowns explain a change, and which should the briefing ignore? | `CATALOG` dimensions, `PLANNER_HINTS` |
| Which questions will people ask it? | `examples` in `app.json`, `questions.json` |
| Which acronyms and labels do readers use? | `ACRONYMS`, `LABELS` |

**3. Needs a person.** The data can't answer these:

| Question | Where the answer goes |
| --- | --- |
| Who reads the briefing, how often, and what do they decide after reading it? | `AUDIENCE`, `BRIEFING_QUESTION`, `CADENCE`, the Guide |
| Does a second audience read the same data? | `BOARDS` (see [Boards](#boards-one-flight-several-dashboards)) |
| What are the targets, and what size of change is worth reporting? | The Guide; there is no threshold setting |
| Which events or data issues matter that the data doesn't show? | The Guide, `DOMAIN_CONTEXT` |
| Who acts on which kind of insight? | The Guide |
| Who may view, who may ask questions, and which columns must not reach the model? | The share, `GRANT RUN`, the columns in `MARTS` (see [Security](#security)) |
| Which look do readers expect? | `theme` (see [Theme and brand](#theme-and-brand)) |

**Don't wait for every answer.** Build the first briefing on the best available answer, and write each assumption into the Guide under a heading such as "Assumptions to confirm", so a person can correct it by editing the Guide. A real page gets better feedback than a list of questions: after the first briefing, find out which insight readers would act on, which is noise, and what is missing, and update the Guide. Unexplained spikes need no question up front either, because the briefing asks its viewers about them on the page.

## Caveats

- **Live data:** the example's data is static, so it ships `MARTS_REFRESH = "missing"` (build the marts once) and `AS_OF_DEFAULT = "max"` (report up to the latest date in the data). For data that keeps loading, set `MARTS_REFRESH = "always"`, or the briefing never changes. Set `AS_OF_DEFAULT = "yesterday"` when the latest day is still filling in.
- **History:** the change scan skips a metric with less than about 30 weeks of history (`BASELINE_WEEKS` plus 4). The comparison with last year needs a full year more than the window.
- **Continuity:** insights are marked ongoing, worsening or resolved only once earlier briefings exist, so the first page shows every insight as new.
- **Run time:** each generation makes two or three `prompt()` calls (plan, follow-up queries, narrative) and takes about 15 to 35 seconds. Set `ANALYST_PROBES = 0` to drop the follow-up step. Every question a viewer asks is a Flight run with these calls.
- **Writes:** a Dive can't write to a database. Viewer feedback reaches the log tables only through a Flight run, which validates every event.
- **Model:** `DEFAULT_MODEL` names the `prompt()` model. Check current model names in the AI functions docs.
- **Wrong tool:** for a fixed dashboard with no narrative, build a plain Dive. For a question asked once, use AI Chat. The recipe pays off when the same audience reads it repeatedly.
- **Agent skills:** the recipe matches the `motherduck-build-malleable-dive` and `motherduck-dive-components` agent skills. With them installed, an agent can investigate the data, run the interview and adapt the recipe for you.

## What you'll adjust

Copy `recipes/nyc-311` to `recipes/<name>` and replace these in `domain.py`:

| Setting | What it does | Example |
| --- | --- | --- |
| `DB` | The output database the Flight creates and writes | `"nyc_311_briefing"` |
| `ATTACH` | Sources to attach, as alias and path | `[("sample_data", "md:_share/…")]` |
| `MARTS` | Curated tables, pre-aggregated where you can | `{"mart_requests": "SELECT …"}` |
| `MARTS_REFRESH` | When to rebuild the marts | `"missing"` (once), `"always"` (every briefing) |
| `ANCHOR` | The mart and date column that set the data's date range | `("mart_requests", "day")` |
| `AS_OF_DEFAULT` | The date a briefing reports up to | `"max"`, `"yesterday"` |
| `CATALOG` | Datasets, dimensions and measures, with descriptions the planner reads. Mark a measure where lower is better with `"down"` | See `recipes/nyc-311/domain.py` |
| `WATCH` | Measures for the change scan | `[("requests.requests", "Requests")]` |
| `BASELINE_WEEKS` | Weeks the change scan uses as its baseline | `26` |
| `DEFAULT_COMPARE` | The comparison when a question names none | `"previous_period"`, `"previous_year"`, `"none"` |
| `BRIEFING_PLAN` | The blocks of the scheduled page | KPI row, line, bar, heatmap, table |
| `CADENCE` | What a briefing reports on | `"daily"` (default), `"monthly"` |
| `AUDIENCE`, `BRIEFING_QUESTION` | Who the narrator writes for, and the question each briefing answers | `"city operations analyst"` |
| `DOMAIN_DESCRIPTION`, `DOMAIN_CONTEXT` | What the data is, in a sentence and in a paragraph | |
| `PLANNER_HINTS` | Rules for the planner: which dimension explains what | `"- Use descriptor to explain a complaint type's change"` |
| `ACRONYMS`, `LABELS` | How names are displayed | `{"nypd": "NYPD"}` |
| `GUIDE_TOPICS` | More Guide topics to read | `["core-metrics"]` |
| `ANALYST_PROBES`, `VIEWER_QUESTIONS` | Follow-up queries per page, and questions to viewers per page; `0` turns either off | `3`, `2` |
| `DEFAULT_MODEL` | The `prompt()` model | |

And the other recipe files:

| File | What to set |
| --- | --- |
| `app.json` | `brand`, `tagline`, `placeholder`, `examples` (example questions), `db` (the same as `DB`), `theme` (see [Theme and brand](#theme-and-brand)), `flight.name`, `flight.schedule` (cron in UTC, `0 6 * * *` by default), `requiredDatabases` (see [Security](#security)) |
| `questions.json` | 8 to 12 eval questions with what a good plan uses, plus one off-topic question |
| `business-context.sql` | The Guide with your answers. It is at the folder root; set its topic to `briefings/<DB>`, the topic the Flight reads |

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

First create the business-context Guide, so the first briefing reads it: run the statement in `business-context.sql` once, in the MotherDuck UI or any client connected to MotherDuck.

Then build the recipe and run it locally:

```bash
export MOTHERDUCK_TOKEN=...
uv run python build.py recipes/nyc-311                # writes recipes/nyc-311/flight/main.py and dive/index.tsx
cd recipes/nyc-311/flight
MODE=briefing REQUEST_ID=t1 uv run --with duckdb==1.5.6 python main.py
MODE=ask PROMPT="Which agencies take longest to close requests?" REQUEST_ID=t2 uv run --with duckdb==1.5.6 python main.py
cd ../../..
uv run --with duckdb==1.5.6 python eval.py recipes/nyc-311 --out baseline.json
```

To see insights continue across weeks, run several briefings with consecutive dates:

```bash
cd recipes/nyc-311/flight
for d in 2022-12-12 2022-12-19 2022-12-26; do
  AS_OF=$d MODE=briefing REQUEST_ID=w-$d uv run --with duckdb==1.5.6 python main.py
done
```

The Flight reads its settings from config keys, which arrive as environment variables:

| Key | What it does |
| --- | --- |
| `MODE` | `briefing` (default), `ask`, `investigate`, `refresh` or `feedback` |
| `PROMPT` | The question, for `ask` and `investigate` |
| `PARENT` | For `investigate`: the id of the page whose insight is being investigated |
| `BOARD` | The board the run is for. A scheduled briefing without it writes every board |
| `REQUEST_ID` | An id from the caller, so the Dive can open the page it asked for |
| `AS_OF` | The analysis date. Empty means the recipe's `AS_OF_DEFAULT` |
| `MODEL` | The `prompt()` model. Empty means the recipe's `DEFAULT_MODEL` |
| `FEEDBACK` | A JSON list of viewer events. `MODE=feedback` only records them |

### Deploy as a Flight

```bash
uv run --with duckdb==1.5.6 python deploy.py recipes/nyc-311 --dry-run   # shows what it will create or update
uv run --with duckdb==1.5.6 python deploy.py recipes/nyc-311
```

`deploy.py` creates or updates the Flight with `MD_CREATE_FLIGHT` or `MD_UPDATE_FLIGHT`, finding it by name in `MD_LIST_FLIGHTS()`. It sets the schedule from `flight.schedule` in `app.json`; pass `--no-schedule` to leave it off. It writes the Flight id into `app.json`, rebuilds, and creates the Dive or adds a version with `MD_CREATE_DIVE` or `MD_UPDATE_DIVE_CONTENT`. Then run the briefing once (`SELECT * FROM MD_RUN_FLIGHT(flight_id := '<id>')`) and open the Dive.

## Security

- **Reading:** viewers need a share of the output database. Set `requiredDatabases` in `app.json` to the share, in the shape of the default: `[{"type": "database", "path": "md:<db>", "alias": "<db>"}]`.
- **Asking and rating:** viewers need run access on the Flight: `GRANT RUN ON FLIGHT '<flight_id>' TO ROLE <role>`. Every run uses the Flight owner's credentials, so grant it only to the people the Dive is for.
- **What a viewer can reach:** the model never writes SQL. It picks measures and dimensions from `CATALOG`, and Python compiles the query. A viewer's question can therefore reach only what the marts and the catalog expose, so keep columns that viewers or the model must not see out of `MARTS`.
- **What reaches the model:** query results, viewer questions, viewer notes and the Guides are sent to `prompt()`. Viewer notes also go into the memory Guide, which AI Chat and other agents read, so treat them as untrusted text.
- **The Guide:** `business-context.sql` creates it with `access = 'user'`, so only you and the Flight read it. Widen the access when other people's AI Chat and agents should read it too.

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
- With the MotherDuck MCP server connected, `get_dive_guide` and `get_flight_guide` give the current Dive and Flight authoring rules, and `ask_docs_question` answers questions from the docs.
