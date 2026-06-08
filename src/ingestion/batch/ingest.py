"""Batch ingestion: CoinGecko -> Parquet -> S3 Bronze, idempotent by hourly snapshot."""

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

# Default universe: the top-N coins by market cap (one CoinGecko page, <=250). Override
# per-deploy with the INGEST_TOP_N env var, or pass explicit `coins` to bypass top-N.
DEFAULT_TOP_N = 100
DEFAULT_QUOTE_CURRENCY = "usd"

# Bronze is partitioned by capture time so every hourly snapshot is retained and a
# re-run of the same hour overwrites only that hour's partition (idempotent, L.3).
_PARTITION_COLS = ["snapshot_date", "snapshot_hour"]

# Pin every Bronze column to an explicit Athena/Glue physical type. This keeps the
# Parquet schema identical across partitions even when a nullable field is null for
# every coin in a pull -- otherwise pandas/pyarrow would infer a clashing type and
# Athena would raise HIVE_BAD_DATA against the manually-defined Glue table (Stage 2).
# Amounts stay currency-neutral (the currency is its own column); timestamps stay raw
# ISO strings here (Bronze is raw) and are cast to timestamps in Silver.
_BRONZE_DTYPES: dict[str, str] = {
    "coin_id": "string",
    "coin_symbol": "string",
    "coin_name": "string",
    "quote_currency": "string",
    "current_price": "double",
    "market_cap": "double",
    "market_cap_rank": "bigint",
    "fully_diluted_valuation": "double",
    "total_volume": "double",
    "high_24h": "double",
    "low_24h": "double",
    "price_change_24h": "double",
    "price_change_pct_24h": "double",
    "price_change_pct_1h": "double",
    "price_change_pct_7d": "double",
    "market_cap_change_24h": "double",
    "market_cap_change_pct_24h": "double",
    "circulating_supply": "double",
    "total_supply": "double",
    "max_supply": "double",
    "ath": "double",
    "ath_change_pct": "double",
    "source_updated_at": "string",
    "ingested_at": "string",
}


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


def _resolve_top_n(top_n: int | None) -> int:
    """Resolve the top-N coin count: explicit arg > ``INGEST_TOP_N`` env > ``DEFAULT_TOP_N``.

    Args:
        top_n: explicit override; if ``None``, fall back to the env var, then the default.

    Returns:
        The number of top-by-market-cap coins to fetch (range-checked later by the client).

    Raises:
        IngestionError: if ``INGEST_TOP_N`` is set but is not a valid integer.
    """
    if top_n is not None:
        return top_n
    raw = (os.getenv("INGEST_TOP_N") or "").strip()
    if not raw:
        return DEFAULT_TOP_N
    try:
        return int(raw)
    except ValueError as exc:
        raise IngestionError(f"INGEST_TOP_N must be an integer; got {raw!r}") from exc


