"""Critical, offline tests for the Bronze -> Silver transform logic (Section L.6).

These run on DuckDB against a tiny Hive-partitioned Parquet fixture — no S3, no
LocalStack, no dbt server, no network — and assert the two guarantees the
``silver_prices`` model makes:

1. the raw ISO-string timestamps become real ``timestamp`` values (handling both
   the ``+00:00`` form from ingestion and the ``Z`` form from CoinGecko); and
2. the natural key ``(coin_id, snapshot_date, snapshot_hour)`` is deduplicated to
   the latest capture, **idempotently** (re-running yields the identical result).

The dbt model is authoritative; this mirrors its portable dedup + typing core
(``read_parquet`` + ``cast`` + ``qualify row_number()``) to prove it on
representative rows. The richer data-quality tests (uniqueness, range, row-count
sanity) run inside ``dbt build`` against the real lake.
"""

from __future__ import annotations

import duckdb
import pandas as pd

# Portable core of models/silver/silver_prices.sql: type the timestamps and keep
# one row per natural key, latest ingested_at wins. {glob} is filled at runtime.
SILVER_SQL = """
    with typed as (
        select
            coin_id,
            cast(current_price as double) as current_price,
            cast(source_updated_at as timestamptz) at time zone 'UTC' as source_updated_at,
            cast(ingested_at as timestamptz) at time zone 'UTC' as ingested_at,
            cast(snapshot_date as date) as snapshot_date,
            cast(snapshot_hour as integer) as snapshot_hour
        from read_parquet('{glob}', hive_partitioning = true, hive_types_autocast = 0)
    ),
    deduped as (
        select typed.*,
            row_number() over (
                partition by coin_id, snapshot_date, snapshot_hour
                order by ingested_at desc
            ) as _row_num
        from typed
    )
    select coin_id, current_price, source_updated_at, ingested_at, snapshot_date, snapshot_hour
    from deduped
    where _row_num = 1
"""


def _write_bronze_fixture(root) -> str:
    """Write a Hive-partitioned Parquet fixture mimicking Bronze; return the glob."""
    frame = pd.DataFrame(
        [
            # bitcoin @ (2026-06-08, hour 12): an older + a newer capture (newer wins).
            {
                "coin_id": "bitcoin",
                "current_price": 50000.0,
                "ingested_at": "2026-06-08T12:00:00.000000+00:00",
                "source_updated_at": "2026-06-08T11:59:00.000Z",  # CoinGecko 'Z' form
                "snapshot_date": "2026-06-08",
                "snapshot_hour": "12",
            },
            {
                "coin_id": "bitcoin",
                "current_price": 50500.0,
                "ingested_at": "2026-06-08T12:05:00.000000+00:00",  # later -> should win
                "source_updated_at": "2026-06-08T12:04:00.000Z",
                "snapshot_date": "2026-06-08",
                "snapshot_hour": "12",
            },
            {
                "coin_id": "ethereum",
                "current_price": 3000.0,
                "ingested_at": "2026-06-08T12:00:00.000000+00:00",
                "source_updated_at": "2026-06-08T11:58:00.000Z",
                "snapshot_date": "2026-06-08",
                "snapshot_hour": "12",
            },
        ]
    )
    frame.to_parquet(root, partition_cols=["snapshot_date", "snapshot_hour"], index=False)
    return f"{str(root).replace(chr(92), '/')}/**/*.parquet"


def test_silver_dedup_keeps_latest_and_types_timestamps(tmp_path):
    glob = _write_bronze_fixture(tmp_path)
    result = duckdb.connect().execute(SILVER_SQL.format(glob=glob)).df()

    # Deduplicated to one row per natural key: 3 Bronze rows -> 2 Silver rows.
    assert len(result) == 2
    by_coin = result.set_index("coin_id")
    # The later ingested_at won for bitcoin.
    assert by_coin.loc["bitcoin", "current_price"] == 50500.0
    assert by_coin.loc["ethereum", "current_price"] == 3000.0

    # ISO strings (both +00:00 and Z forms) became real timestamps.
    assert pd.api.types.is_datetime64_any_dtype(result["ingested_at"])
    assert pd.api.types.is_datetime64_any_dtype(result["source_updated_at"])


def test_silver_transform_is_idempotent(tmp_path):
    glob = _write_bronze_fixture(tmp_path)
    sql = SILVER_SQL.format(glob=glob)
    con = duckdb.connect()

    first = con.execute(sql).df().sort_values("coin_id").reset_index(drop=True)
    second = con.execute(sql).df().sort_values("coin_id").reset_index(drop=True)

    pd.testing.assert_frame_equal(first, second)
