with vertices as (
    select
        domain_id,
        host_reversed,
        domain_name
    from {{ ref('stg_commoncrawl__domain_vertices') }}
),

target_domains as (
    select
        domain_id
    from {{ ref('stg_commoncrawl__domain_vertices') }}
    where is_target_domain
),

edges as (
    select
        from_id,
        to_id
    from {{ ref('stg_commoncrawl__domain_edges') }}
    inner join target_domains on
        -- filter down to only edges for our target domains
        to_id = target_domains.domain_id
)

select
    edges.from_id as source_domain_id,
    source_domains.host_reversed as source_domain_reversed,
    source_domains.domain_name as source_domain_name,

    edges.to_id as target_domain_id,
    target_domains.host_reversed as target_host_reversed,
    target_domains.domain_name as target_domain_name,

from edges
left join vertices as source_domains on
    edges.from_id = source_domains.domain_id
left join vertices as target_domains on
    edges.to_id = target_domains.domain_id
