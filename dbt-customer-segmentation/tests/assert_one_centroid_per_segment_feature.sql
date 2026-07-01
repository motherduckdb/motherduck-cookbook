select
    segment_id,
    feature_name,
    count(*) as centroid_rows
from {{ ref('segment_centroids') }}
group by 1, 2
having count(*) != 1