def _to_dataframe(
    records: list[dict], quote_currency: str, captured_at: datetime | None = None
) -> pd.DataFrame:
    """Normalise CoinGecko market records into a Bronze DataFrame.

    Bronze stays raw: every analytically-useful scalar the API returns is kept with its
    source value (timestamps remain raw ISO strings — Silver casts them later), and the
    partition columns are derived from the capture instant.

    Args:
        records: raw CoinGecko market dicts.
        quote_currency: the currency the request was priced in (stored verbatim).
        captured_at: capture instant (UTC); defaults to now. Drives ``ingested_at`` and
            the ``snapshot_date`` / ``snapshot_hour`` partitions.

    Returns:
        One row per coin, columns named + ordered per ``_BRONZE_DTYPES`` plus the
        partition columns. Missing source fields become null — the API marks most
        numerics nullable (e.g. ``max_supply`` is null for uncapped coins like ETH).
    """
    captured = captured_at or datetime.now(UTC)
    captured_iso = captured.isoformat()
    snapshot_date = captured.date().isoformat()
    snapshot_hour = captured.strftime("%H")  # zero-padded "00".."23" -> sorts chronologically
    rows = [
        {
            "coin_id": r.get("id"),
            "coin_symbol": r.get("symbol"),
            "coin_name": r.get("name"),
            "quote_currency": quote_currency,
            "current_price": r.get("current_price"),
            "market_cap": r.get("market_cap"),
            "market_cap_rank": r.get("market_cap_rank"),
            "fully_diluted_valuation": r.get("fully_diluted_valuation"),
            "total_volume": r.get("total_volume"),
            "high_24h": r.get("high_24h"),
            "low_24h": r.get("low_24h"),
            "price_change_24h": r.get("price_change_24h"),
            "price_change_pct_24h": r.get("price_change_percentage_24h"),
            "price_change_pct_1h": r.get("price_change_percentage_1h_in_currency"),
            "price_change_pct_7d": r.get("price_change_percentage_7d_in_currency"),
            "market_cap_change_24h": r.get("market_cap_change_24h"),
            "market_cap_change_pct_24h": r.get("market_cap_change_percentage_24h"),
            "circulating_supply": r.get("circulating_supply"),
            "total_supply": r.get("total_supply"),
            "max_supply": r.get("max_supply"),
            "ath": r.get("ath"),
            "ath_change_pct": r.get("ath_change_percentage"),
            "source_updated_at": r.get("last_updated"),
            "ingested_at": captured_iso,
            "snapshot_date": snapshot_date,
            "snapshot_hour": snapshot_hour,
        }
        for r in records
    ]
    return pd.DataFrame(rows, columns=[*_BRONZE_DTYPES, *_PARTITION_COLS])


def run_ingestion(
    coins: list[str] | None = None,
    top_n: int | None = None,
    quote_currency: str = DEFAULT_QUOTE_CURRENCY,
    captured_at: datetime | None = None,
) -> dict:
    """Fetch CoinGecko prices and write them as Parquet to Bronze (idempotent).

    Re-running within the same hour overwrites that hour's partition only — no
    duplicates — while earlier hours are preserved (L.3).

    Args:
        coins: explicit CoinGecko coin IDs to fetch. If omitted, fetch the top ``top_n``
            coins by market cap (the default universe).
        top_n: number of top-by-market-cap coins to fetch when ``coins`` is omitted
            (default: the ``INGEST_TOP_N`` env var, else ``DEFAULT_TOP_N``).
        quote_currency: quote currency for the request.
        captured_at: override the capture instant (default now, UTC).

    Returns:
        A summary dict: rows written, bucket, snapshot partition.

    Raises:
        IngestionError: on fetch or write failure.
    """
    client = CoinGeckoClient(_read_api_key())
    if coins:
        records = client.fetch_markets(coins, vs_currency=quote_currency)
    else:
        count = _resolve_top_n(top_n)
        records = client.fetch_top_markets(count, vs_currency=quote_currency)
    frame = _to_dataframe(records, quote_currency, captured_at)

    bucket = settings.bronze_bucket()
    path = f"s3://{bucket}/prices/"
    snapshot_date = frame["snapshot_date"].iloc[0]
    snapshot_hour = frame["snapshot_hour"].iloc[0]
    try:
        wr.s3.to_parquet(
            df=frame,
            path=path,
            dataset=True,
            partition_cols=_PARTITION_COLS,
            mode="overwrite_partitions",  # idempotent: replace only this hour's partition
            compression="snappy",
            dtype=_BRONZE_DTYPES,  # stable physical types for the manual Glue table (Stage 2)
        )
    except (ClientError, BotoCoreError, ValueError) as exc:
        raise IngestionError(
            f"Failed writing Parquet to {path} "
            f"(snapshot_date={snapshot_date}, snapshot_hour={snapshot_hour}): {exc}"
        ) from exc

    summary = {
        "rows": int(len(frame)),
        "bucket": bucket,
        "snapshot_date": snapshot_date,
        "snapshot_hour": snapshot_hour,
    }
    logger.info("Wrote prices to Bronze", extra=summary)
    return summary


def main() -> None:
    """Run one batch ingestion from the CLI (local/dev use).

    Reads all configuration from the environment (APP_ENV, BRONZE_BUCKET,
    AWS_ENDPOINT_URL, MARKETDATA_API_KEY). Used by ``make run-local``.
    """
    summary = run_ingestion()
    logger.info("Batch ingestion complete", extra=summary)


if __name__ == "__main__":
    main()
