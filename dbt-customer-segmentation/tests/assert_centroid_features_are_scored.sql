select distinct
    centroids.feature_name
from {{ ref('segment_centroids') }} as centroids
left join {{ ref('fct_household_feature_vectors') }} as vectors
    on centroids.feature_name = vectors.feature_name
where vectors.feature_name is null
