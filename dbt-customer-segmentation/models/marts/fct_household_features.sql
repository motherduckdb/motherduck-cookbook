with metrics as (
    select *
    from {{ ref('fct_household_promotion_metrics') }}
),

features as (
    select
        household_id,
        basket_count,
        purchase_dates,
        total_units,
        total_amount_list,
        total_amount_paid,
        total_discount_amount,
        campaign_coupon_discount_amount,
        manufacturer_coupon_discount_amount,
        instore_discount_amount,

        case when pdates_campaign_targeted > 0 then 1 else 0 end as has_campaign_targeted_purchase,
        case when pdates_campaign_coupon_redemptions > 0 then 1 else 0 end as has_campaign_coupon_redemption,
        case when pdates_manufacturer_coupon_redemptions > 0 then 1 else 0 end as has_manufacturer_coupon_redemption,
        case when pdates_private_label > 0 then 1 else 0 end as has_private_label_purchase,
        case when pdates_instore_discount_applied > 0 then 1 else 0 end as has_instore_discount,

        case when purchase_dates > 0 and pdates_campaign_targeted > 0 then pdates_campaign_targeted::double / purchase_dates end as pct_purchase_dates_campaign_targeted,
        case when purchase_dates > 0 and pdates_campaign_coupon_redemptions > 0 then pdates_campaign_coupon_redemptions::double / purchase_dates end as pct_purchase_dates_campaign_coupon,
        case when purchase_dates > 0 and pdates_manufacturer_coupon_redemptions > 0 then pdates_manufacturer_coupon_redemptions::double / purchase_dates end as pct_purchase_dates_manufacturer_coupon,
        case when purchase_dates > 0 and pdates_private_label > 0 then pdates_private_label::double / purchase_dates end as pct_purchase_dates_private_label,
        case when purchase_dates > 0 and pdates_instore_discount_applied > 0 then pdates_instore_discount_applied::double / purchase_dates end as pct_purchase_dates_instore_discount,

        case when total_amount_list > 0 and pdates_campaign_targeted > 0 then amount_list_with_campaign_targeted / total_amount_list end as pct_amount_campaign_targeted,
        case when total_amount_list > 0 and pdates_campaign_coupon_redemptions > 0 then amount_list_with_campaign_coupon_redemptions / total_amount_list end as pct_amount_campaign_coupon,
        case when total_amount_list > 0 and pdates_manufacturer_coupon_redemptions > 0 then amount_list_with_manufacturer_coupon_redemptions / total_amount_list end as pct_amount_manufacturer_coupon,
        case when total_amount_list > 0 and pdates_private_label > 0 then amount_list_with_private_label / total_amount_list end as pct_amount_private_label,
        case when total_amount_list > 0 and pdates_instore_discount_applied > 0 then amount_list_with_instore_discount_applied / total_amount_list end as pct_amount_instore_discount,
        case when total_amount_list > 0 then total_discount_amount / total_amount_list end as discount_depth,
        case when basket_count > 0 then total_amount_paid / basket_count end as avg_basket_value,
        case when purchase_dates > 0 then total_units::double / purchase_dates end as avg_units_per_purchase_date
    from metrics
)

select *
from features
