select
    household_id,
    kmeans_cluster_id
from {{ ref('fct_household_kmeans_segments') }}
where aligned_segment_id is null
   or aligned_segment_name is null
