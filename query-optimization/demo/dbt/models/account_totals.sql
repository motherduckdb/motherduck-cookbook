SELECT
    account_id,
    total_cents: sum(amount_cents),
    event_count: count(*)
FROM {{ ref('stg_events') }}
WHERE event_kind = 'payment'
GROUP BY account_id
