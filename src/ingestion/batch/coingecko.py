"""CoinGecko market-data client — the batch-ingestion source (free Demo API).

Uses only the standard library (``urllib``) + a tiny retry, so the Lambda zip stays
small: pandas / pyarrow / awswrangler / boto3 come from the AWS SDK for pandas
layer, not the package.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request

from src.common.errors import IngestionError
from src.common.logging_config import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.coingecko.com/api/v3"
_TIMEOUT_SECONDS = 15
_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 2


class CoinGeckoClient:
    """Fetches current crypto market data from CoinGecko's free Demo API."""

    def __init__(self, api_key: str, base_url: str = BASE_URL) -> None:
        """Initialise the client.

        Args:
            api_key: CoinGecko Demo API key (sent as the ``x-cg-demo-api-key`` header).
            base_url: API base URL (override for tests).

        Raises:
            IngestionError: if the API key is empty.
        """
        if not api_key:
            raise IngestionError("CoinGecko API key is empty.")
        self._api_key = api_key
        self._base_url = base_url.rstrip("/")

    def _get(self, path: str, params: dict[str, str]) -> list | dict:
        """GET a path with retry + exponential backoff on transient errors.

        Args:
            path: API path, e.g. ``/coins/markets``.
            params: query parameters.

        Returns:
            The parsed JSON body.

        Raises:
            IngestionError: on a network/HTTP error after ``_MAX_ATTEMPTS`` attempts.
        """
        url = f"{self._base_url}{path}?{urllib.parse.urlencode(params)}"
        request = urllib.request.Request(
            url, headers={"x-cg-demo-api-key": self._api_key, "accept": "application/json"}
        )
        last_error: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                with urllib.request.urlopen(request, timeout=_TIMEOUT_SECONDS) as resp:
                    return json.loads(resp.read())
            except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as exc:
                last_error = exc
                if attempt < _MAX_ATTEMPTS:
                    backoff = _BACKOFF_BASE_SECONDS * 2 ** (attempt - 1)
                    logger.warning(
                        "CoinGecko request failed; retrying",
                        extra={"attempt": attempt, "backoff_s": backoff},
                    )
                    time.sleep(backoff)
        msg = f"CoinGecko request failed after {_MAX_ATTEMPTS} attempts: {last_error}"
        raise IngestionError(msg)

    def fetch_markets(self, coin_ids: list[str], vs_currency: str = "usd") -> list[dict]:
        """Fetch current market data (price, volume, market cap, 24h change) per coin.

        Args:
            coin_ids: CoinGecko coin IDs, e.g. ``["bitcoin", "ethereum"]``.
            vs_currency: quote currency, e.g. ``"usd"``.

        Returns:
            One dict per coin.

        Raises:
            IngestionError: on request failure or an unexpected payload shape.
        """
        params = {
            "vs_currency": vs_currency,
            "ids": ",".join(coin_ids),
            "price_change_percentage": "24h",
        }
        logger.info(
            "Fetching CoinGecko markets", extra={"coins": coin_ids, "vs_currency": vs_currency}
        )
        data = self._get("/coins/markets", params)
        if not isinstance(data, list) or not data:
            raise IngestionError(f"CoinGecko returned an unexpected payload: {type(data)}")
        logger.info("Fetched CoinGecko markets", extra={"count": len(data)})
        return data
