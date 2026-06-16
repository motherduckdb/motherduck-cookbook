with feature_rows as (
    select
        customer_id,
        as_of_date,
        region_id,
        customer_name,
        marketing_opt_in,
        segment,
        is_active_member,
        membership_age_days,
        prior_memberships,
        is_auto_renew,
        days_since_last_event,
        events_30d,
        events_60d,
        events_90d,
        spend_90d,
        failed_payments_30d,
        complaints_60d,
        avg_satisfaction_60d
    from {{ ref('fct_customer_features_daily') }}
    where is_eligible_for_scoring
),

historical_rows as (
    select
        features.customer_id,
        features.as_of_date,
        features.segment,
        labels.prediction_window_days,
        labels.churned,
        features.membership_age_days,
        features.prior_memberships,
        features.is_auto_renew,
        features.days_since_last_event,
        features.events_30d,
        features.events_90d,
        features.failed_payments_30d,
        features.complaints_60d,
        features.avg_satisfaction_60d
    from {{ ref('fct_customer_features_historical') }} as features
    inner join {{ ref('fct_customer_churn_labels') }} as labels
        on features.customer_id = labels.customer_id
        and features.as_of_date = labels.as_of_date
        and features.segment = labels.segment
    where features.is_eligible_for_scoring
),

segment_rates as (
    select
        segment,
        prediction_window_days,
        labeled_customers,
        churned_customers,
        observed_churn_rate
    from {{ ref('fct_churn_segment_rates') }}
),

historical_signal_flags as (
    select customer_id, as_of_date, segment, prediction_window_days, churned, 'payment_risk' as signal_name
    from historical_rows
    where failed_payments_30d > 0

    union all

    select customer_id, as_of_date, segment, prediction_window_days, churned, 'activity_risk' as signal_name
    from historical_rows
    where days_since_last_event >= 45
       or events_30d = 0

    union all

    select customer_id, as_of_date, segment, prediction_window_days, churned, 'experience_risk' as signal_name
    from historical_rows
    where complaints_60d > 0
       or (avg_satisfaction_60d is not null and avg_satisfaction_60d < 7)

    union all

    select customer_id, as_of_date, segment, prediction_window_days, churned, 'membership_risk' as signal_name
    from historical_rows
    where (segment = 'member' and is_auto_renew = 0)
       or (segment = 'member' and prior_memberships > 0)
),

signal_rates as (
    select
        signal_flags.segment,
        signal_flags.prediction_window_days,
        signal_flags.signal_name,
        count(*) as signal_labeled_customers,
        sum(signal_flags.churned) as signal_churned_customers,
        avg(signal_flags.churned) as signal_churn_rate
    from historical_signal_flags as signal_flags
    group by 1, 2, 3
),

signal_details as (
    select
        feature_rows.customer_id,
        feature_rows.as_of_date,
        feature_rows.segment,
        case when feature_rows.segment = 'member' then 30 else 60 end as prediction_window_days,
        'payment_risk' as signal_name,
        'recent failed payment attempts' as reason,
        'prioritize billing recovery outreach' as recommended_action,
        'billing_support' as offer_type
    from feature_rows
    where failed_payments_30d > 0

    union all

    select
        feature_rows.customer_id,
        feature_rows.as_of_date,
        feature_rows.segment,
        case when feature_rows.segment = 'member' then 30 else 60 end as prediction_window_days,
        'activity_risk' as signal_name,
        'recent activity has dropped' as reason,
        'send a re-engagement message' as recommended_action,
        're_engagement' as offer_type
    from feature_rows
    where days_since_last_event >= 45
       or events_30d = 0

    union all

    select
        feature_rows.customer_id,
        feature_rows.as_of_date,
        feature_rows.segment,
        case when feature_rows.segment = 'member' then 30 else 60 end as prediction_window_days,
        'experience_risk' as signal_name,
        'recent complaints or low satisfaction' as reason,
        'offer proactive support' as recommended_action,
        'support_outreach' as offer_type
    from feature_rows
    where complaints_60d > 0
       or (avg_satisfaction_60d is not null and avg_satisfaction_60d < 7)

    union all

    select
        feature_rows.customer_id,
        feature_rows.as_of_date,
        feature_rows.segment,
        case when feature_rows.segment = 'member' then 30 else 60 end as prediction_window_days,
        'membership_risk' as signal_name,
        'membership setup suggests renewal risk' as reason,
        'review renewal and onboarding journey' as recommended_action,
        'renewal_nudge' as offer_type
    from feature_rows
    where (segment = 'member' and is_auto_renew = 0)
       or (segment = 'member' and prior_memberships > 0)
),

