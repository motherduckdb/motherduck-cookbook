with scored as (
    select
        customer_id,
        as_of_date,
        region_id,
        customer_name,
        marketing_opt_in,
        segment,
        prediction_window_days,
        observed_churn_rate,
        base_rate_points,
        matched_signal_count,
        signal_risk_points,
        risk_score,
        matched_signals,
        top_reason_1,
        top_reason_2,
        top_reason_3,
        recommended_action,
        offer_type
    from {{ ref('fct_customer_churn_scores_daily') }}
),

actions as (
    select
        as_of_date,
        row_number() over (
            partition by as_of_date
            order by risk_score desc, observed_churn_rate desc, customer_id
        ) as queue_rank,
        customer_id,
        customer_name,
        region_id,
        segment,
        prediction_window_days,
        observed_churn_rate,
        risk_score,
        case
            when risk_score >= 70 then 'high'
            when risk_score >= 40 then 'medium'
            else 'low'
        end as risk_band,
        base_rate_points,
        matched_signal_count,
        matched_signals,
        top_reason_1,
        top_reason_2,
        top_reason_3,
        recommended_action,
        offer_type
    from scored
    where risk_score >= 25
)

select
    as_of_date,
    queue_rank,
    customer_id,
    customer_name,
    region_id,
    segment,
    prediction_window_days,
    observed_churn_rate,
    risk_score,
    risk_band,
    base_rate_points,
    matched_signal_count,
    matched_signals,
    top_reason_1,
    top_reason_2,
    top_reason_3,
    recommended_action,
    offer_type
from actions
