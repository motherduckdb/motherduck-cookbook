with assigned_features as (
    select
        segments.segment_id,
        segments.segment_name,
        segments.segment_label,
        vectors.feature_name,
        vectors.raw_value,
        vectors.feature_value
    from {{ ref('fct_household_segments') }} as segments
    inner join {{ ref('fct_household_feature_vectors') }} as vectors
        on segments.household_id = vectors.household_id
),

segment_rollup as (
    select
        segment_id,
        segment_name,
        segment_label,
        feature_name,
        count(*) as household_count,
        avg(raw_value) as avg_raw_value,
        avg(feature_value) as avg_standardized_value,
        min(feature_value) as min_standardized_value,
        max(feature_value) as max_standardized_value
    from assigned_features
    group by 1, 2, 3, 4
)

select
    segment_rollup.segment_id,
    segment_rollup.segment_name,
    segment_rollup.segment_label,
    segment_rollup.feature_name,
    segment_rollup.household_count,
    segment_rollup.avg_raw_value,
    segment_rollup.avg_standardized_value,
    segment_rollup.min_standardized_value,
    segment_rollup.max_standardized_value,
    centroids.centroid_value,
    centroids.feature_weight,
    abs(segment_rollup.avg_standardized_value - centroids.centroid_value) as centroid_gap
from segment_rollup
left join {{ ref('segment_centroids') }} as centroids
    on segment_rollup.segment_id = centroids.segment_id
    and segment_rollup.feature_name = centroids.feature_name
