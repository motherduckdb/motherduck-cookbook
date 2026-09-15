-- Terminal: mission-control console — deep indigo, monospace, periwinkle
-- values, violet section headers, amber warnings.
--
-- Rendered example: ../assets/terminal.png
--
-- Edit the palette and the title, run it to create the Guide privately, then
-- promote it with MD_SET_GUIDE_ACCESS once a Dive comes back the way you want.

SELECT id, topic, title, access, current_version
FROM MD_CREATE_GUIDE(
  topic = 'dives',
  title = 'Terminal Dive style',
  description = 'Dive styling for dark operational dashboards: deep indigo background, monospace throughout, bordered panels, violet bracketed snake_case labels, periwinkle values with amber warnings, status line',
  content = '
# Terminal

These rules replace the corresponding MotherDuck defaults. Anything not listed
here follows the built-in design guidance. Use this style for dashboards people
watch during an incident, not for anything a customer reads.

## Color

- Page background: #0a0e23
- Panel background: #141936
- Borders and gridlines: #262c52
- Text: #d6dcf5
- Dim text and labels: #8590c4
- Values and series, nominal: #a5b4fc
- Section headers: #c084fc
- Warnings and anomalies: #ffb454

The page runs cool: indigo ground, periwinkle data, violet structure. Amber is
the only warm color and it means something is wrong.

Never go dimmer than #8590c4 on this background — anything paler fails contrast
at small sizes.

## Typography

- Everything in `font-mono`, including axis ticks:
  `tick={{ fontSize: 10, fill: "#8590c4", fontFamily: "monospace" }}`.
- Section headers: `text-xs uppercase` in brackets and in violet,
  `[ revenue_per_day ]`.
- Labels are snake_case identifiers, not prose: `avg_fare`, not "Average fare".
- KPI values `text-2xl tabular-nums` in the nominal color.

## Layout

- This is the one style that wants chrome: KPIs and charts each sit in a panel
  with `background: #141936` and a 1 px #262c52 border.
- Four KPIs in `grid grid-cols-4 gap-3`, label above value.
- A status line closes the page, `text-xs` dim: source table, row count, and
  state — "src sample_data.nyc.taxi · rows 3,252,620 · ok".

## Number and date formatting

- Currency in millions to two decimals: $71.79M
- Percentages to one decimal: 18.8%
- Counts with thousands separators in the status line, in millions to two
  decimals in a KPI
- Dates as ISO: 2022-11-01, and ranges as "2022-11-01 → 2022-11-30"
- Every numeric column right-aligned and `tabular-nums`

## Charts

- One 1.5 px line in the nominal color, `dot={false}`, roughly 170 px tall.
- Dotted grid: `strokeDasharray="1 3"` in #262c52. Axis lines in the same
  color — never in violet, which reads as a series at the baseline.
- Call out the extreme under the chart in the warning color, prefixed with `!`:
  "! min 11-24 $1.44M". One line, and only when a value is worth acting on.

## Tables

- Five rows. Row borders in #262c52, no fills, no striping.
- Category values lowercased and snake_cased to match the labels.

## Copy

- No sentences. Identifiers, values, units.
- Titles state the measure. Anything resembling interpretation goes in the `!`
  line or nowhere.
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
