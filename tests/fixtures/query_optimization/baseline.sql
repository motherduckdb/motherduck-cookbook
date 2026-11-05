-- Baseline test query: repeated JSON parsing.
WITH parsed AS MATERIALIZED (
    SELECT
        *,
        event_kind: json_extract_string(payload, '$.kind'),
        amount_cents: json_extract_string(payload, '$.amount_cents')::BIGINT
    FROM demo_events
)
SELECT
    account_id,
    total_cents: sum(amount_cents),
    event_count: count(*)
FROM parsed
WHERE event_kind = 'payment'
GROUP BY account_id
