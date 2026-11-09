-- The Guide the recipe interview produces. The briefing Flight reads every Guide under
-- the topic 'briefings/<db>' before each analysis, so editing this changes the next briefing.
-- Replace the contents with your own answers; keep access = 'user' until you want it org-wide.
SELECT id, current_version
FROM MD_CREATE_GUIDE(
    topic = 'briefings/nyc_311_briefing',
    title = 'NYC 311 briefing: business context',
    description = 'Audience, decisions, targets and known seasonality for the NYC 311 briefing',
    access = 'user',
    content = '# NYC 311 briefing: business context

## Who reads it and what they decide
- City operations analysts, every Monday. They decide which agencies need extra staff or follow-up that week.
- A good briefing names the one or two complaint types or agencies that need action, and says whether last week''s issues got better.

## Targets
- HPD heat and hot water complaints: close within 3 days during heating season (1 October to 31 May).
- NYPD noise and parking complaints: close within 1 day.
- Share of requests closed after 7 or more days: keep below 15%.
- Share of requests reported online or in the app: grow toward 70%.

## Known patterns
- Heat and hot water complaints rise every heating season and peak in the coldest weeks.
- Noise complaints peak in summer and on weekend nights.
- Holidays (Thanksgiving, Christmas, New Year) lower volume for most complaint types.

## Definitions
- "Days to close" counts only requests that have closed.
- Requests with no borough are "Unspecified" and are not a data error.
'
);
