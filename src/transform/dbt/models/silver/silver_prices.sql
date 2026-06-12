-- Bronze -> Silver: cast the raw ISO-string timestamps to real timestamps and
-- collapse to one row per natural key (coin_id + snapshot_date + snapshot_hour),
-- keeping the latest capture. Bronze is never modified; Silver is fully
-- re-derivable from it (medallion rule). Idempotent via an incremental Iceberg MERGE
-- on the natural key: re-runs upsert new/changed rows (no duplicates) instead of a full
-- rebuild; the first run (no table yet) builds in full.
-- Dedup uses the portable subquery + WHERE pattern (NOT QUALIFY -- Athena engine v3
-- does not support QUALIFY); columns are listed explicitly since portable SQL has no
-- `* EXCEPT`. Same SQL runs on both DuckDB and Athena.

with bronze as (

    select * from {{ source("bronze", "prices") }}
    {% if is_incremental() %}
    -- Incremental: only reprocess snapshots from the latest date already in Silver
    -- onward; the MERGE on the natural key upserts them (no duplicates). Cast the raw
    -- string partition to date to compare against Silver's typed snapshot_date.
    where cast(snapshot_date as date)
        >= (select coalesce(max(snapshot_date), date '1900-01-01') from {{ this }})
    {% endif %}

),

typed as (

    select
        coin_id,
        coin_symbol,
        coin_name,
        quote_currency,
        current_price,
        market_cap,
        market_cap_rank,
        fully_diluted_valuation,
        total_volume,
        high_24h,
        low_24h,
        price_change_24h,
        price_change_pct_24h,
        price_change_pct_1h,
        price_change_pct_7d,
        market_cap_change_24h,
        market_cap_change_pct_24h,
        circulating_supply,
        total_supply,
        max_supply,
        ath,
        ath_change_pct,
        {{ parse_iso_timestamp("source_updated_at") }} as source_updated_at,
        {{ parse_iso_timestamp("ingested_at") }} as ingested_at,
        cast(snapshot_date as date) as snapshot_date,
        cast(snapshot_hour as integer) as snapshot_hour
    from bronze

),

deduped as (

    select
        typed.*,
        row_number() over (
            partition by coin_id, snapshot_date, snapshot_hour
            order by ingested_at desc
        ) as _row_num
    from typed

)

select
    coin_id,
    coin_symbol,
    coin_name,
    quote_currency,
    current_price,
    market_cap,
    market_cap_rank,
    fully_diluted_valuation,
    total_volume,
    high_24h,
    low_24h,
    price_change_24h,
    price_change_pct_24h,
    price_change_pct_1h,
    price_change_pct_7d,
    market_cap_change_24h,
    market_cap_change_pct_24h,
    circulating_supply,
    total_supply,
    max_supply,
    ath,
    ath_change_pct,
    source_updated_at,
    ingested_at,
    snapshot_date,
    snapshot_hour
from deduped
where _row_num = 1
