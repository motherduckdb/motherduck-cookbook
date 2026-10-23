WITH expected AS (
    SELECT
        account_id,
        total_cents: sum(json_extract_string(payload, '$.amount_cents')::BIGINT),
        event_count: count(*)
    FROM {{ ref('raw_events') }}
    WHERE json_extract_string(payload, '$.kind') = 'payment'
    GROUP BY account_id
)
(SELECT * FROM expected EXCEPT ALL SELECT * FROM {{ ref('account_totals') }})
UNION ALL
(SELECT * FROM {{ ref('account_totals') }} EXCEPT ALL SELECT * FROM expected)
