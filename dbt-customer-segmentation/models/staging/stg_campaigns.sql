select
    campaign_id::integer as campaign_id,
    campaign_name::varchar as campaign_name,
    campaign_type::varchar as campaign_type,
    start_day::integer as start_day,
    end_day::integer as end_day
from {{ source('retail_raw', 'raw_campaigns') }}
where campaign_id is not null
