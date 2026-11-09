"""Recipe: New York City 311 service requests, from MotherDuck's public sample_data share.

A static dataset exported on 2023-03-12. The marts stop two months earlier, on 2023-01-09,
so every request in the window has had time to close and closing times are not cut short.
The briefing compares the last 28 days with the same 28 days a year earlier. Copy this file to start a recipe for
your own data: replace the sources, marts, catalog, watch list and briefing plan.
"""

DB = "nyc_311_briefing"
ATTACH = [
    ("sample_data", "md:_share/sample_data/23b0d623-1361-421d-ae77-62d701d471e6"),
]

MARTS_REFRESH = "missing"  # static data: build once
ANCHOR = ("mart_requests", "day")
AS_OF_DEFAULT = "max"
COMPARES = ["none", "previous_period", "previous_year"]
DEFAULT_COMPARE = "previous_year"
BASELINE_WEEKS = 26

DOMAIN_DESCRIPTION = "New York City's 311 service requests: complaints and requests residents report, and how fast city agencies close them"
AUDIENCE = "city operations analyst"
BRIEFING_QUESTION = "311 briefing: how did the last 28 days compare with the same 28 days a year earlier, and what needs attention?"
DOMAIN_CONTEXT = (
    "311 is New York City's non-emergency service line. Each request has a complaint type, a more specific descriptor, "
    "the agency that handles it (NYPD for parking and noise, HPD for housing conditions such as heat and hot water, "
    "DSNY for sanitation, DOT for streets), the borough, and the channel it came in by (phone, online, mobile app). "
    "Heat and hot water complaints rise every winter. The data ends on 2023-01-09, two months before it was exported, "
    "so closing times in the window are complete."
)
PLANNER_HINTS = """- Use descriptor to explain a complaint type's change: it is the detail behind the type.
- Use requests.avg_days_to_close and requests.slow_share for agency performance; compare agencies with a table.
- Use channel for questions about how residents report (phone, online, mobile)."""

# ---- Learning ------------------------------------------------------------------------
# The Flight reads every Guide under topic "briefings/<DB>" (the business context from the
# recipe interview, and the memory Guide it writes after each briefing) plus GUIDE_TOPICS.
GUIDE_TOPICS = []          # e.g. ["core-metrics"] for organization metric definitions
MEMORY_GUIDE = True        # keep "What the <DB> briefing has learned" up to date
ANALYST_PROBES = 3         # follow-up queries the analyst step may run before narrating; 0 turns it off
DEFAULT_MODEL = "gpt-6-luna"

ACRONYMS = {"nypd": "NYPD", "hpd": "HPD", "dsny": "DSNY", "dot": "DOT", "dep": "DEP"}
LABELS = {
    "requests": "Requests",
    "avg_days_to_close": "Days to close",
    "slow_share": "Closed after 7+ days",
    "online_share": "Reported online or in the app",
    "closed_share": "Closed so far",
}

# ---- Marts --------------------------------------------------------------------------

MARTS = {
    "mart_requests": """
        SELECT created_date::DATE AS day,
               complaint_type,
               coalesce(descriptor, '(none)') AS descriptor,
               agency,
               CASE borough WHEN 'BROOKLYN' THEN 'Brooklyn' WHEN 'QUEENS' THEN 'Queens' WHEN 'BRONX' THEN 'Bronx' WHEN 'MANHATTAN' THEN 'Manhattan' WHEN 'STATEN ISLAND' THEN 'Staten Island' ELSE 'Unspecified' END AS borough,
               CASE open_data_channel_type WHEN 'ONLINE' THEN 'online' WHEN 'MOBILE' THEN 'mobile app' WHEN 'PHONE' THEN 'phone' ELSE 'other' END AS channel,
               count(*) AS requests,
               count(*) FILTER (WHERE closed_date >= created_date) AS closed,
               sum(date_diff('hour', created_date, closed_date)) FILTER (WHERE closed_date >= created_date) AS resolution_hours,
               count(*) FILTER (WHERE closed_date >= created_date + INTERVAL 7 DAY) AS slow
        FROM "sample_data"."nyc"."service_requests"
        WHERE created_date >= DATE '2021-01-01' AND created_date < DATE '2023-01-10'
        GROUP BY ALL""",
}

# ---- Catalog --------------------------------------------------------------------------
# measure: (sql aggregate, format, description[, "down" when lower is better])

CATALOG = {
    "requests": {
        "label": "311",
        "table": "mart_requests",
        "time": "day",
        "about": "311 service requests per day, by complaint type, descriptor, agency, borough and channel, with how fast they were closed.",
        "dims": {
            "complaint_type": ("complaint_type", "What the request is about: Illegal Parking, Noise - Residential, HEAT/HOT WATER, Blocked Driveway, Street Condition, ..."),
            "descriptor": ("descriptor", "The detail behind the complaint type, e.g. Loud Music/Party, ENTIRE BUILDING, Blocked Hydrant"),
            "agency": ("agency", "City agency that handles it: NYPD, HPD, DSNY, DOT, DEP, DPR, DOB, DOHMH"),
            "borough": ("borough", "Brooklyn, Queens, Bronx, Manhattan, Staten Island or Unspecified"),
            "channel": ("channel", "How it was reported: online, phone, mobile app, other"),
        },
        "measures": {
            "requests": ("sum(requests)", "count", "Service requests"),
            "closed_share": ("sum(closed) / nullif(sum(requests), 0)", "pct", "Share of requests closed so far"),
            "avg_days_to_close": ("sum(resolution_hours) / nullif(sum(closed), 0) / 24", "number", "Average days from request to closing, for closed requests", "down"),
            "slow_share": ("sum(slow) / nullif(sum(closed), 0)", "pct", "Share of closed requests that took 7 days or more", "down"),
            "online_share": ("sum(requests) FILTER (WHERE channel IN ('online', 'mobile app')) / nullif(sum(requests), 0)", "pct", "Share of requests reported online or in the mobile app"),
        },
    },
}

