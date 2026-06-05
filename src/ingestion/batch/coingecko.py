"""CoinGecko market-data client — the batch-ingestion source (free Demo API)."""

from __future__ import annotations

import requests
from tenacity import (
    retry,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from src.common.errors import IngestionError
from src.common.logging_config import get_logger

logger = get_logger(__name__)

BASE_URL = "https://api.coingecko.com/api/v3"
_TIMEOUT_SECONDS = 15


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

    @retry(
        retry=retry_if_exception_type(requests.RequestException),
        stop=stop_after_attempt(3),
        wait=wait_exponential(multiplier=1, min=2, max=10),
        reraise=True,
    )
    def _get(self, path: str, params: dict[str, str]) -> list[dict]:
        """GET a path, retrying on transient network/HTTP errors.

        Args:
            path: API path, e.g. ``/coins/markets``.
            params: query parameters.

        Returns:
            The parsed JSON body.

        Raises:
            requests.RequestException: on a network/HTTP error (after 3 attempts).
        """
        url = f"{self._base_url}{path}"
        headers = {"x-cg-demo-api-key": self._api_key, "accept": "application/json"}
        resp = requests.get(url, params=params, headers=headers, timeout=_TIMEOUT_SECONDS)
        resp.raise_for_status()
        return resp.json()

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
        try:
            data = self._get("/coins/markets", params)
        except requests.RequestException as exc:
            raise IngestionError(f"CoinGecko request failed after retries: {exc}") from exc
        if not isinstance(data, list) or not data:
            raise IngestionError(f"CoinGecko returned an unexpected payload: {type(data)}")
        logger.info("Fetched CoinGecko markets", extra={"count": len(data)})
        return data
