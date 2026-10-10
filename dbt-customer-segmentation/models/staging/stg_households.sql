select
    household_id::integer as household_id,
    age_bracket::varchar as age_bracket,
    marital_status::varchar as marital_status,
    income_bracket::varchar as income_bracket,
    case income_bracket::varchar
        when 'Under 15K' then 0
        when '15-24K' then 15
        when '25-34K' then 25
        when '35-49K' then 35
        when '50-74K' then 50
        when '75-99K' then 75
        when '100-124K' then 100
        when '125-149K' then 125
        when '150-174K' then 150
        when '175-199K' then 175
        when '200-249K' then 200
        when '250K+' then 250
        else null
    end as income_bracket_floor,
    homeownership::varchar as homeownership,
    composition::varchar as composition,
    case composition::varchar
        when 'Single Female' then 0
        when 'Single Male' then 1
        when '1 Adult Kids' then 2
        when '2 Adults Kids' then 3
        when '2 Adults No Kids' then 4
        else 5
    end as composition_sort_order,
    size_category::varchar as size_category,
    child_category::varchar as child_category
from {{ source('retail_raw', 'raw_households') }}
where household_id is not null
