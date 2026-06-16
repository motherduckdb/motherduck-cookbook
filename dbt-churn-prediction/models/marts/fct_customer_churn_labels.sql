with label_dates(as_of_date) as (
    {{ churn_label_dates() }}
),

customer_dates as (
    select
        customers.customer_id,
        label_dates.as_of_date
    from {{ ref('stg_customers') }} as customers
    cross join label_dates
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
        max(
            case
                when memberships.canceled_at > customer_dates.as_of_date
                    and memberships.canceled_at <= customer_dates.as_of_date + interval '30 days'
                    then 1
                else 0
            end
        ) as canceled_next_30d
    from customer_dates
    left join {{ ref('stg_memberships') }} as memberships
        on customer_dates.customer_id = memberships.customer_id
    group by 1, 2
),

event_state as (
    select
        customer_dates.customer_id,
        customer_dates.as_of_date,
        sum(case when usage_events.event_date <= customer_dates.as_of_date and usage_events.event_date > customer_dates.as_of_date - interval '120 days' then 1 else 0 end) as events_prior_120d,
        sum(case when usage_events.event_date <= customer_dates.as_of_date and usage_events.event_date > customer_dates.as_of_date - interval '180 days' then 1 else 0 end) as events_prior_180d,
        sum(case when usage_events.event_date > customer_dates.as_of_date and usage_events.event_date <= customer_dates.as_of_date + interval '30 days' then 1 else 0 end) as events_next_30d,
        sum(case when usage_events.event_date > customer_dates.as_of_date and usage_events.event_date <= customer_dates.as_of_date + interval '60 days' then 1 else 0 end) as events_next_60d
    from customer_dates
    left join {{ ref('stg_usage_events') }} as usage_events
        on customer_dates.customer_id = usage_events.customer_id
    group by 1, 2
),

labels as (
    select
        customer_dates.customer_id,
        customer_dates.as_of_date,
        case when coalesce(membership_state.is_active_member, 0) = 1 then 'member' else 'casual' end as segment,
        case
            when coalesce(membership_state.is_active_member, 0) = 1 then true
            when coalesce(event_state.events_prior_120d, 0) >= 2 then true
            when coalesce(event_state.events_prior_180d, 0) >= 3 then true
            else false
        end as is_eligible_for_label,
        case
            when coalesce(membership_state.is_active_member, 0) = 1 then 30
            else 60
        end as prediction_window_days,
        case
            when coalesce(membership_state.is_active_member, 0) = 1
                then greatest(coalesce(membership_state.canceled_next_30d, 0), case when coalesce(event_state.events_next_30d, 0) = 0 then 1 else 0 end)
            when coalesce(event_state.events_prior_120d, 0) >= 2 or coalesce(event_state.events_prior_180d, 0) >= 3
                then case when coalesce(event_state.events_next_60d, 0) = 0 then 1 else 0 end
            else null
        end as churned
    from customer_dates
    left join membership_state
        on customer_dates.customer_id = membership_state.customer_id
        and customer_dates.as_of_date = membership_state.as_of_date
    left join event_state
        on customer_dates.customer_id = event_state.customer_id
        and customer_dates.as_of_date = event_state.as_of_date
)

select
    customer_id,
    as_of_date,
    segment,
    is_eligible_for_label,
    prediction_window_days,
    churned
from labels
where is_eligible_for_label
