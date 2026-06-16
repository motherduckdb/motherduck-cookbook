select
    target_domain_name as target_domain,
    source_domain_name as from_domain,
    source_domain_reversed as from_host_reversed,
    source_pagerank_rank as from_pagerank_rank,
    target_domain_name as to_domain,
    target_host_reversed as to_host_reversed,
    target_pagerank_rank as to_pagerank_rank,
    source_domain_id as from_id,
    target_domain_id as to_id
from {{ ref('int_domain_edges_with_rank') }}
order by to_pagerank_rank nulls last, from_pagerank_rank nulls last
