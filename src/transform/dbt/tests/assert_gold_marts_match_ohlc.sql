-- Row-count sanity (catch silent data loss) for the Gold layer. gold_price_moving_avg
-- and gold_volatility are strictly 1:1 derivations of gold_daily_ohlc (they select from
-- it with no filtering), so each must have exactly the same row count. Returns rows ->
-- the test fails. Mirrors assert_silver_rowcount_matches_bronze.sql for the Gold layer.
with ohlc as (
    select count(*) as n from {{ ref("gold_daily_ohlc") }}
),

moving_avg as (
    select count(*) as n from {{ ref("gold_price_moving_avg") }}
),

volatility as (
    select count(*) as n from {{ ref("gold_volatility") }}
)

select
    ohlc.n as ohlc_rows,
    moving_avg.n as moving_avg_rows,
    volatility.n as volatility_rows
from ohlc
cross join moving_avg
cross join volatility
where ohlc.n <> moving_avg.n or ohlc.n <> volatility.n
