-- 7-day simple moving average of the daily close, per coin. The window is the 7 most
-- recent AVAILABLE daily closes (rows-based) -- with daily data and possible gaps
-- that means "the last 7 observed days". `days_in_window` exposes how many closes
-- actually fed the average, so consumers know when it is a full 7. Portable window
-- SQL (DuckDB & Athena).

select
    coin_id,
    snapshot_date,
    close_price,
    avg(close_price) over (
        partition by coin_id
        order by snapshot_date
        rows between 6 preceding and current row
    ) as close_sma_7d,
    count(*) over (
        partition by coin_id
        order by snapshot_date
        rows between 6 preceding and current row
    ) as days_in_window
from {{ ref("gold_daily_ohlc") }}
