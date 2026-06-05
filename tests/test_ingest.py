"""Critical tests for batch ingestion: fetch, transform, key read, idempotent write."""

import json

import boto3
import pytest
from moto import mock_aws

from config import settings
from src.common.errors import IngestionError
from src.ingestion.batch import ingest
from src.ingestion.batch.coingecko import CoinGeckoClient

_SAMPLE = [
    {
        "id": "bitcoin",
        "symbol": "btc",
        "name": "Bitcoin",
        "current_price": 100.0,
        "market_cap": 10,
        "total_volume": 5,
        "price_change_percentage_24h": -0.5,
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


def test_fetch_markets_rejects_bad_payload(monkeypatch):
    monkeypatch.setattr(
        "src.ingestion.batch.coingecko.urllib.request.urlopen", lambda *a, **k: _FakeUrlopen({})
    )
    with pytest.raises(IngestionError):
        CoinGeckoClient("CG-test").fetch_markets(["bitcoin"])


# ── Transform ─────────────────────────────────────────────────────────────────
def test_to_dataframe_shape():
    frame = ingest._to_dataframe(_SAMPLE, "usd", ingest_date="2026-06-05")
    assert len(frame) == 2
    assert frame["dt"].iloc[0] == "2026-06-05"
    assert frame["price"].iloc[0] == 100.0
    assert frame["vs_currency"].iloc[0] == "usd"
    assert {"coin_id", "symbol", "price", "dt", "ingested_at"}.issubset(frame.columns)


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
def test_run_ingestion_writes_with_overwrite_partitions(monkeypatch):
    monkeypatch.setenv("MARKETDATA_API_KEY", "CG-test")
    monkeypatch.setenv("BRONZE_BUCKET", "marketpulse-dev-bucket-bronze-test")
    monkeypatch.setattr(
        CoinGeckoClient, "fetch_markets", lambda self, coin_ids, vs_currency="usd": _SAMPLE
    )
    captured: dict = {}
    monkeypatch.setattr(
        "src.ingestion.batch.ingest.wr.s3.to_parquet", lambda **kwargs: captured.update(kwargs)
    )

    summary = ingest.run_ingestion(ingest_date="2026-06-05")

    assert summary["rows"] == 2
    assert summary["partition"] == "2026-06-05"
    # idempotency-by-design: replace only this date's partition, no appends
    assert captured["mode"] == "overwrite_partitions"
    assert captured["partition_cols"] == ["dt"]
    assert captured["path"] == "s3://marketpulse-dev-bucket-bronze-test/prices/"


# ── CLI entry ─────────────────────────────────────────────────────────────────
def test_main_invokes_run_ingestion(monkeypatch):
    called: dict = {}
    monkeypatch.setattr(
        "src.ingestion.batch.ingest.run_ingestion",
        lambda: called.setdefault("summary", {"rows": 2}),
    )
    ingest.main()
    assert called["summary"]["rows"] == 2
