"""Batch ingestion: CoinGecko -> Parquet -> S3 Bronze, idempotent by date partition."""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime

import awswrangler as wr
import boto3
import pandas as pd
from botocore.exceptions import BotoCoreError, ClientError

from config import settings
from src.common.errors import IngestionError
from src.common.logging_config import get_logger
from src.ingestion.batch.coingecko import CoinGeckoClient

logger = get_logger(__name__)

DEFAULT_COINS = ["bitcoin", "ethereum"]
DEFAULT_VS_CURRENCY = "usd"


def _read_api_key() -> str:
    """Return the CoinGecko API key — from env (local) or Secrets Manager (cloud).

    Returns:
        The API key string.

    Raises:
        IngestionError: if neither source yields a usable key.
    """
    env_key = (os.getenv("MARKETDATA_API_KEY") or "").strip()
    if env_key:
        return env_key

    secret_name = settings.marketdata_secret_name()
    client = boto3.client(
        "secretsmanager",
        endpoint_url=settings.aws_endpoint_url(),
        region_name=settings.aws_region(),
    )
    try:
        resp = client.get_secret_value(SecretId=secret_name)
    except (ClientError, BotoCoreError) as exc:
        raise IngestionError(f"Could not read secret '{secret_name}': {exc}") from exc
    try:
        return json.loads(resp["SecretString"])["api_key"]
    except (KeyError, TypeError, json.JSONDecodeError) as exc:
        raise IngestionError(f"Secret '{secret_name}' has no 'api_key' field: {exc}") from exc


def _to_dataframe(
    records: list[dict], vs_currency: str, ingest_date: str | None = None
) -> pd.DataFrame:
    """Normalise CoinGecko market records into a Bronze DataFrame (partition col ``dt``).

    Args:
        records: raw CoinGecko market dicts.
        vs_currency: quote currency used for the request.
        ingest_date: ISO date (YYYY-MM-DD) for the partition; defaults to today (UTC).

    Returns:
        One row per coin, with a ``dt`` partition column.
    """
    partition = ingest_date or datetime.now(UTC).date().isoformat()
    now_iso = datetime.now(UTC).isoformat()
    rows = [
        {
            "coin_id": r.get("id"),
            "symbol": r.get("symbol"),
            "name": r.get("name"),
            "vs_currency": vs_currency,
            "price": r.get("current_price"),
            "market_cap": r.get("market_cap"),
            "total_volume": r.get("total_volume"),
            "price_change_pct_24h": r.get("price_change_percentage_24h"),
            "last_updated": r.get("last_updated"),
            "ingested_at": now_iso,
            "dt": partition,
        }
        for r in records
    ]
    return pd.DataFrame(rows)


def run_ingestion(
    coins: list[str] | None = None,
    vs_currency: str = DEFAULT_VS_CURRENCY,
    ingest_date: str | None = None,
) -> dict:
    """Fetch CoinGecko prices and write them as Parquet to Bronze (idempotent).

    Re-running for the same date overwrites that date's partition — no duplicates (L.3).

    Args:
        coins: CoinGecko coin IDs (default BTC + ETH).
        vs_currency: quote currency.
        ingest_date: override the partition date (default today, UTC).

    Returns:
        A summary dict: rows written, bucket, partition, coins.

    Raises:
        IngestionError: on fetch or write failure.
    """
    coins = coins or DEFAULT_COINS
    client = CoinGeckoClient(_read_api_key())
    records = client.fetch_markets(coins, vs_currency)
    frame = _to_dataframe(records, vs_currency, ingest_date)

    bucket = settings.bronze_bucket()
    path = f"s3://{bucket}/prices/"
    partition = frame["dt"].iloc[0]
    try:
        wr.s3.to_parquet(
            df=frame,
            path=path,
            dataset=True,
            partition_cols=["dt"],
            mode="overwrite_partitions",  # idempotent: replace this date's partition only
            compression="snappy",
        )
    except (ClientError, BotoCoreError, ValueError) as exc:
        raise IngestionError(f"Failed writing Parquet to {path} (dt={partition}): {exc}") from exc

    summary = {"rows": int(len(frame)), "bucket": bucket, "partition": partition, "coins": coins}
    logger.info("Wrote prices to Bronze", extra=summary)
    return summary
