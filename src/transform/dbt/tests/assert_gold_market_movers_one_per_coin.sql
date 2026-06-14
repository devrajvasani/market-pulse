-- Completeness: gold_market_movers is the latest snapshot per coin, so it must hold
-- exactly one row per coin present in Silver. Returns rows (-> test fails) if any coin
-- is dropped or duplicated -- a silent loss the uniqueness/range tests alone wouldn't
-- catch. Same row-count-parity pattern as assert_gold_marts_match_ohlc.sql.
with movers as (
    select count(*) as n from {{ ref("gold_market_movers") }}
),

silver_coins as (
    select count(distinct coin_id) as n from {{ ref("silver_prices") }}
)

select
    movers.n as mover_rows,
    silver_coins.n as silver_coin_count
from movers
cross join silver_coins
where movers.n <> silver_coins.n
