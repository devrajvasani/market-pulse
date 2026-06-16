-- Near-real-time "latest price" per product: the most recent trade for each product_id
-- (BTC-USD, ETH-USD, ...). This is the streaming serving mart (Stage 4 DoD) -- the RAG
-- assistant / dashboards read it for "what's the price right now". One row per product.
-- Portable subquery + WHERE (no QUALIFY); event_time is the tiebreaker, ingested_at breaks
-- exact event-time ties deterministically.

with ranked as (

    select
        product_id,
        price,
        trade_size,
        side,
        event_time,
        ingested_at,
        row_number() over (
            partition by product_id
            order by event_time desc, ingested_at desc
        ) as _row_num
    from {{ ref("silver_trades") }}

)

select
    product_id,
    price as latest_price,
    trade_size as latest_trade_size,
    side as latest_side,
    event_time as last_trade_at,
    ingested_at as last_ingested_at
from ranked
where _row_num = 1
