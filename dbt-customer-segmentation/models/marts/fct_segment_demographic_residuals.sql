with demographics_long as (
    select
        household_id,
        demographic_field,
        demographic_value
    from {{ ref('stg_households') }}
    unpivot (
        demographic_value for demographic_field in (
            age_bracket,
            income_bracket,
            homeownership,
            composition
        )
    )
),

assigned_demographics as (
    select
        segments.segment_id,
        segments.segment_name,
        segments.segment_label,
        demographics_long.demographic_field,
        demographics_long.demographic_value,
        segments.household_id
    from {{ ref('fct_household_segments') }} as segments
    inner join demographics_long
        on segments.household_id = demographics_long.household_id
    where demographics_long.demographic_value is not null
),

segment_values as (
    select distinct
        segment_id,
        segment_name,
        segment_label,
        demographic_field
    from assigned_demographics
),

category_values as (
    select distinct
        demographic_field,
        demographic_value
    from assigned_demographics
),

contingency_grid as (
    select
        segment_values.segment_id,
        segment_values.segment_name,
        segment_values.segment_label,
        segment_values.demographic_field,
        category_values.demographic_value
    from segment_values
    inner join category_values
        on segment_values.demographic_field = category_values.demographic_field
),

observed_counts as (
    select
        segment_id,
        demographic_field,
        demographic_value,
        count(*) as observed_count
    from assigned_demographics
    group by 1, 2, 3
),

segment_totals as (
    select
        demographic_field,
        segment_id,
        count(*) as segment_total
    from assigned_demographics
    group by 1, 2
),

category_totals as (
    select
        demographic_field,
        demographic_value,
        count(*) as category_total
    from assigned_demographics
    group by 1, 2
),

field_totals as (
    select
        demographic_field,
        count(*) as field_total
    from assigned_demographics
    group by 1
),

residuals as (
    select
        contingency_grid.segment_id,
        contingency_grid.segment_name,
        contingency_grid.segment_label,
        contingency_grid.demographic_field,
        contingency_grid.demographic_value,
        coalesce(observed_counts.observed_count, 0) as observed_count,
        segment_totals.segment_total,
        category_totals.category_total,
        field_totals.field_total,
        (
            segment_totals.segment_total::double
            * category_totals.category_total::double
        ) / nullif(field_totals.field_total, 0) as expected_count
    from contingency_grid
    left join observed_counts
        on contingency_grid.segment_id = observed_counts.segment_id
        and contingency_grid.demographic_field = observed_counts.demographic_field
        and contingency_grid.demographic_value = observed_counts.demographic_value
    inner join segment_totals
        on contingency_grid.demographic_field = segment_totals.demographic_field
        and contingency_grid.segment_id = segment_totals.segment_id
    inner join category_totals
        on contingency_grid.demographic_field = category_totals.demographic_field
        and contingency_grid.demographic_value = category_totals.demographic_value
    inner join field_totals
        on contingency_grid.demographic_field = field_totals.demographic_field
),

residual_scores as (
    select
        segment_id,
        segment_name,
        segment_label,
        demographic_field,
        demographic_value,
        observed_count,
        segment_total,
        category_total,
        field_total,
        expected_count,
        (observed_count - expected_count) / nullif(sqrt(expected_count), 0.0) as pearson_residual,
        power(
            observed_count - expected_count,
            2
        ) / nullif(expected_count, 0.0) as chi_square_contribution
    from residuals
)

select
    segment_id,
    segment_name,
    segment_label,
    demographic_field,
    demographic_value,
    observed_count,
    segment_total,
    category_total,
    field_total,
    expected_count,
    pearson_residual,
    chi_square_contribution,
    case
        when abs(pearson_residual) >= 4 then 'very strong over/under representation'
        when abs(pearson_residual) >= 2 then 'notable over/under representation'
        else 'within expected range'
    end as residual_interpretation
from residual_scores
