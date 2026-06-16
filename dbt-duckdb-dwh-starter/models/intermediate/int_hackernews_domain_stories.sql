with linked_domains as (
    select
        source_domain_id as domain_id,
        source_domain_name as domain,
        source_domain_reversed as host_reversed,
        source_pagerank_rank as pagerank_rank
    from {{ ref('int_domain_edges_with_rank') }}

    union

    select
        target_domain_id as domain_id,
        target_domain_name as domain,
        target_host_reversed as host_reversed,
        target_pagerank_rank as pagerank_rank
    from {{ ref('int_domain_edges_with_rank') }}
),

domains as (
    select distinct
        domain_id,
        domain,
        host_reversed,
        pagerank_rank
    from linked_domains
),

stories as (
    select *
    from {{ ref('stg_hackernews__stories') }}
),

matches as (
    select
        domains.domain_id,
        domains.domain,
        domains.host_reversed,
        domains.pagerank_rank,
        stories.story_id,
        stories.title,
        stories.url,
        stories.url_host,
        stories.score,
        stories.author,
        stories.created_at,
        case
            when stories.url_host = domains.domain
                or ends_with(stories.url_host, '.' || domains.domain)
                then 'url_host'
            when contains(lower(coalesce(stories.title, '')), lower(domains.domain))
                then 'title'
            else 'text'
        end as matched_on
    from stories
    inner join domains
        on stories.url_host = domains.domain
        or ends_with(stories.url_host, '.' || domains.domain)
        or contains(lower(coalesce(stories.title, '')), lower(domains.domain))
        or contains(lower(coalesce(stories.text, '')), lower(domains.domain))
)

select *
from matches
qualify row_number() over (
    partition by domain
    order by score desc nulls last, created_at desc nulls last, story_id desc
) <= {{ var('hackernews_max_stories_per_domain') }}
