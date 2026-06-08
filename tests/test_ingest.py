"""Critical tests for batch ingestion: fetch, transform, key read, idempotent write."""

import json
from datetime import UTC, datetime

import boto3
import pandas as pd
import pytest
from moto import mock_aws

from config import settings
from src.common.errors import IngestionError
from src.ingestion.batch import ingest
from src.ingestion.batch.coingecko import CoinGeckoClient

# Bitcoin is fully populated; Ethereum deliberately omits several nullable fields
# (max_supply, ath, 1h/7d moves, supplies, rank) to exercise null-safety — the API
# marks most numerics nullable.
_SAMPLE = [
    {
        "id": "bitcoin",
        "symbol": "btc",
        "name": "Bitcoin",
        "current_price": 100.0,
        "market_cap": 10,
        "market_cap_rank": 1,
        "fully_diluted_valuation": 12,
        "total_volume": 5,
        "high_24h": 110.0,
        "low_24h": 90.0,
        "price_change_24h": -0.5,
        "price_change_percentage_24h": -0.5,
        "price_change_percentage_1h_in_currency": 0.1,
        "price_change_percentage_7d_in_currency": 3.3,
        "market_cap_change_24h": -1.0,
        "market_cap_change_percentage_24h": -0.4,
        "circulating_supply": 19.0,
        "total_supply": 19.0,
        "max_supply": 21.0,
        "ath": 200.0,
        "ath_change_percentage": -50.0,
        "last_updated": "2026-06-05T00:00:00Z",
    },
    {
        "id": "ethereum",
        "symbol": "eth",
        "name": "Ethereum",
        "current_price": 50.0,
        "market_cap": 8,
        "total_volume": 4,
        "price_change_percentage_24h": -4.2,
        "last_updated": "2026-06-05T00:00:00Z",
    },
]

_CAPTURED_AT = datetime(2026, 6, 5, 21, 30, tzinfo=UTC)


class _FakeUrlopen:
    """Stand-in for urllib.request.urlopen's context manager (returns canned JSON bytes)."""

    def __init__(self, payload):
        self._payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False

    def read(self):
        return self._payload


# ── CoinGecko client ──────────────────────────────────────────────────────────
def test_client_rejects_empty_key():
    with pytest.raises(IngestionError):
        CoinGeckoClient("")


def test_fetch_markets_parses_payload(monkeypatch):
    monkeypatch.setattr(
        "src.ingestion.batch.coingecko.urllib.request.urlopen",
        lambda *a, **k: _FakeUrlopen(_SAMPLE),
    )
    data = CoinGeckoClient("CG-test").fetch_markets(["bitcoin", "ethereum"])
    assert [r["symbol"] for r in data] == ["btc", "eth"]


def test_fetch_markets_requests_multi_window(monkeypatch):
    seen: dict = {}

    def _capture(request, *a, **k):
        seen["url"] = request.full_url
        return _FakeUrlopen(_SAMPLE)

    monkeypatch.setattr("src.ingestion.batch.coingecko.urllib.request.urlopen", _capture)
    CoinGeckoClient("CG-test").fetch_markets(["bitcoin"])
    # 1h,24h,7d requested (comma url-encoded) -> unlocks the 1h & 7d change columns
    assert "price_change_percentage=1h%2C24h%2C7d" in seen["url"]


def test_fetch_markets_rejects_bad_payload(monkeypatch):
    monkeypatch.setattr(
        "src.ingestion.batch.coingecko.urllib.request.urlopen", lambda *a, **k: _FakeUrlopen({})
    )
    with pytest.raises(IngestionError):
        CoinGeckoClient("CG-test").fetch_markets(["bitcoin"])


def test_fetch_markets_rejects_empty_list(monkeypatch):
    # a valid-but-empty result (e.g. all ids unknown) must fail loudly, not write 0 rows
    monkeypatch.setattr(
        "src.ingestion.batch.coingecko.urllib.request.urlopen", lambda *a, **k: _FakeUrlopen([])
    )
    with pytest.raises(IngestionError):
        CoinGeckoClient("CG-test").fetch_markets(["nonexistent-coin"])


def test_fetch_markets_fails_fast_on_4xx(monkeypatch):
    import urllib.error

    calls = {"n": 0}

    def _raise_401(*a, **k):
        calls["n"] += 1
        raise urllib.error.HTTPError("http://x", 401, "Unauthorized", {}, None)

    monkeypatch.setattr("src.ingestion.batch.coingecko.urllib.request.urlopen", _raise_401)
    with pytest.raises(IngestionError):
        CoinGeckoClient("CG-test").fetch_markets(["bitcoin"])
    assert calls["n"] == 1  # client error -> no retry


def test_fetch_top_markets_requests_top_n(monkeypatch):
    seen: dict = {}

    def _capture(request, *a, **k):
        seen["url"] = request.full_url
        return _FakeUrlopen(_SAMPLE)

    monkeypatch.setattr("src.ingestion.batch.coingecko.urllib.request.urlopen", _capture)
    CoinGeckoClient("CG-test").fetch_top_markets(100)
    # top-N by descending market cap, one page, with the 1h/24h/7d windows
    assert "order=market_cap_desc" in seen["url"]
    assert "per_page=100" in seen["url"]
    assert "page=1" in seen["url"]
    assert "price_change_percentage=1h%2C24h%2C7d" in seen["url"]


