"""Offline unit tests for the streaming producer's pure logic (Section L.6).

No WebSocket, no Kinesis, no network — just the message-normalisation and
record-shaping functions that turn raw Coinbase messages into Kinesis records.
"""

from __future__ import annotations

import json

from src.ingestion.streaming.producer import _to_record, normalize_trade


def test_normalize_trade_keeps_match_fields():
    msg = {
        "type": "match",
        "trade_id": 12345,
        "product_id": "BTC-USD",
        "price": "50000.10",
        "size": "0.0123",
        "side": "buy",
        "time": "2026-06-12T12:00:00.000000Z",
    }
    trade = normalize_trade(msg)
    assert trade is not None
    assert trade["trade_id"] == 12345
    assert trade["product_id"] == "BTC-USD"
    assert trade["price"] == "50000.10"
    assert trade["size"] == "0.0123"
    assert trade["side"] == "buy"
    assert trade["event_time"] == "2026-06-12T12:00:00.000000Z"
    assert trade["source"] == "coinbase"
    assert trade["ingested_at"]  # stamped at capture time


def test_normalize_trade_accepts_last_match():
    # Coinbase emits a `last_match` (most recent trade) right after subscribe.
    assert normalize_trade({"type": "last_match", "product_id": "ETH-USD"}) is not None


def test_normalize_trade_ignores_non_trade_messages():
    for kind in ("subscriptions", "heartbeat", "ticker", "error"):
        assert normalize_trade({"type": kind}) is None


def test_to_record_partitions_by_product_and_encodes_json():
    rec = _to_record({"product_id": "ETH-USD", "price": "3000.5"})
    assert rec["PartitionKey"] == "ETH-USD"
    assert json.loads(rec["Data"])["price"] == "3000.5"  # bytes -> JSON round-trips


def test_to_record_defaults_partition_key_when_missing():
    rec = _to_record({"price": "1.0"})
    assert rec["PartitionKey"] == "unknown"
