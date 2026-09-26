select distinct
    centroids.segment_id,
    centroids.segment_name
from {{ ref('segment_centroids') }} as centroids
left join {{ ref('segment_playbook') }} as playbook
    on centroids.segment_id = playbook.segment_id
where playbook.segment_id is null
