select
    household_id,
    segment_id,
    count(*) as distance_rows
from {{ ref('fct_household_segment_distances') }}
group by 1, 2
having count(*) != 1
