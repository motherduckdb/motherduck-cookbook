with households as (
    select distinct household_id
    from {{ ref('fct_household_features') }}
),

segment_features as (
    select
        segment_id::integer as segment_id,
        segment_name::varchar as segment_name,
        feature_name::varchar as feature_name,
        centroid_value::double as centroid_value,
        feature_weight::double as feature_weight
    from {{ ref('segment_centroids') }}
),

scored_feature_distances as (
    select
        households.household_id,
        segment_features.segment_id,
        segment_features.segment_name,
        segment_features.feature_name,
        coalesce(feature_vectors.feature_value, 0.0) as household_feature_value,
        segment_features.centroid_value,
        segment_features.feature_weight,
        segment_features.feature_weight * power(coalesce(feature_vectors.feature_value, 0.0) - segment_features.centroid_value, 2) as weighted_squared_distance
    from households
    cross join segment_features
    left join {{ ref('fct_household_feature_vectors') }} as feature_vectors
        on households.household_id = feature_vectors.household_id
        and segment_features.feature_name = feature_vectors.feature_name
)

select
    household_id,
    segment_id,
    segment_name,
    count(*) as scored_feature_count,
    sum(feature_weight) as total_feature_weight,
    sqrt(sum(weighted_squared_distance) / nullif(sum(feature_weight), 0.0)) as segment_distance,
    sum(weighted_squared_distance) as weighted_squared_distance
from scored_feature_distances
group by 1, 2, 3
