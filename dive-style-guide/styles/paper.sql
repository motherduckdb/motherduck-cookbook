-- Paper: cream stock, serif type, clay accent, as little ink as the point needs.
--
-- Rendered example: ../assets/paper.png
--
-- Edit the palette and the title, run it to create the Guide privately, then
-- promote it with MD_SET_GUIDE_ACCESS once a Dive comes back the way you want.

SELECT id, topic, title, access, current_version
FROM MD_CREATE_GUIDE(
  topic = 'dives',
  title = 'Paper Dive style',
  description = 'Dive styling for a printed-page look: cream background, serif throughout, clay accent, no gridlines, observations in a marginal note rather than the title',
  content = '
# Paper

These rules replace the corresponding MotherDuck defaults. Anything not listed
here follows the built-in design guidance.

## Color

- Page background: #faf9f5
- Text: #191919
- Muted text and labels: #6b6658
- Hairline rules: #ddd8c9
- Accent: #c96442

One accent for the series. Reserve any second color for a value whose direction
genuinely carries meaning.

## Typography

- Everything in `font-serif`, including axis ticks.
- Recharts renders SVG text that ignores Tailwind font classes, so set the
  family per axis: `tick={{ fontSize: 11, fill: "#6b6658", fontFamily: "serif" }}`.
- Page title `text-3xl`, subtitle `text-base italic` and muted.
- KPI values `text-4xl tabular-nums`, labels `text-sm` lowercase and muted.
- No bold anywhere. Emphasis comes from size and whitespace.

## Layout

- Outer padding `p-8` — generous margins are part of the look.
- Three KPIs in a `flex gap-16` row, not a grid of cards.
- No borders, no fills, no shadows. Sections separate by space, and tables by a
  single hairline.

## Number and date formatting

- Currency in millions to one decimal: $71.8M
- Percentages to one decimal: 18.8%
- Counts in millions to two decimals: 3.25M
- Axis ticks are bare numbers; the unit goes in the chart title, as in
  "Revenue per day, USD millions"

## Charts

- One 1.5 px accent line, `dot={false}`, roughly 160 px tall.
- No gridlines, no axis lines. Three y ticks is enough.
- Add a dashed mean or benchmark line in the muted color with an inline label,
  so a reader can see which points are unusual without a legend.
- The y axis starts at zero. If a range is genuinely too tight to read, say so
  in the note rather than truncating the axis.

## Tables

- Four rows.
- Italic muted headers, one hairline under the header and one under the body.
  No vertical rules, no zebra striping.

## Copy

- Chart titles state the measure. The observation goes in one `text-sm italic`
  muted note directly under the chart: "The low point is Thanksgiving,
  24 November."
- One note per chart, and only when it names something a reader would otherwise
  have to work out.
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
