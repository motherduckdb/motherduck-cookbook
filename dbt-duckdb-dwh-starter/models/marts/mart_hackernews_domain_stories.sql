select
    domain,
    host_reversed,
    pagerank_rank,
    matched_on,
    story_id,
    title,
    url,
    url_host,
    score,
    author,
    created_at
from {{ ref('int_hackernews_domain_stories') }}
order by domain, score desc nulls last, created_at desc nulls last
