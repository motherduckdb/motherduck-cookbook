SELECT
    event_id: i,
    account_id: i % 1000,
    payload: '{"kind":"' || CASE WHEN i % 3 = 0 THEN 'payment' ELSE 'view' END
        || '","amount_cents":' || (i % 10000)::VARCHAR || '}'
FROM range(200000) events(i)
