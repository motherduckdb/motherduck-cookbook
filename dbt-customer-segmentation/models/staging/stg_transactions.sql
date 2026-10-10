select
    transaction_id::integer as transaction_id,
    household_id::integer as household_id,
    basket_id::bigint as basket_id,
    day::integer as day,
    product_id::integer as product_id,
    quantity::integer as quantity,
    cast(sales_amount as decimal(12, 2)) as sales_amount,
    store_id::integer as store_id,
    cast(discount_amount as decimal(12, 2)) as discount_amount,
    transaction_time::integer as transaction_time,
    week_no::integer as week_no,
    cast(coupon_discount as decimal(12, 2)) as coupon_discount,
    cast(coupon_discount_match as decimal(12, 2)) as coupon_discount_match
from {{ source('retail_raw', 'raw_transactions') }}
where transaction_id is not null
  and household_id is not null
  and product_id is not null