ranked_signals as (
    select
        signal_details.customer_id,
        signal_details.as_of_date,
        signal_details.signal_name,
        signal_details.reason,
        signal_details.recommended_action,
        signal_details.offer_type,
        coalesce(signal_rates.signal_labeled_customers, 0) as signal_labeled_customers,
        coalesce(signal_rates.signal_churned_customers, 0) as signal_churned_customers,
        coalesce(signal_rates.signal_churn_rate, 0.0) as signal_churn_rate,
        coalesce(segment_rates.observed_churn_rate, 0.0) as observed_churn_rate,
        greatest(
            coalesce(signal_rates.signal_churn_rate, 0.0) - coalesce(segment_rates.observed_churn_rate, 0.0),
            0.0
        ) as signal_rate_uplift,
        row_number() over (
            partition by signal_details.customer_id, signal_details.as_of_date
            order by greatest(
                coalesce(signal_rates.signal_churn_rate, 0.0) - coalesce(segment_rates.observed_churn_rate, 0.0),
                0.0
            ) desc, signal_details.signal_name
        ) as signal_rank
    from signal_details
    left join signal_rates
        on signal_details.segment = signal_rates.segment
        and signal_details.prediction_window_days = signal_rates.prediction_window_days
        and signal_details.signal_name = signal_rates.signal_name
    left join segment_rates
        on signal_details.segment = segment_rates.segment
        and signal_details.prediction_window_days = segment_rates.prediction_window_days
),

signal_summary as (
    select
        customer_id,
        as_of_date,
        count(*) as matched_signal_count,
        sum(signal_rate_uplift) as total_signal_rate_uplift,
        cast(round(sum(signal_rate_uplift) * 100, 0) as integer) as signal_risk_points,
        string_agg(signal_name, ', ' order by signal_rate_uplift desc, signal_name) as matched_signals,
        max(case when signal_rank = 1 then reason end) as top_reason_1,
        max(case when signal_rank = 2 then reason end) as top_reason_2,
        max(case when signal_rank = 3 then reason end) as top_reason_3,
        max(case when signal_rank = 1 then recommended_action end) as recommended_action,
        max(case when signal_rank = 1 then offer_type end) as offer_type
    from ranked_signals
    group by 1, 2
),

scored as (
    select
        feature_rows.customer_id,
        feature_rows.as_of_date,
        feature_rows.region_id,
        feature_rows.customer_name,
        feature_rows.marketing_opt_in,
        feature_rows.segment,
        case when feature_rows.segment = 'member' then 30 else 60 end as prediction_window_days,
        feature_rows.membership_age_days,
        feature_rows.prior_memberships,
        feature_rows.is_auto_renew,
        feature_rows.days_since_last_event,
        feature_rows.events_30d,
        feature_rows.events_60d,
        feature_rows.events_90d,
        feature_rows.spend_90d,
        feature_rows.failed_payments_30d,
        feature_rows.complaints_60d,
        feature_rows.avg_satisfaction_60d,
        coalesce(segment_rates.labeled_customers, 0) as labeled_customers,
        coalesce(segment_rates.churned_customers, 0) as churned_customers,
        coalesce(segment_rates.observed_churn_rate, 0.0) as observed_churn_rate,
        cast(round(coalesce(segment_rates.observed_churn_rate, 0.0) * 100, 0) as integer) as base_rate_points,
        coalesce(signal_summary.matched_signal_count, 0) as matched_signal_count,
        coalesce(signal_summary.signal_risk_points, 0) as signal_risk_points,
        cast(
            round(
                least(
                    1.0,
                    coalesce(segment_rates.observed_churn_rate, 0.0) + coalesce(signal_summary.total_signal_rate_uplift, 0.0)
                ) * 100,
                0
            ) as integer
        ) as risk_score,
        coalesce(signal_summary.matched_signals, 'segment_rate_only') as matched_signals,
        coalesce(signal_summary.top_reason_1, 'historical segment churn rate') as top_reason_1,
        signal_summary.top_reason_2,
        signal_summary.top_reason_3,
        coalesce(signal_summary.recommended_action, 'monitor trend and review recent customer history') as recommended_action,
        coalesce(signal_summary.offer_type, 'no_offer') as offer_type
    from feature_rows
    left join segment_rates
        on feature_rows.segment = segment_rates.segment
        and case when feature_rows.segment = 'member' then 30 else 60 end = segment_rates.prediction_window_days
    left join signal_summary
        on feature_rows.customer_id = signal_summary.customer_id
        and feature_rows.as_of_date = signal_summary.as_of_date
)

select
    customer_id,
    as_of_date,
    region_id,
    customer_name,
    marketing_opt_in,
    segment,
    prediction_window_days,
    membership_age_days,
    prior_memberships,
    is_auto_renew,
    days_since_last_event,
    events_30d,
    events_60d,
    events_90d,
    spend_90d,
    failed_payments_30d,
    complaints_60d,
    avg_satisfaction_60d,
    labeled_customers,
    churned_customers,
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
from scored
