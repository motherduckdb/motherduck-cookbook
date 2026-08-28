---
title: Give Every Dive Your Brand Style With a Guide
id: dive-style-guide
description: >-
  A ready-to-edit Guide filed under the reserved `dives` topic and published
  org-wide, so every Dive your team builds carries your brand palette, number
  formatting, and layout rules instead of the MotherDuck defaults. Use when
  Dives across a team look inconsistent, or when staying on brand depends on
  someone remembering to ask.
type: example
category: analytics
features: [dives, mcp]
prompt: >-
  I want every Dive my team builds to follow our brand palette, number formatting,
  and layout rules, published once org-wide rather than restated each session. Help
  me adapt the "Give Every Dive Your Brand Style With a Guide" MotherDuck recipe to
  our conventions, using it as a guide:
  https://motherduck.com/docs/cookbook/dive-style-guide
published_date: 2026-08-27
---

# Give Every Dive Your Brand Style With a Guide

An agent building a Dive reads MotherDuck's built-in design conventions first: a default palette, KPI and chart layout rules, and a visual checklist. Those defaults are good, and they are not yours. Define your brand rules once as a Guide, publish it to your organization, and every Dive anyone builds carries them — nobody has to remember to ask.

`dive-style-guide.sql` creates that Guide. Edit the palette, formatting, and layout rules, run it to create it privately, confirm a Dive comes back on brand, then publish it org-wide.

## How it works

`dives` is a reserved topic name. A Guide filed there is appended to the Dive instructions an agent receives before it starts building, listed by title and description alongside its ID. The agent reads the ones that look relevant in full.

Two things follow from that mechanism, and they shape how the example Guide is written:

- **The description is the hook.** It's the only text an agent sees before deciding whether to open the Guide, so it states what the Guide governs rather than what it is.
- **Write only the differences.** The MotherDuck defaults are already in the agent's context. A Guide that restates them costs tokens and changes nothing. The example covers color, number and date formatting, layout, and copy — and stays quiet everywhere else.

The same mechanism works for Flights under the reserved `flights` topic, where the content is scheduling standards, naming rules, and reusable ingest patterns rather than visual conventions.

## What you'll adjust

Everything inside the `content` string in `dive-style-guide.sql`:

- **Palette.** Replace the hex values with your brand colors. Keep the distinction between a primary series color and directional positive/negative colors — an agent that treats every downward line as negative produces misleading charts.
- **Formatting.** Currency scale and precision, percentage precision, date formats for axis ticks against table cells.
- **Layout.** KPI count, how many charts belong on a page, table row caps.
- **Copy.** Whether chart titles state the measure or the finding. This one rule prevents a lot of unwanted editorializing.

Also change `title` and `description` to name your organization, and decide on `access`. The file creates a private Guide (`access = 'user'`) so you can try it before it affects anyone else.

## Questions to answer

- Do you have a documented brand palette, or are you standardizing one here for the first time? If the latter, pick five or six categorical colors that stay distinguishable next to each other.
- Should this be personal or org-wide? Start personal, confirm the output looks right, then promote it.
- Does your team already have styling rules living somewhere else — a design system doc, a BI tool theme? Copy from there rather than inventing a second source of truth.

## Run it

1. Open `dive-style-guide.sql` and edit the `content` block to match your conventions.
2. Run it in the MotherDuck UI, or through any DuckDB client attached to MotherDuck. It returns the new Guide's `id`.
3. Ask an agent to build a Dive and check that it picks up your rules.
4. When the output looks right, uncomment the `MD_SET_GUIDE_ACCESS` call at the bottom of the file, fill in the ID, and run it to publish the Guide org-wide. That step needs admin permission.

To verify the Guide is filed correctly at any point:

```sql
SELECT id, title, description, access
FROM MD_LIST_GUIDES(topic = 'dives');
```

## Caveats

- The topic must read exactly `dives`. A nested topic like `dives/style` is not the reserved topic and won't reach the Dive instructions.
- Style Guides are deliberately absent from the query-side Guide overview, so an agent preparing to write SQL never loads them. They surface through `get_dive_guide` and `list_guides` instead. If you're checking whether the Guide exists and looking at `get_query_guide`, you won't find it.
- Promoting a Guide with `MD_SET_GUIDE_ACCESS` does not transfer ownership. The creator remains the owner and the only account that can delete it. For a Guide the whole team should maintain, create it from a shared service account.
- Rules an agent can't act on get ignored. "Make it feel premium" does nothing; a hex value and a KPI count do.

## Files

| File | What it does |
|------|--------------|
| `dive-style-guide.sql` | Creates the Guide under the reserved `dives` topic, with a worked style guide to edit and commented follow-ups for publishing and verifying |

## Learn more

- [Style Dives and Flights with Guides](https://motherduck.com/docs/key-tasks/guides/style-your-dives-with-guides) — the full how-to, including the Flight equivalent
- [Using Guides to improve AI query accuracy](https://motherduck.com/docs/key-tasks/guides/) — topics, governance, references, and Guide chaining
- [Dives](https://motherduck.com/docs/key-tasks/dives/) — what Dives are and how agents build them
- MCP tools involved: `get_dive_guide` returns the Dive instructions with your `dives` Guides appended, `create_guide` and `set_guide_access` are what an agent calls when you ask it to do this conversationally instead of running the SQL
