# The Learning Loop

## Contents

- What gets logged
- What the next analysis reads back
- Guides
- Viewer feedback
- Questions and viewer notes
- The analyst step and the number check
- Improving prompts and models
- Recipe settings

A malleable Dive improves while people use it. Every run, model call, insight and viewer signal is logged in the output database. The next analysis reads them back, and `eval.py` scores prompt and model changes before they ship.

## What gets logged

The Flight creates these tables in `"<DB>".main`:

| Table | One row per | Holds |
| --- | --- | --- |
| `dive_specs` | page | The spec the Dive renders |
| `gen_runs` | generation | Mode, question, parent page, data date, model, prompt version, time per step, the raw plan, the follow-up queries, the raw narrative, the number check, and what context was used |
| `llm_calls` | `prompt()` call | Step (`plan`, `probe`, `narrate`), model, prompt version, input and output size, latency, success |
| `insights` | insight on a page | Tone, title, body, investigation question, continuity, first seen, times seen |
| `feedback` | viewer event | Ratings, chart-type switches, hidden blocks, moved blocks, Investigate clicks |
| `annotations` | viewer note | Kind (`date` or `topic`), the period or dimension and value, kind of note, the note, the question it answers, whether it's active |
| `evals` | eval question | Prompt version, model, plan score, grounded share, latency |

The prompt version is a hash of every instruction and schema, so it changes whenever a prompt does.

## What the next analysis reads back

Before planning and narrating, the Flight loads:

- **The last three briefings' insights.** The narrator marks each new insight as `new`, `ongoing`, `worsening`, `improving` or `resolved`, and names the earlier insight it continues. The engine then carries `first_seen` and `times_seen` forward, so the Dive can show "Worsening since 12 Dec · 5 briefings". The narrator doesn't repeat an unchanged insight unless it's critical.
- **Ratings.** It avoids insights similar to ones viewers rated not useful.
- **Viewer preferences:** for example "viewers switched bar blocks to table 3 times".
- **Frequent questions.** They give the planner context for new questions.

## Guides

The Flight reads every Guide under the topic `briefings/<DB>`, plus each topic in `GUIDE_TOPICS`. It uses `MD_LIST_GUIDES` and `MD_GET_GUIDE`, up to about 9,000 characters.

- **Business context:** written during the recipe interview. It holds the audience, the decisions they make, targets, known patterns and definitions. The narrator cites a target when the data crosses it.
- **Memory:** after each briefing, the Flight writes "What the `<DB>` briefing has learned". It holds the current insights with their history, earlier insights it no longer repeats, frequent questions and viewer preferences. It uses `MD_CREATE_GUIDE` the first time and `MD_UPDATE_GUIDE` after that, so every version is kept.

Both are ordinary Guides, so the effects reach beyond the Flight:
- **People:** editing a Guide changes the next analysis.
- **Other agents:** the Dive's AI Chat and other agents can read the same context.
- **Optional:** if Guides are unavailable, the run still publishes.

## Viewer feedback

A Dive can't write to a database, so viewer actions go through the Flight:

- **Ride-along events:** the Dive queues chart-type switches, hidden and moved blocks, and Investigate clicks. The next generation it starts carries them in the `FEEDBACK` config key.
- **Ratings:** thumbs up and down on an insight or a page send straight away, with a `MODE=feedback` run that only records events.
- **Validation:** the Flight checks each event: a known event type, a spec id format, and length limits.
- **Learned defaults:** when viewers switch a block type to the same chart type 3 or more times, the Dive uses that type as the default for every block of that type. A viewer's own choice still wins.

The Dive's **Memory** page reads these tables directly. It shows the insight timeline, frequent questions, viewer actions, and every prompt version with its speed, grounded share, eval scores and ratings.

## Questions and viewer notes

The briefing asks its viewers about what the data can't explain, and remembers the answers as notes.

- **Asking:** the narrator returns up to `VIEWER_QUESTIONS` (2) questions when the data shows what moved but not why in business terms. A question is about a date (a spike in one period of a time chart) or a topic (one value of a dimension, such as a referrer, page or borough). Each offers 2 to 4 likely explanations, such as "Partner referral" or "Bot traffic".
- **Checking:** the engine keeps a question only when its `x` is in the block's data and its dimension is in the catalog. It drops questions an earlier note already answers, and questions asked in the last three briefings.
- **Answering:** the page shows the questions under the insights. A viewer picks an explanation or writes one, chooses a kind (event, release, campaign, data issue, expected, not important, or other when it is unclear what happened) and answers. The answer goes to the Flight as an `annotate` event in a `MODE=feedback` run, and the Dive shows it at once while the run saves it.
- **Unprompted notes:** the pin on any time chart or category chart adds a note to one of its periods or values.
- **Where notes show:**
  - **Charts:** every block that shows that date or value shows the note, as a marker on line and area charts and as a line under the block.
  - **The narrator:** the next analysis reads every active note. It uses a note as the explanation when the data moves there, treats a period noted as a data issue as unreliable, and doesn't report what viewers noted as not important unless it grows.
  - **The memory Guide:** it lists the notes, so AI Chat and other agents see them too.
  - **The Memory page:** it lists them with a Remove button, which sends an `unannotate` event.

## The analyst step and the number check

- **Analyst step:** after compiling the page, one `prompt()` call proposes up to `ANALYST_PROBES` follow-up queries, such as which segment drove a change. Each is a hypothesis built from catalog ids. Python compiles and runs them like any block. The narrator uses the results as evidence and may put two of them on the page, marked as follow-up queries.
- **Number check:** every number in the headline, summary, insights, captions and block notes must match a value, difference or change in the data, the follow-up queries, the Guides or earlier insights. Windows like "28 days" and small counts are skipped. If less than 85% match, the narrator retries once with the unmatched numbers named, and the better draft wins. The page shows the result, for example "45/45 numbers checked".

## Improving prompts and models

1. **Write questions.** Put 8 to 12 questions, with what a good plan uses, in `recipes/<name>/questions.json`. Seed them from the recipe interview and from `gen_runs` once people ask questions.
2. **Run evals.** `python3 eval.py recipes/<name>` scores the plan only, at about 6 seconds a question. `--narrate` runs the full generation and also scores the grounded share. Write a baseline with `--out`, then pass `--baseline` after a change to see each question that moved.
3. **Change the cheapest thing first:**
   - catalog descriptions and planner hints;
   - Guide content;
   - prompt wording in `template/engine.py`;
   - `--model` to compare `prompt()` models.
4. **Ship the change** when the eval holds and ratings on the Memory page don't drop for the new prompt version.

## Recipe settings

| Setting | Default | Effect |
| --- | --- | --- |
| `GUIDE_TOPICS` | `[]` | More Guide topics to read, for example organization metric definitions |
| `MEMORY_GUIDE` | `True` | Write the memory Guide after each briefing |
| `ANALYST_PROBES` | `3` | Follow-up queries before narrating; `0` turns the step off |
| `DEFAULT_MODEL` | `gpt-6-luna` | The `prompt()` model when the Flight's `MODEL` config is empty. Check current model names in the `prompt()` docs. |
| `VIEWER_QUESTIONS` | `2` | Questions the narrator may ask viewers per page; `0` turns them off |
