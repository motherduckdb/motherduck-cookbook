-- Editorial: Financial Times conventions — pink stock, oxford blue bars with a
-- claret highlight, serif headline that states the finding, sans dek, source
-- line.
--
-- Rendered example: ../assets/editorial.png
--
-- Edit the palette and the title, run it to create the Guide privately, then
-- promote it with MD_SET_GUIDE_ACCESS once a Dive comes back the way you want.

SELECT id, topic, title, access, current_version
FROM MD_CREATE_GUIDE(
  topic = 'dives',
  title = 'Editorial Dive style',
  description = 'Dive styling for charts that read like an FT news graphic: pink paper, oxford blue bars with one claret highlight, serif headline stating the finding, sans dek with units, scale on the right, source line',
  content = '
# Editorial

These rules replace the corresponding MotherDuck defaults. Anything not listed
here follows the built-in design guidance.

## Color

- Page background: #fff1e5
- Text: #33302e
- Muted text and labels: #66605c
- Hairline rules: #cec6b4
- Primary series and KPI figures: #0f5499
- Highlight and marker rule: #990f3d

Two hues, with distinct jobs. Oxford blue carries every series by default.
Claret marks the page and the one data point the headline is about — never a
second series, never a whole chart.

## Typography

- Headline: `font-serif text-3xl font-bold leading-tight`.
- Everything else in `font-sans`: dek `text-sm` muted, section titles
  `text-sm font-semibold`, labels `text-xs`.
- KPI figures: `text-3xl font-semibold tabular-nums` in oxford blue.
- Recharts ticks: `tick={{ fontSize: 10, fill: "#66605c" }}`.

## Layout

- A 44 x 5 px claret block above the headline. It is the page marker; nothing
  else uses that shape.
- Headline, then dek, then a hairline, then four KPIs in
  `grid grid-cols-4 gap-6`.
- A source line at the bottom, `text-xs` muted above a hairline:
  "Source: <fully qualified table>". Every page carries one.

## Number and date formatting

- Currency in millions to one decimal: $71.8M
- Percentages to one decimal: 18.8%
- Counts in millions to two decimals: 3.25M
- The dek carries place, period, and unit: "New York City yellow taxi revenue,
  November 2022, USD millions"

## Charts

- Bars, solid oxford blue fill, roughly 170 px tall. Use a line only for a
  series long enough that bars would not resolve.
- When the headline names one point, fill that bar claret with a Recharts
  `<Cell>` and say so in the sub-label: "Thanksgiving in claret". One
  highlighted bar per chart, and never without the label.
- Horizontal gridlines only (`vertical={false}`) in the hairline color.
- Put the y scale on the right (`orientation="right"`). The x axis line is
  1 px in the text color.
- The unit goes in the dek, and the sub-label under a chart title is free for
  the highlight note.

## Tables

- Three rows. A table here supports the headline; browsing belongs elsewhere.

## Copy

- The headline states the finding and names the comparison it rests on:
  "Thanksgiving costs taxis a third of a Thursday", not "Revenue dips late in
  the month" and not "Daily revenue".
- Only claim what the query shows. Never an adjective without a number behind
  it.
- Chart and table titles stay descriptive; the finding lives in the headline.
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
