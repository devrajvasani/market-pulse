"""Offline test for the Silver trades transform logic (Section L.6).

DuckDB on an in-memory trades fixture (no S3, no LocalStack). Asserts dedup by trade_id
(latest ingested_at wins, since at-least-once delivery can repeat a trade) and timestamp
typing — the portable core of models/silver/silver_trades.sql.
"""

from __future__ import annotations

import duckdb
import pandas as pd

SILVER_TRADES_SQL = """
    with typed as (
        select
            cast(trade_id as bigint) as trade_id,
            product_id,
            cast(price as double) as price,
            cast(ingested_at as timestamptz) at time zone 'UTC' as ingested_at
        from trades
    ),
    deduped as (
        select typed.*,
            row_number() over (partition by trade_id order by ingested_at desc) as _row_num
        from typed
    )
    select trade_id, product_id, price, ingested_at from deduped where _row_num = 1
"""


def test_silver_trades_dedups_by_trade_id_keeping_latest():
    con = duckdb.connect()
    con.execute(
        """
        create table trades as select * from (values
            (1001, 'BTC-USD', 63990.0, '2026-06-14T18:00:00.000000+00:00'),
            (1001, 'BTC-USD', 64000.0, '2026-06-14T18:00:05.000000+00:00'),  -- retried, later wins
            (1002, 'ETH-USD',  1662.0, '2026-06-14T18:00:01.000000+00:00')
        ) as t(trade_id, product_id, price, ingested_at)
        """
    )
    rows = con.execute(SILVER_TRADES_SQL).df().sort_values("trade_id").reset_index(drop=True)

    assert len(rows) == 2  # 3 raw rows -> 2 after dedup by trade_id
    # The later-ingested duplicate of trade 1001 wins.
    assert rows.loc[rows.trade_id == 1001, "price"].iloc[0] == 64000.0
    assert pd.api.types.is_datetime64_any_dtype(rows["ingested_at"])
