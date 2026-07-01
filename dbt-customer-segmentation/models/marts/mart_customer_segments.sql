with feature_rankings as (
    select
        household_id,
        feature_name,
        feature_value,
        row_number() over (
            partition by household_id
            order by abs(feature_value) desc, feature_name
        ) as feature_rank
    from {{ ref('fct_household_feature_vectors') }}
),

feature_summaries as (
    select
        household_id,
        string_agg(
            replace(feature_name, '_z', '') || '=' || cast(round(feature_value, 2) as varchar),
            ', '
            order by feature_rank
        ) as strongest_standardized_features
    from feature_rankings
    where feature_rank <= 3
    group by 1
),

segment_quality as (
    select
        segment_id,
        count(*) as segment_households,
        avg(segment_distance) as avg_segment_distance,
        avg(segment_confidence) as avg_segment_confidence
    from {{ ref('fct_household_segments') }}
    group by 1
)

select
    segments.household_id,
    segments.segmentation_reference_day,
    segments.segment_id,
    segments.segment_name,
    segments.segment_label,
    segments.segment_description,
    segments.segment_distance,
    segments.alternative_segment_name,
    segments.alternative_segment_distance,
    segments.segment_confidence,
    segment_quality.segment_households,
    segment_quality.avg_segment_distance,
    segment_quality.avg_segment_confidence,
    features.purchase_dates,
    features.basket_count,
    features.total_amount_paid,
    features.total_discount_amount,
    features.has_campaign_targeted_purchase,
    features.has_campaign_coupon_redemption,
    features.has_manufacturer_coupon_redemption,
    features.has_private_label_purchase,
    features.has_instore_discount,
    features.pct_amount_campaign_targeted,
    features.pct_amount_campaign_coupon,
    features.pct_amount_manufacturer_coupon,
    features.pct_amount_private_label,
    features.pct_amount_instore_discount,
    features.discount_depth,
    features.avg_basket_value,
    feature_summaries.strongest_standardized_features,
    python_clusters.kmeans_cluster_id,
    python_clusters.kmeans_cluster_households,
    python_clusters.aligned_segment_id as kmeans_aligned_segment_id,
    python_clusters.aligned_segment_name as kmeans_aligned_segment_name,
    python_clusters.cluster_segment_distance as kmeans_cluster_segment_distance,
    python_clusters.distance_to_kmeans_center,
    python_clusters.kmeans_silhouette_score,
    python_clusters.overall_kmeans_silhouette_score,
    segments.recommended_action,
    segments.recommended_offer
from {{ ref('fct_household_segments') }} as segments
inner join {{ ref('fct_household_features') }} as features
    on segments.household_id = features.household_id
left join feature_summaries
    on segments.household_id = feature_summaries.household_id
left join segment_quality
    on segments.segment_id = segment_quality.segment_id
left join {{ ref('fct_household_kmeans_segments') }} as python_clusters
    on segments.household_id = python_clusters.household_id
