select
    coupon_id::integer as coupon_id,
    campaign_id::integer as campaign_id,
    product_id::integer as product_id
from {{ source('retail_raw', 'raw_coupons') }}
where coupon_id is not null
  and campaign_id is not null
  and product_id is not null
