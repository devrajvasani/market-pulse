-- Latest snapshot per coin with its short-horizon price moves + all-time-high reference
-- (ath = the all-time-high price, ath_change_pct = % the current price sits below that ATH),
-- ranked. change_rank_24h
-- = 1 is the biggest 24h gainer; the largest rank is the biggest loser. A point-in-time
-- "what's moving now" serving table. Columns are listed explicitly (no `*, expr`) so the
-- same SQL parses on both DuckDB and Athena (Trino). The rank uses `nulls last` (CoinGecko
-- can return a null 24h change; without this, Trino sorts nulls FIRST and a null-change coin
-- would falsely rank #1 -- DuckDB sorts them last) plus a `coin_id` tiebreaker, so the rank
-- is deterministic and identical on both engines. Portable SQL.

with ranked as (

    select
        coin_id,
        coin_symbol,
        coin_name,
        current_price,
        market_cap,
        market_cap_rank,
        price_change_pct_1h,
        price_change_pct_24h,
        price_change_pct_7d,
        ath,
        ath_change_pct,
        snapshot_date,
        snapshot_hour,
        row_number() over (
            partition by coin_id order by snapshot_date desc, snapshot_hour desc
        ) as _row_num
    from {{ ref("silver_prices") }}

),

-- Latest row per coin via the portable subquery + WHERE pattern (Athena has no QUALIFY).
latest as (

    select
        coin_id,
        coin_symbol,
        coin_name,
        current_price,
        market_cap,
        market_cap_rank,
        price_change_pct_1h,
        price_change_pct_24h,
        price_change_pct_7d,
        ath,
        ath_change_pct,
        snapshot_date,
        snapshot_hour
    from ranked
    where _row_num = 1

)

select
    coin_id,
    coin_symbol,
    coin_name,
    current_price,
    market_cap,
    market_cap_rank,
    price_change_pct_1h,
    price_change_pct_24h,
    price_change_pct_7d,
    snapshot_date,
    snapshot_hour,
    row_number() over (
        order by price_change_pct_24h desc nulls last, coin_id
    ) as change_rank_24h
from latest
