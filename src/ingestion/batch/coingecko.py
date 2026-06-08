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
_MAX_PER_PAGE = 250  # CoinGecko /coins/markets returns at most 250 coins per page
# 1h/24h/7d percent moves -> adds price_change_percentage_{1h,7d}_in_currency
# (24h is also always returned as the base price_change_percentage_24h field).
_PRICE_CHANGE_WINDOWS = "1h,24h,7d"


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
            IngestionError: immediately on a non-retryable HTTP 4xx (bad key/params), else
                after ``_MAX_ATTEMPTS`` attempts on transient (429 / 5xx / network) errors.
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
            except urllib.error.HTTPError as exc:
                # Client errors (bad key / params) won't fix themselves — fail fast.
                # Only 429 (rate limit) and 5xx are worth retrying.
                if exc.code != 429 and exc.code < 500:
                    raise IngestionError(f"CoinGecko returned HTTP {exc.code} for {path}") from exc
                last_error = exc
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

    def _markets(self, params: dict[str, str], label: str) -> list[dict]:
        """GET /coins/markets with ``params``, validate the shape, return the coin list.

        Raises:
            IngestionError: on request failure or an unexpected (non-list / empty) payload.
        """
        data = self._get("/coins/markets", params)
        if not isinstance(data, list):
            raise IngestionError(
                f"CoinGecko [{label}] returned an unexpected payload: "
                f"expected a list, got {type(data).__name__}"
            )
        if not data:
            raise IngestionError(f"CoinGecko [{label}] returned an empty result set")
        logger.info("Fetched CoinGecko markets", extra={"request": label, "count": len(data)})
        return data

    def fetch_markets(self, coin_ids: list[str], vs_currency: str = "usd") -> list[dict]:
        """Fetch market data for specific coins by id (price, volume, cap, supply, ATH, moves).

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
            "price_change_percentage": _PRICE_CHANGE_WINDOWS,
        }
        logger.info(
            "Fetching CoinGecko markets by id",
            extra={"coins": coin_ids, "vs_currency": vs_currency},
        )
        return self._markets(params, label=f"ids={len(coin_ids)}")

    def fetch_top_markets(self, count: int, vs_currency: str = "usd") -> list[dict]:
        """Fetch the top ``count`` coins by market cap in a single page.

        Args:
            count: how many coins to fetch (1..250 — the CoinGecko per-page maximum).
            vs_currency: quote currency, e.g. ``"usd"``.

        Returns:
            One dict per coin, ranked by descending market cap.

        Raises:
            IngestionError: if ``count`` is outside 1..250, or on request / payload failure.
        """
        if not 1 <= count <= _MAX_PER_PAGE:
            raise IngestionError(
                f"top-N must be 1..{_MAX_PER_PAGE} (CoinGecko per-page max); got {count}"
            )
        params = {
            "vs_currency": vs_currency,
            "order": "market_cap_desc",
            "per_page": str(count),
            "page": "1",
            "price_change_percentage": _PRICE_CHANGE_WINDOWS,
        }
        logger.info(
            "Fetching CoinGecko top markets",
            extra={"top_n": count, "vs_currency": vs_currency},
        )
        return self._markets(params, label=f"top={count}")
