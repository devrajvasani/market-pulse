"""Critical tests: backend factory, the DuckDB local twin (Bronze + Gold), and Athena."""

import duckdb
import pandas as pd

from config import settings
from src.backends.athena_backend import AthenaBackend
from src.backends.duckdb_backend import DuckDBBackend
from src.backends.query_engine import QueryEngine


# ── Backend factory (APP_ENV picks the implementation) ──────────────────────────
def test_local_query_engine_is_duckdb(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    engine = settings.get_query_engine()
    assert isinstance(engine, DuckDBBackend)
    assert isinstance(engine, QueryEngine)


def test_aws_query_engine_is_athena(monkeypatch):
    monkeypatch.setenv("APP_ENV", "aws")
    engine = settings.get_query_engine()
    assert isinstance(engine, AthenaBackend)
    assert isinstance(engine, QueryEngine)


def test_local_gold_query_engine_is_duckdb(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    engine = settings.get_gold_query_engine()
    assert isinstance(engine, DuckDBBackend)
    assert isinstance(engine, QueryEngine)


def test_aws_gold_query_engine_is_athena(monkeypatch):
    monkeypatch.setenv("APP_ENV", "aws")
    engine = settings.get_gold_query_engine()
    assert isinstance(engine, AthenaBackend)
    assert isinstance(engine, QueryEngine)


# ── settings: Athena database / workgroup defaults ──────────────────────────────
def test_athena_settings_defaults(monkeypatch):
    monkeypatch.delenv("ATHENA_DATABASE", raising=False)
    monkeypatch.delenv("ATHENA_WORKGROUP", raising=False)
    monkeypatch.setenv("ENVIRONMENT", "dev")
    assert settings.athena_database() == "marketpulse_dev"
    assert settings.athena_workgroup() == "marketpulse-dev-athena-analytics"


# ── DuckDB local twin (real DuckDB over local Parquet) ──────────────────────────
def _write_partition(root, snapshot_date, snapshot_hour, frame):
    """Write `frame` as Parquet under a Hive-style snapshot_date/snapshot_hour path."""
    part = root / f"snapshot_date={snapshot_date}" / f"snapshot_hour={snapshot_hour}"
    part.mkdir(parents=True)
    frame.to_parquet(part / "data.parquet", index=False)


def test_duckdb_backend_counts_local_parquet(tmp_path):
    _write_partition(
        tmp_path,
        "2026-06-05",
        "22",
        pd.DataFrame({"coin_id": ["bitcoin", "ethereum"], "current_price": [100.0, 50.0]}),
    )
    rows = DuckDBBackend(bronze_location=str(tmp_path)).run_sql(
        "SELECT count(*) AS row_count FROM bronze_prices"
    )
    assert rows == [{"row_count": 2}]


def test_duckdb_backend_reads_partition_columns_as_strings(tmp_path):
    _write_partition(
        tmp_path,
        "2026-06-05",
        "22",
        pd.DataFrame({"coin_id": ["bitcoin"], "current_price": [100.0]}),
    )
    rows = DuckDBBackend(bronze_location=str(tmp_path)).run_sql(
        "SELECT coin_id, snapshot_date, snapshot_hour FROM bronze_prices"
    )
    # partition values stay strings (matching the Glue table's string partition types)
    assert rows == [{"coin_id": "bitcoin", "snapshot_date": "2026-06-05", "snapshot_hour": "22"}]


def test_duckdb_backend_accumulates_hourly_partitions(tmp_path):
    for hour in ("22", "23"):
        _write_partition(
            tmp_path,
            "2026-06-05",
            hour,
            pd.DataFrame({"coin_id": ["bitcoin", "ethereum"], "current_price": [1.0, 2.0]}),
        )
    rows = DuckDBBackend(bronze_location=str(tmp_path)).run_sql(
        "SELECT count(*) AS row_count FROM bronze_prices"
    )
    assert rows == [{"row_count": 4}]  # 2 hours x 2 coins


def test_duckdb_backend_reads_gold_database(tmp_path):
    # Gold mode: query the dbt-materialised marts by name from a DuckDB database file.
    db_path = tmp_path / "marketpulse.duckdb"
    con = duckdb.connect(str(db_path))
    con.execute(
        "CREATE TABLE gold_latest_price AS SELECT 'BTC-USD' AS product_id, 100.5 AS latest_price"
    )
    con.close()
    rows = DuckDBBackend(database_path=str(db_path)).run_sql(
        "SELECT product_id, latest_price FROM gold_latest_price"
    )
    assert rows == [{"product_id": "BTC-USD", "latest_price": 100.5}]


# ── Athena cloud backend (awswrangler mocked) ───────────────────────────────────
def test_athena_backend_runs_query(monkeypatch):
    captured: dict = {}

    def _fake_read_sql_query(sql, **kwargs):
        captured["sql"] = sql
        captured.update(kwargs)
        return pd.DataFrame([{"row_count": 26}])

    monkeypatch.setattr(
        "src.backends.athena_backend.wr.athena.read_sql_query", _fake_read_sql_query
    )
    rows = AthenaBackend(database="marketpulse_dev", workgroup="wg-test").run_sql(
        "SELECT count(*) AS row_count FROM bronze_prices"
    )
    assert rows == [{"row_count": 26}]
    assert captured["database"] == "marketpulse_dev"
    assert captured["workgroup"] == "wg-test"
