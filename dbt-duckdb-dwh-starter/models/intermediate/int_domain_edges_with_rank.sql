select
    edges.*,
    source_ranks.host_count as source_host_count,
    source_ranks.harmonicc_rank as source_harmonicc_rank,
    source_ranks.harmonicc_value as source_harmonicc_value,
    source_ranks.pagerank_rank as source_pagerank_rank,
    source_ranks.pagerank_value as source_pagerank_value,
    target_ranks.host_count as target_host_count,
    target_ranks.harmonicc_rank as target_harmonicc_rank,
    target_ranks.harmonicc_value as target_harmonicc_value,
    target_ranks.pagerank_rank as target_pagerank_rank,
    target_ranks.pagerank_value as target_pagerank_value
from {{ ref('int_domain_edges') }} as edges
left join {{ ref('stg_commoncrawl__domain_ranks') }} as source_ranks
    on edges.source_domain_reversed = source_ranks.host_reversed
left join {{ ref('stg_commoncrawl__domain_ranks') }} as target_ranks
    on edges.target_host_reversed = target_ranks.host_reversed
