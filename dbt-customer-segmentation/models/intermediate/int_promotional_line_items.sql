with targeted_products_by_household as (
    select distinct
        campaign_households.household_id,
        coupons.product_id
    from {{ ref('stg_campaigns') }} as campaigns
    inner join {{ ref('stg_campaign_households') }} as campaign_households
        on campaigns.campaign_id = campaign_households.campaign_id
    inner join {{ ref('stg_coupons') }} as coupons
        on campaigns.campaign_id = coupons.campaign_id
),

line_items as (
    select
        transactions.household_id,
        transactions.transaction_id,
        transactions.day,
        transactions.week_no,
        transactions.basket_id,
        transactions.product_id,
        products.department,
        products.brand,
        products.commodity_desc,
        products.is_private_label,
        transactions.amount_list,
        transactions.campaign_coupon_discount,
        transactions.manufacturer_coupon_discount,
        transactions.manufacturer_coupon_match_discount,
        transactions.total_coupon_discount,
        transactions.instore_discount,
        transactions.amount_paid,
        transactions.units,
        case when transactions.campaign_coupon_discount > 0 then 1 else 0 end as campaign_coupon_redemption,
        case when transactions.manufacturer_coupon_discount > 0 then 1 else 0 end as manufacturer_coupon_redemption,
        case when transactions.instore_discount > 0 then 1 else 0 end as instore_discount_applied,
        coalesce(products.is_private_label, 0) as private_label,
        case when targeted.product_id is null then 0 else 1 end as campaign_targeted
    from {{ ref('int_transactions_adjusted') }} as transactions
    inner join {{ ref('stg_products') }} as products
        on transactions.product_id = products.product_id
    left join targeted_products_by_household as targeted
        on transactions.household_id = targeted.household_id
        and transactions.product_id = targeted.product_id
)

select *
from line_items
