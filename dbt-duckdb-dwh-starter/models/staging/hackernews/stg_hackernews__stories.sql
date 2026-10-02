{{ config(
    pre_hook="
        ATTACH IF NOT EXISTS 'md:_share/hacker_news/daa0cc99-d20c-4f5c-abd5-f7b22a1a1da9' AS hacker_news;
        "
) }}

select
    id::bigint as story_id,
    title as title,
    url as url,
    regexp_replace(
        regexp_replace(lower(coalesce(url, '')), '^https?://', ''),
        '/.*$',
        ''
    ) as url_host,
    score::bigint as score,
    "by" as author,
    timestamp::timestamp as created_at,
    text,
    type as item_type
from {{ source('hackernews', 'hacker_news') }}
where type = 'story'
and timestamp >= '2020-01-01'
