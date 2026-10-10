select
    household_id,
    transaction_id,
    basket_id,
    week_no,
    day,
    transaction_time,
    store_id,
    product_id,
    coalesce(sales_amount - discount_amount - coupon_discount - coupon_discount_match, 0.0) as amount_list,
    case
        when coalesce(coupon_discount_match, 0.0) = 0.0 then -1 * coalesce(coupon_discount, 0.0)
        else 0.0
    end as campaign_coupon_discount,
    case
        when coalesce(coupon_discount_match, 0.0) != 0.0 then -1 * coalesce(coupon_discount, 0.0)
        else 0.0
    end as manufacturer_coupon_discount,
    -1 * coalesce(coupon_discount_match, 0.0) as manufacturer_coupon_match_discount,
    -1 * coalesce(coupon_discount + coupon_discount_match, 0.0) as total_coupon_discount,
    -1 * coalesce(discount_amount, 0.0) as instore_discount,
    coalesce(sales_amount, 0.0) as amount_paid,
    quantity as units
from {{ ref('stg_transactions') }}
