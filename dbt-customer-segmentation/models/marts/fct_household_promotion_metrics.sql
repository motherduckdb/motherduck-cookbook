select
    household_id,
    count(distinct basket_id) as basket_count,
    count(distinct day) as purchase_dates,
    sum(units) as total_units,
    sum(amount_list) as total_amount_list,
    sum(amount_paid) as total_amount_paid,
    sum(campaign_coupon_discount) as campaign_coupon_discount_amount,
    sum(manufacturer_coupon_discount) as manufacturer_coupon_discount_amount,
    sum(manufacturer_coupon_match_discount) as manufacturer_coupon_match_discount_amount,
    sum(total_coupon_discount + instore_discount) as total_discount_amount,
    sum(instore_discount) as instore_discount_amount,

    count(distinct case when campaign_targeted = 1 then day end) as pdates_campaign_targeted,
    count(distinct case when private_label = 1 then day end) as pdates_private_label,
    count(distinct case when campaign_targeted = 1 and private_label = 1 then day end) as pdates_campaign_targeted_private_label,
    count(distinct case when campaign_coupon_redemption = 1 then day end) as pdates_campaign_coupon_redemptions,
    count(distinct case when manufacturer_coupon_redemption = 1 then day end) as pdates_manufacturer_coupon_redemptions,
    count(distinct case when instore_discount_applied = 1 then day end) as pdates_instore_discount_applied,
    count(distinct case when campaign_targeted = 1 and instore_discount_applied = 1 then day end) as pdates_campaign_targeted_instore_discount_applied,
    count(distinct case when campaign_coupon_redemption = 1 and instore_discount_applied = 1 then day end) as pdates_campaign_coupon_redemption_instore_discount_applied,

    sum(case when campaign_targeted = 1 then amount_list else 0 end) as amount_list_with_campaign_targeted,
    sum(case when private_label = 1 then amount_list else 0 end) as amount_list_with_private_label,
    sum(case when campaign_targeted = 1 and private_label = 1 then amount_list else 0 end) as amount_list_with_campaign_targeted_private_label,
    sum(case when campaign_coupon_redemption = 1 then amount_list else 0 end) as amount_list_with_campaign_coupon_redemptions,
    sum(case when manufacturer_coupon_redemption = 1 then amount_list else 0 end) as amount_list_with_manufacturer_coupon_redemptions,
    sum(case when instore_discount_applied = 1 then amount_list else 0 end) as amount_list_with_instore_discount_applied,
    sum(case when campaign_targeted = 1 and instore_discount_applied = 1 then amount_list else 0 end) as amount_list_with_campaign_targeted_instore_discount_applied,
    sum(case when campaign_coupon_redemption = 1 and instore_discount_applied = 1 then amount_list else 0 end) as amount_list_with_campaign_coupon_redemption_instore_discount_applied
from {{ ref('int_promotional_line_items') }}
group by 1
