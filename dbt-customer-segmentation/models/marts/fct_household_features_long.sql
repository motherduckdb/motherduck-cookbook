with features as (
    select
        household_id,
        purchase_dates::double as purchase_frequency_z,
        pct_amount_campaign_targeted::double as promo_targeting_z,
        pct_amount_campaign_coupon::double as campaign_coupon_z,
        pct_amount_manufacturer_coupon::double as manufacturer_coupon_z,
        pct_amount_private_label::double as private_label_z,
        pct_amount_instore_discount::double as instore_discount_z,
        avg_basket_value::double as basket_value_z,
        discount_depth::double as discount_depth_z
    from {{ ref('fct_household_features') }}
)

select
    household_id,
    feature_name,
    raw_value
from features
unpivot include nulls (
    raw_value for feature_name in (
        purchase_frequency_z,
        promo_targeting_z,
        campaign_coupon_z,
        manufacturer_coupon_z,
        private_label_z,
        instore_discount_z,
        basket_value_z,
        discount_depth_z
    )
)
