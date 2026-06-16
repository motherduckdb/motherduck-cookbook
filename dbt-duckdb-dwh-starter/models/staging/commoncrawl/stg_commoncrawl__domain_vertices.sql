

with configured_domains as (
    {{ commoncrawl_configured_domains_sql() }}
),

target_domains as (
    select
        requested_domain,
        {{ commoncrawl_host_reversed_sql('requested_domain') }} as host_reversed
    from configured_domains
),

source as (
    select *
    from {{ source('commoncrawl', 'domain_vertices') }}
)

select
    source.column0::bigint as domain_id,
    source.column1::varchar as host_reversed,
    array_to_string(list_reverse(string_split(source.column1::varchar, '.')), '.') as domain_name,
    if(target_domains.requested_domain is not null, true, false) as is_target_domain,
    source.column2::bigint as host_count
from source
left join target_domains
    on source.column1::varchar = target_domains.host_reversed
order by source.column1
