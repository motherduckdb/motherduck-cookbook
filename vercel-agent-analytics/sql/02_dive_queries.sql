-- Starter queries for a MotherDuck Dive. Each block is one tile.
-- Run against the same destination configured on the collector:
--   MD_DESTINATION=agent_analytics.raw
--   MD_TABLE=vercel_request_logs
-- If you changed either value, replace the database/schema/table identifiers
-- in this file before saving it as a Dive.

USE agent_analytics;

---------------------------------------------------------------------
-- Live counter: AI requests in the last 5 minutes
---------------------------------------------------------------------
SELECT COUNT(*) AS ai_requests_5m
FROM raw.vercel_request_logs
WHERE event_ts >= now() - INTERVAL 5 MINUTE
  AND ai_category IS NOT NULL;

---------------------------------------------------------------------
-- Requests per minute, last 60 min, by category
---------------------------------------------------------------------
SELECT
    date_trunc('minute', event_ts) AS minute,
    COALESCE(ai_category, 'human') AS category,
    COUNT(*) AS requests
FROM raw.vercel_request_logs
WHERE event_ts >= now() - INTERVAL 60 MINUTE
GROUP BY ALL
ORDER BY minute, category;

---------------------------------------------------------------------
-- Top 20 AI agents, last 24 hours
---------------------------------------------------------------------
SELECT
    ai_name,
    ai_category,
    COUNT(*) AS requests,
    SUM(CASE WHEN status_code BETWEEN 400 AND 499 THEN 1 ELSE 0 END) AS errors_4xx,
    SUM(CASE WHEN status_code BETWEEN 500 AND 599 THEN 1 ELSE 0 END) AS errors_5xx
FROM raw.vercel_request_logs
WHERE event_ts >= now() - INTERVAL 24 HOUR
  AND ai_name IS NOT NULL
GROUP BY ALL
ORDER BY requests DESC
LIMIT 20;

---------------------------------------------------------------------
-- Humans arriving via AI tools (referer match), last 24h
---------------------------------------------------------------------
SELECT
    ai_name AS ai_source,
    host,
    COUNT(*) AS sessions_estimate
FROM raw.vercel_request_logs
WHERE event_ts >= now() - INTERVAL 24 HOUR
  AND ai_category = 'human_via_ai'
GROUP BY ALL
ORDER BY sessions_estimate DESC;

---------------------------------------------------------------------
-- 404s hit by crawlers: content they looked for and did not find
---------------------------------------------------------------------
SELECT
    path,
    ai_name,
    COUNT(*) AS misses
FROM raw.vercel_request_logs
WHERE event_ts >= now() - INTERVAL 7 DAY
  AND ai_category = 'crawler'
  AND status_code = 404
GROUP BY ALL
ORDER BY misses DESC
LIMIT 50;

---------------------------------------------------------------------
-- Share of traffic that is AI, per day, last 30 days
---------------------------------------------------------------------
SELECT
    date_trunc('day', event_ts) AS day,
    SUM(CASE WHEN ai_category IS NOT NULL THEN 1 ELSE 0 END) * 1.0
        / NULLIF(COUNT(*), 0) AS ai_share,
    COUNT(*) AS total_requests
FROM raw.vercel_request_logs
WHERE event_ts >= now() - INTERVAL 30 DAY
GROUP BY ALL
ORDER BY day;

---------------------------------------------------------------------
-- Top pages by AI category, last 24h
---------------------------------------------------------------------
SELECT
    path,
    SUM(CASE WHEN ai_category = 'crawler'      THEN 1 ELSE 0 END) AS crawler,
    SUM(CASE WHEN ai_category = 'agent'        THEN 1 ELSE 0 END) AS agent,
    SUM(CASE WHEN ai_category = 'human_via_ai' THEN 1 ELSE 0 END) AS human_via_ai
FROM raw.vercel_request_logs
WHERE event_ts >= now() - INTERVAL 24 HOUR
  AND ai_category IS NOT NULL
GROUP BY ALL
ORDER BY (crawler + agent + human_via_ai) DESC
LIMIT 25;
