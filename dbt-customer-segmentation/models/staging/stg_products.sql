select
    product_id::integer as product_id,
    manufacturer::varchar as manufacturer,
    department::varchar as department,
    brand::varchar as brand,
    commodity_desc::varchar as commodity_desc,
    sub_commodity_desc::varchar as sub_commodity_desc,
    case when lower(brand::varchar) = 'private' then 1 else 0 end as is_private_label
from {{ source('retail_raw', 'raw_products') }}
where product_id is not null
