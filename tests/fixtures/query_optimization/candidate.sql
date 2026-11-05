-- Equivalent candidate using typed test columns.
SELECT
    account_id,
    total_cents: sum(amount_cents),
    event_count: count(*)
FROM demo_typed_events
WHERE event_kind = 'payment'
GROUP BY account_id
