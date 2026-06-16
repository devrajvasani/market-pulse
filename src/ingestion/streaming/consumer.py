"""Kinesis consumer Lambda: trades stream -> bronze/trades/ as NDJSON (Stage 4).

Triggered by the Kinesis event-source mapping. Decodes the batch of trade records and
writes them as one newline-delimited-JSON (NDJSON) object per line to a single Bronze
file. Uses **only boto3** (no pandas/pyarrow/awswrangler), so it runs identically on AWS
and LocalStack without a Lambda layer.

Idempotency: the S3 key is derived from the batch's last Kinesis sequence number, so a
RETRIED batch overwrites the same key rather than duplicating a file; Silver additionally
dedups by the natural key ``(product_id, trade_id)``. Batches that keep failing are parked
by the event-source mapping's
on-failure DLQ (Section L.5).
"""

from __future__ import annotations

import base64
import json
from datetime import UTC, datetime

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from config import settings
from src.common.errors import IngestionError
from src.common.logging_config import get_logger

logger = get_logger(__name__)


def _s3_client():
    """Return an S3 client (honours AWS_ENDPOINT_URL so it targets LocalStack locally)."""
    return boto3.client(
        "s3", endpoint_url=settings.aws_endpoint_url(), region_name=settings.aws_region()
    )


def _decode_records(event: dict) -> tuple[list[dict], str | None]:
    """Decode a Kinesis event into (trades, last_sequence_number); skip bad records."""
    trades: list[dict] = []
    last_seq: str | None = None
    for record in event.get("Records", []):
        kinesis = record.get("kinesis", {})
        last_seq = kinesis.get("sequenceNumber", last_seq)
        try:
            trades.append(json.loads(base64.b64decode(kinesis["data"])))
        except (KeyError, ValueError, json.JSONDecodeError) as exc:
            logger.warning("Skipping unparseable Kinesis record: %s", exc)
    return trades, last_seq


def _partition_from(trade: dict) -> tuple[str, str]:
    """Derive ``(snapshot_date, snapshot_hour)`` from a trade's event time (fallback now).

    Using the event time (not the write time) keeps the partition deterministic, so a
    retried batch lands in the same partition + key (idempotent).
    """
    raw = trade.get("event_time") or trade.get("ingested_at")
    try:
        when = datetime.fromisoformat(str(raw).replace("Z", "+00:00"))
    except (TypeError, ValueError):
        when = datetime.now(UTC)
    return when.date().isoformat(), when.strftime("%H")


def handler(event: dict, context: object | None = None) -> dict:
    """Lambda entry point: write a batch of Kinesis trade records to ``bronze/trades/``.

    Raises:
        IngestionError: if the S3 write fails (the event-source mapping then retries,
            and ultimately routes the batch to the DLQ).
    """
    trades, last_seq = _decode_records(event)
    received = len(event.get("Records", []))
    if not trades:
        logger.info("No parseable trades in a batch of %d records", received)
        return {"records": received, "written": 0}

    snapshot_date, snapshot_hour = _partition_from(trades[0])
    key = f"trades/snapshot_date={snapshot_date}/snapshot_hour={snapshot_hour}/{last_seq}.json"
    body = "\n".join(json.dumps(t) for t in trades).encode("utf-8")
    bucket = settings.bronze_bucket()
    try:
        _s3_client().put_object(Bucket=bucket, Key=key, Body=body)
    except (ClientError, BotoCoreError) as exc:
        raise IngestionError(f"Failed writing trades to s3://{bucket}/{key}: {exc}") from exc

    summary = {"records": received, "written": len(trades), "key": key}
    logger.info("Wrote trades to Bronze", extra=summary)
    return summary
