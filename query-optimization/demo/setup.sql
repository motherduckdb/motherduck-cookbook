-- Synthetic data only. Execute in a disposable local database or experiment clone.
CREATE TABLE demo_events AS
SELECT
    event_id: i,
    account_id: i % 1000,
    payload: '{"kind":"' || CASE WHEN i % 3 = 0 THEN 'payment' ELSE 'view' END
        || '","amount_cents":' || (i % 10000)::VARCHAR || '}'
FROM range(200000) events(i);

-- Pay extraction once in staging; recurring consumers use typed columns.
CREATE TABLE demo_typed_events AS
SELECT
    event_id,
    account_id,
    event_kind: json_extract_string(payload, '$.kind'),
    amount_cents: json_extract_string(payload, '$.amount_cents')::BIGINT
FROM demo_events;
