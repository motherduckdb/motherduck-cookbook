SELECT
    event_id,
    account_id,
    event_kind: json_extract_string(payload, '$.kind'),
    amount_cents: json_extract_string(payload, '$.amount_cents')::BIGINT
FROM {{ ref('raw_events') }}