def test_fetch_top_markets_validates_count():
    client = CoinGeckoClient("CG-test")
    for bad in (0, 251, -5):  # outside CoinGecko's 1..250 per-page bound
        with pytest.raises(IngestionError):
            client.fetch_top_markets(bad)


# ── Transform ─────────────────────────────────────────────────────────────────
def test_to_dataframe_shape_and_naming():
    frame = ingest._to_dataframe(_SAMPLE, "usd", captured_at=_CAPTURED_AT)
    assert len(frame) == 2
    # full column contract = pinned columns + capture-time partitions, in order
    assert list(frame.columns) == [*ingest._BRONZE_DTYPES, *ingest._PARTITION_COLS]
    # capture-time partitions (hour is zero-padded)
    assert frame["snapshot_date"].iloc[0] == "2026-06-05"
    assert frame["snapshot_hour"].iloc[0] == "21"
    # production column names mapped from the source
    assert frame["current_price"].iloc[0] == 100.0
    assert frame["quote_currency"].iloc[0] == "usd"
    assert frame["coin_name"].iloc[0] == "Bitcoin"
    # widened analytics fields are projected
    assert frame["high_24h"].iloc[0] == 110.0
    assert frame["ath"].iloc[0] == 200.0
    assert frame["price_change_pct_7d"].iloc[0] == 3.3


def test_to_dataframe_is_null_safe_for_missing_fields():
    frame = ingest._to_dataframe(_SAMPLE, "usd", captured_at=_CAPTURED_AT)
    eth = frame[frame["coin_id"] == "ethereum"].iloc[0]
    # fields the API omitted for ETH must be null, not raise KeyError
    assert pd.isna(eth["max_supply"])
    assert pd.isna(eth["ath"])
    assert pd.isna(eth["price_change_pct_1h"])


# ── API key resolution ────────────────────────────────────────────────────────
def test_read_api_key_prefers_env(monkeypatch):
    monkeypatch.setenv("MARKETDATA_API_KEY", "CG-from-env")
    assert ingest._read_api_key() == "CG-from-env"


@mock_aws
def test_read_api_key_from_secrets_manager(monkeypatch):
    monkeypatch.delenv("MARKETDATA_API_KEY", raising=False)
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)  # use moto, not LocalStack
    monkeypatch.setenv("ENVIRONMENT", "dev")
    boto3.client("secretsmanager", region_name="us-east-1").create_secret(
        Name=settings.marketdata_secret_name(),
        SecretString=json.dumps({"api_key": "CG-from-secret"}),
    )
    assert ingest._read_api_key() == "CG-from-secret"


# ── Idempotent write ──────────────────────────────────────────────────────────
def test_run_ingestion_writes_idempotent_hourly_partition(monkeypatch):
    monkeypatch.setenv("MARKETDATA_API_KEY", "CG-test")
    monkeypatch.setenv("BRONZE_BUCKET", "marketpulse-dev-bucket-bronze-test")
    monkeypatch.setattr(
        CoinGeckoClient, "fetch_top_markets", lambda self, count, vs_currency="usd": _SAMPLE
    )
    captured_kwargs: dict = {}
    monkeypatch.setattr(
        "src.ingestion.batch.ingest.wr.s3.to_parquet",
        lambda **kwargs: captured_kwargs.update(kwargs),
    )

    summary = ingest.run_ingestion(captured_at=_CAPTURED_AT)

    assert summary["rows"] == 2
    assert summary["snapshot_date"] == "2026-06-05"
    assert summary["snapshot_hour"] == "21"
    # idempotency-by-design: replace only this hour's partition, no appends
    assert captured_kwargs["mode"] == "overwrite_partitions"
    assert captured_kwargs["partition_cols"] == ["snapshot_date", "snapshot_hour"]
    assert captured_kwargs["path"] == "s3://marketpulse-dev-bucket-bronze-test/prices/"
    # column types are pinned so an all-null partition can't break the Glue table
    assert captured_kwargs["dtype"] == ingest._BRONZE_DTYPES
    assert captured_kwargs["dtype"]["market_cap_rank"] == "bigint"


def test_run_ingestion_explicit_coins_uses_fetch_markets(monkeypatch):
    monkeypatch.setenv("MARKETDATA_API_KEY", "CG-test")
    monkeypatch.setenv("BRONZE_BUCKET", "marketpulse-dev-bucket-bronze-test")
    used: dict = {}

    def _fake_fetch_markets(self, coin_ids, vs_currency="usd"):
        used["ids"] = coin_ids
        return _SAMPLE

    monkeypatch.setattr(CoinGeckoClient, "fetch_markets", _fake_fetch_markets)
    monkeypatch.setattr("src.ingestion.batch.ingest.wr.s3.to_parquet", lambda **kw: None)

    ingest.run_ingestion(coins=["bitcoin", "ethereum"], captured_at=_CAPTURED_AT)
    assert used["ids"] == ["bitcoin", "ethereum"]  # an explicit list bypasses top-N


# ── CLI entry ─────────────────────────────────────────────────────────────────
def test_main_invokes_run_ingestion(monkeypatch):
    called: dict = {}
    monkeypatch.setattr(
        "src.ingestion.batch.ingest.run_ingestion",
        lambda: called.setdefault("summary", {"rows": 2}),
    )
    ingest.main()
    assert called["summary"]["rows"] == 2
