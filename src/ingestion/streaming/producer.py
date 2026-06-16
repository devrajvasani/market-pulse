"""Streaming producer: Coinbase trades WebSocket -> Kinesis (Stage 4).

Connects to the public Coinbase Exchange WebSocket (no auth), subscribes to the
``matches`` channel for a set of products, normalises each trade, and writes batches
to the Kinesis trades stream. By default runs for a BOUNDED window
(``STREAM_WINDOW_SECONDS``, default 60) then exits — short test windows, never left idle
(cost). Set ``STREAM_WINDOW_SECONDS=0`` to stream CONTINUOUSLY until Ctrl+C (the local
continuous-streaming demo).

The same code targets LocalStack (``AWS_ENDPOINT_URL`` set) or real AWS by config — no
emulator-specific branches. Set ``DRY_RUN=1`` to log normalised trades WITHOUT writing to
Kinesis — useful to verify the live feed before the stream exists (Stage 4a).
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime

import boto3
import websocket  # the `websocket-client` package (sync API)
from botocore.exceptions import BotoCoreError, ClientError

from config import settings
from src.common.errors import IngestionError
from src.common.logging_config import get_logger

logger = get_logger(__name__)

_COINBASE_WS_URL = "wss://ws-feed.exchange.coinbase.com"
_DEFAULT_PRODUCTS = "BTC-USD,ETH-USD"
_BATCH_SIZE = 100  # Kinesis put_records caps at 500/batch; 100 keeps payloads small
_FLUSH_SECONDS = 2.0  # flush a partial batch at least this often (near-real-time)
_RECV_TIMEOUT = 1.0  # ws recv timeout so the loop can check the deadline + flush
_CONNECT_TIMEOUT = 15.0  # generous handshake timeout (the TLS+WS upgrade can take >1s)
_RECONNECT_BACKOFF = 2.0
_HEARTBEAT_SECONDS = 15.0  # in continuous mode, log cumulative progress at least this often


def normalize_trade(message: dict) -> dict | None:
    """Normalise a Coinbase ``match``/``last_match`` message into a trade record.

    Args:
        message: a decoded Coinbase WebSocket message.

    Returns:
        A flat trade dict, or ``None`` for non-trade messages (subscriptions,
        heartbeats, errors) which are skipped.
    """
    if message.get("type") not in ("match", "last_match"):
        return None
    return {
        "trade_id": message.get("trade_id"),
        "product_id": message.get("product_id"),
        "price": message.get("price"),
        "size": message.get("size"),
        "side": message.get("side"),
        "event_time": message.get("time"),  # exchange timestamp (raw ISO string)
        "ingested_at": datetime.now(UTC).isoformat(),  # when we captured it (UTC)
        "source": "coinbase",
    }


def _to_record(trade: dict) -> dict:
    """Shape a normalised trade into a Kinesis ``PutRecords`` entry.

    Partitioning by ``product_id`` keeps each symbol's trades ordered within a shard.
    """
    return {
        "Data": json.dumps(trade).encode("utf-8"),
        "PartitionKey": trade.get("product_id") or "unknown",
    }


def _flush(kinesis, stream_name: str, trades: list[dict], dry_run: bool) -> int:
    """Send a batch of trades to Kinesis (or log them on dry-run); return count sent.

    Raises:
        IngestionError: if the Kinesis ``put_records`` call fails outright.
    """
    if not trades:
        return 0
    if dry_run:
        sample = trades[0]
        logger.info(
            "[dry-run] %d trades (e.g. %s %s %s @ %s)",
            len(trades),
            sample.get("product_id"),
            sample.get("side"),
            sample.get("size"),
            sample.get("price"),
        )
        return len(trades)
    try:
        resp = kinesis.put_records(StreamName=stream_name, Records=[_to_record(t) for t in trades])
    except (ClientError, BotoCoreError) as exc:
        raise IngestionError(f"Kinesis put_records to '{stream_name}' failed: {exc}") from exc
    failed = resp.get("FailedRecordCount", 0)
    if failed:
        logger.warning("put_records: %d of %d records failed (will resume)", failed, len(trades))
    return len(trades) - failed


def run(
    stream_name: str,
    products: list[str],
    duration_seconds: int,
    endpoint_url: str | None,
    region: str,
    dry_run: bool = False,
) -> int:
    """Stream Coinbase trades into Kinesis until the window ends — or forever.

    ``duration_seconds <= 0`` runs **continuously** until interrupted (Ctrl+C) — for the
    local continuous-streaming demo. A positive value runs one bounded window then stops
    (the cost-safe default for AWS — never leave a real stream running idle). Reconnects
    with a short backoff on connection drops. Returns the total number of trades sent.
    This is config-driven only (no LocalStack/AWS-specific branches).
    """
    kinesis = (
        None if dry_run else boto3.client("kinesis", endpoint_url=endpoint_url, region_name=region)
    )
    subscribe = json.dumps({"type": "subscribe", "product_ids": products, "channels": ["matches"]})
    continuous = duration_seconds <= 0
    deadline = float("inf") if continuous else time.monotonic() + duration_seconds
    sent_total = 0
    batch: list[dict] = []  # function-scoped so a Ctrl+C in continuous mode can flush it
    last_heartbeat = time.monotonic()
    try:
        while time.monotonic() < deadline:
            try:
                ws = websocket.create_connection(_COINBASE_WS_URL, timeout=_CONNECT_TIMEOUT)
                ws.settimeout(_RECV_TIMEOUT)  # short recv timeout so the loop polls the deadline
                ws.send(subscribe)
                logger.info(
                    "Subscribed to Coinbase matches for %s (%s)",
                    products,
                    "continuous; Ctrl+C to stop" if continuous else f"{duration_seconds}s window",
                )
                last_flush = time.monotonic()
                while time.monotonic() < deadline:
                    try:
                        raw = ws.recv()
                    except websocket.WebSocketTimeoutException:
                        raw = None
                    if raw:
                        trade = normalize_trade(json.loads(raw))
                        if trade:
                            batch.append(trade)
                    now = time.monotonic()
                    if len(batch) >= _BATCH_SIZE or (batch and now - last_flush >= _FLUSH_SECONDS):
                        sent_total += _flush(kinesis, stream_name, batch, dry_run)
                        batch, last_flush = [], now
                    if now - last_heartbeat >= _HEARTBEAT_SECONDS:
                        logger.info(
                            "Streaming... %d trades sent so far -> %s", sent_total, stream_name
                        )
                        last_heartbeat = now
                sent_total += _flush(kinesis, stream_name, batch, dry_run)  # final partial
                batch = []
                ws.close()
            except (websocket.WebSocketException, OSError) as exc:
                logger.warning("WebSocket error (%s); reconnecting in %ss", exc, _RECONNECT_BACKOFF)
                time.sleep(_RECONNECT_BACKOFF)
    except KeyboardInterrupt:
        sent_total += _flush(kinesis, stream_name, batch, dry_run)  # flush pending on Ctrl+C
        logger.info("Interrupted -- flushed pending trades.")
    logger.info(
        "Stream %s: %d trades -> %s",
        "stopped" if continuous else "window complete",
        sent_total,
        stream_name,
    )
    return sent_total


def main() -> None:
    """CLI entry point: read config from the environment and stream.

    ``STREAM_WINDOW_SECONDS`` sets the run length: a positive value streams one bounded
    window (default 60); ``0`` streams CONTINUOUSLY until Ctrl+C. ``DRY_RUN=1`` logs
    without writing to Kinesis; ``STREAM_PRODUCTS`` overrides products (default BTC/ETH).
    """
    products = [
        p.strip()
        for p in (os.getenv("STREAM_PRODUCTS") or _DEFAULT_PRODUCTS).split(",")
        if p.strip()
    ]
    duration = int((os.getenv("STREAM_WINDOW_SECONDS") or "60").strip())
    dry_run = (os.getenv("DRY_RUN") or "").strip().lower() in ("1", "true", "yes")
    run(
        settings.trades_stream_name(),
        products,
        duration,
        settings.aws_endpoint_url(),
        settings.aws_region(),
        dry_run=dry_run,
    )


if __name__ == "__main__":
    main()
