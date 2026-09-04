select
    redemption_id::integer as redemption_id,
    household_id::integer as household_id,
    coupon_id::integer as coupon_id,
    campaign_id::integer as campaign_id,
    day::integer as day
from {{ source('retail_raw', 'raw_coupon_redemptions') }}
where redemption_id is not null
  and household_id is not null
