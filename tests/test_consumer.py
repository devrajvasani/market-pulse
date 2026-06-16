"""Offline unit tests for the Kinesis trades consumer (Section L.6).

No Kinesis, no real S3 — a synthetic Kinesis event + a fake S3 client. Verifies record
decoding (incl. skipping garbage), the idempotent S3 key, and the NDJSON body.
"""

from __future__ import annotations

import base64
import json

from src.ingestion.streaming import consumer
from src.ingestion.streaming.consumer import _decode_records, handler


def _kinesis_record(seq: str, obj=None, raw_bytes: bytes | None = None) -> dict:
    data = raw_bytes if raw_bytes is not None else json.dumps(obj).encode("utf-8")
    return {"kinesis": {"sequenceNumber": seq, "data": base64.b64encode(data).decode("ascii")}}


def test_decode_records_skips_unparseable_and_tracks_last_seq():
    event = {
        "Records": [
            _kinesis_record("1", {"trade_id": 1, "product_id": "BTC-USD"}),
            _kinesis_record("2", raw_bytes=b"not-json"),  # garbage -> skipped
            _kinesis_record("3", {"trade_id": 2, "product_id": "ETH-USD"}),
        ]
    }
    trades, last_seq = _decode_records(event)
    assert [t["trade_id"] for t in trades] == [1, 2]  # the bad record is skipped
    assert last_seq == "3"  # last sequence number tracked even across a bad record


def test_handler_writes_ndjson_to_idempotent_key(monkeypatch):
    captured: dict = {}

    class FakeS3:
        def put_object(self, **kwargs):
            captured.update(kwargs)

    monkeypatch.setattr(consumer, "_s3_client", lambda: FakeS3())
    monkeypatch.setenv("BRONZE_BUCKET", "test-bucket")

    event = {
        "Records": [
            _kinesis_record(
                "42",
                {"trade_id": 1, "product_id": "BTC-USD", "event_time": "2026-06-14T15:30:00.000Z"},
            ),
            _kinesis_record(
                "43",
                {"trade_id": 2, "product_id": "BTC-USD", "event_time": "2026-06-14T15:31:00.000Z"},
            ),
        ]
    }
    summary = handler(event)

    assert summary["written"] == 2
    assert captured["Bucket"] == "test-bucket"
    # key = partition (from first trade's event_time) + the batch's LAST sequence number
    assert captured["Key"] == "trades/snapshot_date=2026-06-14/snapshot_hour=15/43.json"
    lines = captured["Body"].decode("utf-8").splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["trade_id"] == 1


def test_handler_empty_batch_is_a_noop(monkeypatch):
    def _no_s3():
        raise AssertionError("S3 must not be touched for an empty batch")

    monkeypatch.setattr(consumer, "_s3_client", _no_s3)
    assert handler({"Records": []}) == {"records": 0, "written": 0}
