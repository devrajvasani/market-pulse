-- Row-count sanity (catch silent data loss): Silver must hold exactly one row per
-- DISTINCT Bronze natural key. Returns rows (-> test fails) if dedup dropped real
-- data or the counts diverge for any reason. `||` string concat works on both
-- DuckDB and Athena (Trino); keys are non-null (enforced by the not_null tests).
with bronze_keys as (
    select count(distinct coin_id || '|' || snapshot_date || '|' || snapshot_hour) as n
    from {{ source("bronze", "prices") }}
),

silver_rows as (
    select count(*) as n from {{ ref("silver_prices") }}
)

select
    bronze_keys.n as bronze_distinct_keys,
    silver_rows.n as silver_rows
from bronze_keys
cross join silver_rows
where bronze_keys.n <> silver_rows.n
