select
    segment_id,
    demographic_field,
    demographic_value,
    expected_count
from {{ ref('fct_segment_demographic_residuals') }}
where expected_count <= 0
