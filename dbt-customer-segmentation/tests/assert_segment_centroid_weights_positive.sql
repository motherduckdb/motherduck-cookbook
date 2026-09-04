select
    segment_id,
    segment_name,
    feature_name,
    feature_weight
from {{ ref('segment_centroids') }}
where feature_weight <= 0
