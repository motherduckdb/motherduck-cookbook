select
    edges.target_domain_name as target_domain,
    edges.source_domain_name as linking_domain,
    edges.source_domain_reversed as linking_host_reversed,
    edges.source_host_count as linking_host_count,
    edges.source_harmonicc_rank as linking_harmonicc_rank,
    edges.source_pagerank_rank as linking_pagerank_rank,
    edges.source_pagerank_value as linking_pagerank_value,
    edges.source_domain_id as from_id,
    edges.target_domain_id as to_id
from {{ ref('int_domain_edges_with_rank') }} as edges
order by edges.source_pagerank_rank nulls last, edges.source_domain_name
