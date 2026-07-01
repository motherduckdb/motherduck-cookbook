select
    campaign_id::integer as campaign_id,
    household_id::integer as household_id
from {{ source('retail_raw', 'raw_campaign_households') }}
where campaign_id is not null
  and household_id is not null
