-- Create a Dive style Guide.
--
-- Guides filed under the reserved `dives` topic extend the built-in Dive
-- instructions an agent reads before it builds one. Everything you leave out
-- keeps MotherDuck's default styling, so write only the rules that differ.
--
-- Edit the palette, formatting, and layout rules below, then run this in the
-- MotherDuck UI or any DuckDB client attached to MotherDuck.

SELECT id, topic, title, access, current_version
FROM MD_CREATE_GUIDE(
  topic = 'dives',
  title = 'Acme Dive style',
  description = 'Brand palette, currency and date formatting, and layout rules that override the MotherDuck defaults for every Dive we build',
  content = '
# Acme Dive style

These rules replace the corresponding MotherDuck defaults. Anything not listed
here follows the built-in design guidance.

## Color

- Primary series: #1d4ed8
- Positive: #15803d
- Negative: #b91c1c
- Categorical series, in order: #1d4ed8, #b45309, #15803d, #7e22ce, #0e7490
- Leave the page background at the default

Use the primary color for a single series. Reserve positive and negative for
values whose direction carries meaning, not for every line that moved.

## Number and date formatting

- Currency in thousands with no decimal places: $482K
- Percentages to one decimal place, always with the % sign
- Counts with thousands separators: 1,284
- Axis tick dates as MMM YYYY; full dates in tables as YYYY-MM-DD

## Layout

- Put the reporting period in the page subtitle, never in a chart title
- Three KPIs across the top, not four
- One primary chart per Dive; add a second only when it answers a different
  question
- Tables cap at seven rows unless browsing detail is the point of the Dive

## Copy

- Chart titles state the measure, not the finding: "Weekly active accounts",
  not "Strong growth in active accounts"
- Every axis label carries its unit
',
  access = 'user'
);

-- Publish it to the whole organization once you are happy with it.
-- Requires admin permission. Promotion does not change the Guide owner.
--
--   SELECT access
--   FROM MD_SET_GUIDE_ACCESS(
--     id = '<guide_id_returned_above>',
--     access = 'organization'
--   );

-- Confirm it landed under the reserved topic.
--
--   SELECT id, title, description, access
--   FROM MD_LIST_GUIDES(topic = 'dives');
