"""Critical, offline test for the Gold daily-OHLC transform logic (Section L.6).

Runs on DuckDB against a tiny in-memory Silver-shaped fixture (no S3, no dbt
server, no network) and asserts the OHLC semantics the ``gold_daily_ohlc`` model
guarantees for hourly-snapshot data: open = first snapshot of the day, close =
last, high/low = max/min across the day, and volume = the day's CLOSING 24h
figure (the last snapshot's ``total_volume``) — never a sum.

The dbt model is authoritative; this mirrors its portable core (row_number +
conditional aggregation) to prove the semantics on representative rows.
"""

from __future__ import annotations

import duckdb

# Portable core of models/gold/gold_daily_ohlc.sql, run against an in-memory Silver.
GOLD_OHLC_SQL = """
with snapshots as (
    select
        coin_id, snapshot_date, snapshot_hour, current_price, total_volume,
        row_number() over (
            partition by coin_id, snapshot_date order by snapshot_hour asc
        ) as hour_asc,
        row_number() over (
            partition by coin_id, snapshot_date order by snapshot_hour desc
        ) as hour_desc
    from silver
)
select
    coin_id,
    snapshot_date,
    max(case when hour_asc = 1 then current_price end) as open_price,
    max(current_price) as high_price,
    min(current_price) as low_price,
    max(case when hour_desc = 1 then current_price end) as close_price,
    max(case when hour_desc = 1 then total_volume end) as close_total_volume_24h,
    count(*) as snapshots_in_day
from snapshots
group by coin_id, snapshot_date
"""


def test_gold_daily_ohlc_semantics():
    con = duckdb.connect()
    # bitcoin, one day, three hourly snapshots: prices 100 -> 120 -> 90 across hours
    # 0,1,2; total_volume is a rolling 24h figure that happens to end at 12.
    con.execute(
        """
        create table silver as select * from (values
            ('bitcoin', date '2026-06-08', 0, 100.0, 10.0),
            ('bitcoin', date '2026-06-08', 1, 120.0, 11.0),
            ('bitcoin', date '2026-06-08', 2,  90.0, 12.0)
        ) as t(coin_id, snapshot_date, snapshot_hour, current_price, total_volume)
        """
    )
    row = con.execute(GOLD_OHLC_SQL).df().iloc[0]

    assert row["open_price"] == 100.0  # first snapshot of the day
    assert row["high_price"] == 120.0  # max across the day
    assert row["low_price"] == 90.0  # min across the day
    assert row["close_price"] == 90.0  # last snapshot of the day
    assert row["close_total_volume_24h"] == 12.0  # closing 24h volume, NOT 10+11+12
    assert row["snapshots_in_day"] == 3
