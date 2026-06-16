{{ config(
    materialized='incremental',
    unique_key=['from_id', 'to_id'],
    full_refresh=false
) }}

-- prevent full refreshing a 14GB table on every run by using an incremental model

{% if is_incremental() %}
-- skip incremental runs by creating an empty table with the same schema
select * from {{ this }} where false

{% else %}

with source as (
    select *
    from {{ source('commoncrawl', 'domain_edges') }}
),

renamed as (
    select
        column0::bigint as from_id,
        column1::bigint as to_id
    from source
)

select
    from_id,
    to_id
from renamed
order by to_id

{% endif %}
