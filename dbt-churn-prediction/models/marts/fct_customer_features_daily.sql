with as_of as (
    select cast('{{ var("churn_as_of_date") }}' as date) as as_of_date
),

{{ customer_feature_rows('as_of') }}
