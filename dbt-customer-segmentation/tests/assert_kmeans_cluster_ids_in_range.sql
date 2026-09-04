select
    household_id,
    kmeans_cluster_id
from {{ ref('fct_household_kmeans_segments') }}
where kmeans_cluster_id < 0