WATCH = [
    ("requests.requests", "Requests"),
    ("requests.avg_days_to_close", "Days to close"),
    ("requests.slow_share", "Closed after 7+ days"),
    ("requests.online_share", "Reported online or in the app"),
]

BRIEFING_PLAN = {
    "title": "311 briefing",
    "subtitle": "Last 28 days against the same 28 days a year earlier",
    "blocks": [
        {"type": "kpi", "title": "Headline numbers", "measures": ["requests.requests", "requests.avg_days_to_close", "requests.slow_share", "requests.online_share"], "window": "last_28_days", "compare": "previous_year", "span": 12, "why": "Volume, speed and channel at a glance."},
        {"type": "line", "title": "Weekly requests by borough", "measures": ["requests.requests"], "series_dimension": "borough", "window": "last_12_months", "time_grain": "week", "span": 8, "why": "Seasonality and where demand is shifting."},
        {"type": "bar", "title": "Top complaint types (requests)", "measures": ["requests.requests"], "dimension": "complaint_type", "window": "last_28_days", "compare": "previous_year", "limit": 10, "span": 4, "why": "What residents report most, against a year ago."},
        {"type": "heatmap", "title": "Daily requests", "measures": ["requests.requests"], "window": "last_6_months", "span": 12, "why": "Weekly rhythm and unusual days."},
        {"type": "treemap", "title": "Requests by agency and complaint type", "measures": ["requests.requests"], "dimension": "complaint_type", "series_dimension": "agency", "window": "last_28_days", "span": 6, "why": "Which agencies carry the load, and for what."},
        {"type": "sankey", "title": "How requests reach each agency", "measures": ["requests.requests"], "dimension": "channel", "series_dimension": "agency", "window": "last_28_days", "span": 6, "why": "Channel mix per agency."},
        {"type": "table", "title": "Agency performance", "measures": ["requests.requests", "requests.avg_days_to_close", "requests.slow_share"], "dimension": "agency", "window": "last_28_days", "compare": "previous_year", "limit": 10, "span": 12, "why": "Volume and speed per agency."},
    ],
}

# ---- Boards ----------------------------------------------------------------------
# Two dashboards on the same data and Flight. "operations" is the briefing above.
# "leadership" is a monthly, high-level view: the last complete month against the same
# month a year earlier. The 06:00 run writes it once per month.
BOARDS = {
    "operations": {"LABEL": "Operations"},
    "leadership": {
        "LABEL": "Leadership",
        "CADENCE": "monthly",
        "AUDIENCE": "city leadership (deputy mayor for operations and agency commissioners), reading once a month",
        "BRIEFING_QUESTION": "Monthly 311 leadership briefing: how did last month compare with the same month a year earlier, which agencies and boroughs need attention, and is service getting faster?",
        "DEFAULT_COMPARE": "previous_year",
        "WATCH": [("requests.requests", "Requests"), ("requests.avg_days_to_close", "Days to close"), ("requests.slow_share", "Closed after 7+ days")],
        "PLANNER_HINTS": PLANNER_HINTS + """
- This is a monthly leadership view: use window last_full_month for totals and comparisons, and last_12_full_months with time_grain month for trends.
- Stay high level: citywide totals, agencies and boroughs. Use complaint types only to explain an agency's change.""",
        "BRIEFING_PLAN": {
            "title": "Monthly 311 leadership briefing",
            "subtitle": "Last month against the same month a year earlier",
            "blocks": [
                {"type": "kpi", "title": "The month at a glance", "measures": ["requests.requests", "requests.avg_days_to_close", "requests.slow_share", "requests.online_share"], "window": "last_full_month", "compare": "previous_year", "span": 12, "why": "Demand, speed and how residents reach the city, against the same month last year."},
                {"type": "line", "title": "Monthly requests by borough", "measures": ["requests.requests"], "series_dimension": "borough", "window": "last_12_full_months", "time_grain": "month", "span": 8, "why": "The year's shape: seasonality and where demand is growing."},
                {"type": "bar", "title": "Requests by agency (month)", "measures": ["requests.requests"], "dimension": "agency", "window": "last_full_month", "compare": "previous_year", "limit": 8, "span": 4, "why": "Which agencies carry the load this month."},
                {"type": "line", "title": "Monthly days to close", "measures": ["requests.avg_days_to_close", "requests.slow_share"], "window": "last_12_full_months", "time_grain": "month", "span": 6, "why": "Is service getting faster over the year?"},
                {"type": "bar", "title": "Days to close by borough (month)", "measures": ["requests.avg_days_to_close"], "dimension": "borough", "window": "last_full_month", "compare": "previous_year", "limit": 6, "span": 6, "why": "Where residents wait longest."},
                {"type": "table", "title": "Agency scorecard", "measures": ["requests.requests", "requests.avg_days_to_close", "requests.slow_share"], "dimension": "agency", "window": "last_full_month", "compare": "previous_year", "limit": 10, "span": 12, "why": "Volume and speed per agency for the month, against a year ago."},
            ],
        },
    },
}
