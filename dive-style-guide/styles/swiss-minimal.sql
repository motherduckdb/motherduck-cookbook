-- Swiss minimal: white field, black ink, one red accent.
--
-- Rendered example: ../assets/swiss-minimal.png
--
-- Edit the palette and the title, run it to create the Guide privately, then
-- promote it with MD_SET_GUIDE_ACCESS once a Dive comes back the way you want.

SELECT id, topic, title, access, current_version
FROM MD_CREATE_GUIDE(
  topic = 'dives',
  title = 'Swiss minimal Dive style',
  description = 'Dive styling for a white, rule-based, single-accent look: no cards, no gridlines, uppercase micro-labels, three KPIs',
  content = '
# Swiss minimal

These rules replace the corresponding MotherDuck defaults. Anything not listed
here follows the built-in design guidance.

## Color

- Page background: #ffffff
- Text: #111111
- Muted text and all labels: #757575
- Hairline rules: #d4d4d4
- Accent, one only: #d8232a

Every series, bar, and highlight uses the accent. No second hue, no positive
and negative pair — direction is read from the axis, not from color.

## Typography

- Everything in `font-sans`.
- Page title: `text-3xl font-bold tracking-tight`, sentence case.
- Section labels and table headers: `text-xs uppercase tracking-widest`, muted.
- KPI values: `text-4xl font-normal tabular-nums` — size carries the emphasis,
  not weight.
- Recharts tick labels do not inherit Tailwind font classes. Set them per axis:
  `tick={{ fontSize: 10, fill: "#757575" }}`.

## Layout

- A 4 px `#111111` rule above the page title, and the reporting period on the
  same line at the right. Never in a chart title.
- Three KPIs in `grid grid-cols-3`, label above value, separated by 1 px
  vertical hairlines rather than cards.
- Horizontal hairlines separate sections. No rounded corners, no shadows, no
  fills.

## Number and date formatting

- Currency in millions to one decimal: $71.8M
- Percentages to one decimal, always with the sign: 18.8%
- Counts in millions to two decimals: 3.25M
- Axis ticks are bare numbers; the unit belongs in the section label, as in
  "REVENUE PER DAY — USD MILLIONS"
- Dates on an axis as the day of month only when the period is already stated

## Charts

- One 1.5 px accent line, `dot={false}`.
- No gridlines. No y axis line — ticks only. The x axis line is 1 px #111111.
- Roughly 170 px tall.

## Tables

- Four rows.
- A 1 px #111111 rule under the header, a #d4d4d4 hairline between rows.
- Figures right-aligned and `tabular-nums`.

## Copy

- Titles state the measure, never the finding: "Revenue per day", not
  "Revenue holds up through November".
- No adjectives about the data anywhere.
',
  access = 'user'
);

-- Publish org-wide once a Dive comes back the way you want. Needs admin
-- permission; promotion does not change the Guide owner.
--
--   SELECT access
--   FROM MD_SET_GUIDE_ACCESS(
--     id = '<guide_id_returned_above>',
--     access = 'organization'
--   );
