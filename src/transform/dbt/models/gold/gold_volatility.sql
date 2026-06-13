-- Rolling 7-day volatility per coin: the sample standard deviation of daily returns,
-- where daily_return = close / previous_close - 1. `nullif(..., 0)` guards against a
-- divide-by-zero; the first day per coin has no previous close so its return (and any
-- window with < 2 returns) is null -> volatility is null until enough history exists.
-- `returns_in_window` exposes how many non-null returns fed each value (mirrors
-- days_in_window in the moving-average mart) so early, thin values are distinguishable
-- from mature full-window ones. Portable window SQL (DuckDB & Athena).

with daily_returns as (

    select
        coin_id,
        snapshot_date,
        close_price,
        close_price / nullif(
            lag(close_price) over (partition by coin_id order by snapshot_date), 0
        ) - 1 as daily_return
    from {{ ref("gold_daily_ohlc") }}

)

select
    coin_id,
    snapshot_date,
    close_price,
    daily_return,
    stddev_samp(daily_return) over (
        partition by coin_id
        order by snapshot_date
        rows between 6 preceding and current row
    ) as volatility_7d,
    count(daily_return) over (
        partition by coin_id
        order by snapshot_date
        rows between 6 preceding and current row
    ) as returns_in_window
from daily_returns
