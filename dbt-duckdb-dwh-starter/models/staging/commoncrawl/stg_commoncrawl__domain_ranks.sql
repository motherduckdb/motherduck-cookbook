with source as (
    select *
    from {{ source('commoncrawl', 'domain_ranks') }}
)

select
    "#harmonicc_pos"::bigint as harmonicc_rank,
    "#harmonicc_val"::double as harmonicc_value,
    "#pr_pos"::bigint as pagerank_rank,
    "#pr_val"::double as pagerank_value,
    "#host_rev"::varchar as host_reversed,
    array_to_string(list_reverse(string_split("#host_rev"::varchar, '.')), '.') as domain,
    "#n_hosts"::bigint as host_count
from source
