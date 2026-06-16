with label_dates(as_of_date) as (
    {{ churn_label_dates() }}
),

{{ customer_feature_rows('label_dates') }}
