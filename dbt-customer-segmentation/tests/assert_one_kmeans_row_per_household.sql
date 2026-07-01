select
    household_id,
    count(*) as kmeans_rows
from {{ ref('fct_household_kmeans_segments') }}
group by 1
having count(*) != 1
