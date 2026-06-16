{% macro commoncrawl_sql_string(value) -%}
  '{{ value | replace("'", "''") }}'
{%- endmacro %}

{% macro commoncrawl_host_reversed_sql(domain_sql) -%}
  array_to_string(
    list_reverse(
      string_split(
        regexp_replace(
          regexp_replace(lower(trim({{ domain_sql }})), '^https?://', ''),
          '/.*$',
          ''
        ),
        '.'
      )
    ),
    '.'
  )
{%- endmacro %}

{% macro commoncrawl_domain_list() -%}
  {%- set configured = var('commoncrawl_domains', ['motherduck.com']) -%}
  {%- if configured is string -%}
    {%- set configured = configured.split(',') -%}
  {%- endif -%}
  {%- set domains = [] -%}
  {%- for domain in configured -%}
    {%- set cleaned = domain | string | trim -%}
    {%- if cleaned -%}
      {%- do domains.append(cleaned) -%}
    {%- endif -%}
  {%- endfor -%}
  {{ return(domains) }}
{%- endmacro %}

{% macro commoncrawl_configured_domains_sql() -%}
  {%- for domain in commoncrawl_domain_list() %}
    select {{ commoncrawl_sql_string(domain) }} as requested_domain
    {%- if not loop.last %}
    union all
    {%- endif -%}
  {%- endfor -%}
{%- endmacro %}

{% macro commoncrawl_var_int(name, default) -%}
  {{ return(var(name, default) | int) }}
{%- endmacro %}
