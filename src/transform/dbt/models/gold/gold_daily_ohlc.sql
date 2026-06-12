-- Daily OHLC per coin, derived from the hourly Silver snapshots. Bronze/Silver are
-- point-in-time HOURLY snapshots (not ticks), so OHLC is built from the day's
-- current_price series: open = first snapshot of the day, close = last snapshot,
-- high/low = max/min across the day. IMPORTANT: total_volume / high_24h / low_24h
-- from the API are 24h ROLLING figures, not per-hour increments -- summing them
-- would massively overcount -- so "volume" here is the day's CLOSING 24h volume
-- (the last snapshot's total_volume), named explicitly to avoid that confusion.
-- Portable SQL only (row_number + conditional aggregation) -> runs on DuckDB & Athena.

with snapshots as (

    select
        coin_id,
        coin_symbol,
        coin_name,
        quote_currency,
        snapshot_date,
        snapshot_hour,
        current_price,
        total_volume,
        row_number() over (
            partition by coin_id, snapshot_date order by snapshot_hour asc
        ) as hour_asc,
        row_number() over (
            partition by coin_id, snapshot_date order by snapshot_hour desc
        ) as hour_desc
    from {{ ref("silver_prices") }}

)

select
    coin_id,
    max(coin_symbol) as coin_symbol,
    max(coin_name) as coin_name,
    max(quote_currency) as quote_currency,
    snapshot_date,
    max(case when hour_asc = 1 then current_price end) as open_price,
    max(current_price) as high_price,
    min(current_price) as low_price,
    max(case when hour_desc = 1 then current_price end) as close_price,
    max(case when hour_desc = 1 then total_volume end) as close_total_volume_24h,
    count(*) as snapshots_in_day
from snapshots
group by coin_id, snapshot_date
