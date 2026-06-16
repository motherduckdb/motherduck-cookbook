{% macro churn_label_dates() %}
values
    (date '2025-12-31'),
    (date '2026-01-15'),
    (date '2026-01-31'),
    (date '2026-02-15'),
    (date '2026-02-28'),
    (date '2026-03-15'),
    (date '2026-03-31')
{% endmacro %}

{% macro customer_feature_rows(as_of_relation) %}
customers as (
    select
        customer_id,
        region_id,
        customer_name,
        signup_date,
        marketing_opt_in
    from {{ ref('stg_customers') }}
),

customer_dates as (
    select
        customers.customer_id,
        customers.region_id,
        customers.customer_name,
        customers.signup_date,
        customers.marketing_opt_in,
        feature_dates.as_of_date
    from customers
    cross join {{ as_of_relation }} as feature_dates
),

membership_state as (
    select
        customer_dates.customer_id,
        customer_dates.as_of_date,
        max(
            case
                when memberships.status = 'active'
                    and memberships.started_at <= customer_dates.as_of_date
                    and (memberships.canceled_at is null or memberships.canceled_at > customer_dates.as_of_date)
                    then 1
                else 0
            end
        ) as is_active_member,
        min(
            case
                when memberships.status = 'active'
                    and memberships.started_at <= customer_dates.as_of_date
                    and (memberships.canceled_at is null or memberships.canceled_at > customer_dates.as_of_date)
                    then memberships.renewal_date
            end
        ) as next_renewal_date
    from customer_dates
    left join {{ ref('stg_memberships') }} as memberships
        on customer_dates.customer_id = memberships.customer_id
    group by 1, 2
),

current_subscription as (
    select
        customer_dates.customer_id,
        customer_dates.as_of_date,
        subscription_history.membership_id,
        subscription_history.started_at,
        subscription_history.duration_days,
        subscription_history.prior_memberships,
        subscription_history.initial_plan_days,
        subscription_history.is_auto_renew,
        subscription_history.initial_payment_method,
        subscription_history.acquisition_channel,
        row_number() over (
            partition by customer_dates.customer_id, customer_dates.as_of_date
            order by subscription_history.started_at desc, subscription_history.membership_id desc
        ) as subscription_rank
    from customer_dates
    inner join {{ ref('fct_subscription_history') }} as subscription_history
        on customer_dates.customer_id = subscription_history.customer_id
        and subscription_history.started_at <= customer_dates.as_of_date
        and (
            subscription_history.ended_at is null
            or subscription_history.ended_at > customer_dates.as_of_date
        )
),

event_rollup as (
    select
        customer_dates.customer_id,
        customer_dates.as_of_date,
        max(usage_events.event_date) as last_event_date,
        sum(case when usage_events.event_date > customer_dates.as_of_date - interval '30 days' then 1 else 0 end) as events_30d,
        sum(case when usage_events.event_date > customer_dates.as_of_date - interval '60 days' then 1 else 0 end) as events_60d,
        sum(case when usage_events.event_date > customer_dates.as_of_date - interval '90 days' then 1 else 0 end) as events_90d,
        sum(case when usage_events.event_date > customer_dates.as_of_date - interval '120 days' then 1 else 0 end) as events_120d,
        sum(case when usage_events.event_date > customer_dates.as_of_date - interval '180 days' then 1 else 0 end) as events_180d,
        sum(case when usage_events.event_date > customer_dates.as_of_date - interval '90 days' then usage_events.net_amount else 0 end) as spend_90d,
        sum(case when usage_events.event_date > customer_dates.as_of_date - interval '60 days' and usage_events.complaint_flag then 1 else 0 end) as complaints_60d,
        avg(case when usage_events.event_date > customer_dates.as_of_date - interval '60 days' then usage_events.satisfaction_score end) as avg_satisfaction_60d
    from customer_dates
    left join {{ ref('stg_usage_events') }} as usage_events
        on customer_dates.customer_id = usage_events.customer_id
        and usage_events.event_date <= customer_dates.as_of_date
    group by 1, 2
),

payment_rollup as (
    select
        customer_dates.customer_id,
        customer_dates.as_of_date,
        sum(
            case
                when payments.payment_date > customer_dates.as_of_date - interval '30 days'
                    and payments.payment_status = 'failed'
                    then 1
                else 0
            end
        ) as failed_payments_30d
    from customer_dates
    left join {{ ref('stg_payments') }} as payments
        on customer_dates.customer_id = payments.customer_id
        and payments.payment_date <= customer_dates.as_of_date
    group by 1, 2
)

select
    customer_dates.customer_id,
    customer_dates.as_of_date,
    customer_dates.region_id,
    customer_dates.customer_name,
    customer_dates.marketing_opt_in,
    case when coalesce(membership_state.is_active_member, 0) = 1 then 'member' else 'casual' end as segment,
    coalesce(membership_state.is_active_member, 0) as is_active_member,
    current_subscription.membership_id,
    membership_state.next_renewal_date,
    case
        when membership_state.next_renewal_date is null then null
        else date_diff('day', customer_dates.as_of_date, membership_state.next_renewal_date)
    end as days_until_renewal,
    case
        when current_subscription.started_at is null then 0
        else date_diff('day', current_subscription.started_at, customer_dates.as_of_date)
    end as membership_age_days,
    coalesce(current_subscription.prior_memberships, 0) as prior_memberships,
    current_subscription.initial_plan_days,
    coalesce(current_subscription.is_auto_renew, 0) as is_auto_renew,
    current_subscription.initial_payment_method,
    current_subscription.acquisition_channel,
    event_rollup.last_event_date,
    case
        when event_rollup.last_event_date is null then 999
        else date_diff('day', event_rollup.last_event_date, customer_dates.as_of_date)
    end as days_since_last_event,
    coalesce(event_rollup.events_30d, 0) as events_30d,
    coalesce(event_rollup.events_60d, 0) as events_60d,
    coalesce(event_rollup.events_90d, 0) as events_90d,
    coalesce(event_rollup.events_120d, 0) as events_120d,
    coalesce(event_rollup.events_180d, 0) as events_180d,
    coalesce(event_rollup.spend_90d, 0) as spend_90d,
    coalesce(event_rollup.complaints_60d, 0) as complaints_60d,
    coalesce(payment_rollup.failed_payments_30d, 0) as failed_payments_30d,
    event_rollup.avg_satisfaction_60d,
    case
        when coalesce(membership_state.is_active_member, 0) = 1 then true
        when coalesce(event_rollup.events_120d, 0) >= 2 then true
        when coalesce(event_rollup.events_180d, 0) >= 3 then true
        else false
    end as is_eligible_for_scoring
from customer_dates
left join membership_state
    on customer_dates.customer_id = membership_state.customer_id
    and customer_dates.as_of_date = membership_state.as_of_date
left join current_subscription
    on customer_dates.customer_id = current_subscription.customer_id
    and customer_dates.as_of_date = current_subscription.as_of_date
    and current_subscription.subscription_rank = 1
left join event_rollup
    on customer_dates.customer_id = event_rollup.customer_id
    and customer_dates.as_of_date = event_rollup.as_of_date
left join payment_rollup
    on customer_dates.customer_id = payment_rollup.customer_id
    and customer_dates.as_of_date = payment_rollup.as_of_date
{% endmacro %}
