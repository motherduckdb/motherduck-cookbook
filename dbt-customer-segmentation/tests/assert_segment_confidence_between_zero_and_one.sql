select
    household_id,
    segment_confidence
from {{ ref('fct_household_segments') }}
where segment_confidence < 0
   or segment_confidence > 1
