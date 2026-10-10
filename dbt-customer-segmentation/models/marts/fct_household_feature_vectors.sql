with feature_rows as (
    select
        household_id,
        feature_name,
        raw_value
    from {{ ref('fct_household_features_long') }}
),

feature_stats as (
    select
        feature_name,
        avg(raw_value) as feature_mean,
        coalesce(nullif(stddev_samp(raw_value), 0.0), 1.0) as feature_stddev,
        count(raw_value) as non_null_households
    from feature_rows
    group by 1
)

select
    feature_rows.household_id,
    feature_rows.feature_name,
    feature_rows.raw_value,
    feature_stats.feature_mean,
    feature_stats.feature_stddev,
    feature_stats.non_null_households,
    case
        when feature_rows.raw_value is null then 0.0
        else (feature_rows.raw_value - feature_stats.feature_mean) / feature_stats.feature_stddev
    end as feature_value
from feature_rows
inner join feature_stats
    on feature_rows.feature_name = feature_stats.feature_name
