-- Bronze -> Silver for streaming trades: type the raw NDJSON values and keep one row per
-- trade_id. Kinesis is at-least-once and a retried consumer batch can re-land a trade, so
-- the same trade_id may appear in more than one Bronze file -- dedup keeps the latest
-- capture. Portable SQL (subquery + WHERE, not QUALIFY); reuses the parse_iso_timestamp
-- dispatch macro so the same model runs on DuckDB and Athena.

with bronze as (

    select * from {{ source("bronze", "trades") }}

),

typed as (

    select
        cast(trade_id as bigint) as trade_id,
        product_id,
        side,
        cast(price as double) as price,
        cast(size as double) as trade_size,
        {{ parse_iso_timestamp("event_time") }} as event_time,
        {{ parse_iso_timestamp("ingested_at") }} as ingested_at,
        source,
        cast(snapshot_date as date) as snapshot_date,
        cast(snapshot_hour as integer) as snapshot_hour
    from bronze

),

deduped as (

    select
        typed.*,
        row_number() over (
            partition by trade_id
            order by ingested_at desc
        ) as _row_num
    from typed

)

select
    trade_id,
    product_id,
    side,
    price,
    trade_size,
    event_time,
    ingested_at,
    source,
    snapshot_date,
    snapshot_hour
from deduped
where _row_num = 1
