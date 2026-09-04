select
    household_id,
    segment_id,
    segment_distance
from {{ ref('fct_household_segment_distances') }}
where segment_distance < 0
