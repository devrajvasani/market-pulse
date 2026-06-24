"""Hybrid Gold lookups: detect intent in a question, run templated queries over Gold.

The "hybrid twist" — beyond document retrieval, the assistant pulls *live numbers* from the Gold
marts. Queries are **fixed templates** parameterised only with values from a whitelisted coin map
(never raw user text), so there is no SQL-injection surface. Runs through the shared
:class:`GoldQueryRepository`, so it works on Athena (AWS) and DuckDB (local) unchanged.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from rag.app.core.config import RagConfig
from rag.app.core.errors import GoldQueryError
from rag.app.core.logging import get_logger
from rag.app.repositories.gold_query import GoldQueryRepository

logger = get_logger(__name__)

_MOVERS_LIMIT = 5
_MOVERS_RE = re.compile(r"\b(movers?|gainers?|losers?|top\s+coins?|biggest\s+(gain|los|mov))", re.I)


@dataclass(frozen=True)
class Coin:
    """A coin known to the router: its Gold ``coin_id`` + trading symbol + match aliases."""

    coin_id: str  # matches gold_market_movers.coin_id (the CoinGecko id)
    symbol: str  # e.g. "BTC"
    aliases: tuple[str, ...]

    @property
    def product_id(self) -> str:
        """The streaming product id used in gold_latest_price, e.g. ``BTC-USD``."""
        return f"{self.symbol}-USD"


# coin_id values match the CoinGecko ids stored in gold_market_movers.coin_id.
_COINS: tuple[Coin, ...] = (
    Coin("bitcoin", "BTC", ("bitcoin", "btc")),
    Coin("ethereum", "ETH", ("ethereum", "ether", "eth")),
    Coin("solana", "SOL", ("solana", "sol")),
    Coin("ripple", "XRP", ("ripple", "xrp")),
    Coin("cardano", "ADA", ("cardano", "ada")),
    Coin("dogecoin", "DOGE", ("dogecoin", "doge")),
    Coin("binancecoin", "BNB", ("binance coin", "binancecoin", "bnb")),
    Coin("tron", "TRX", ("tron", "trx")),
    Coin("litecoin", "LTC", ("litecoin", "ltc")),
    Coin("polkadot", "DOT", ("polkadot",)),
    Coin("chainlink", "LINK", ("chainlink",)),
    Coin("avalanche-2", "AVAX", ("avalanche", "avax")),
)


@dataclass(frozen=True)
class GoldFact:
    """One live metric, with a display string and (when numeric) the raw value + unit."""

    label: str
    value: str
    numeric: float | None = None
    unit: str | None = None


@dataclass(frozen=True)
class GoldResult:
    """The outcome of a Gold lookup: the detected intent, facts, and a prompt-ready summary."""

    intent: str  # "coin_stats" | "movers" | "none"
    facts: tuple[GoldFact, ...] = ()
    summary: str = ""

    @property
    def used(self) -> bool:
        """True when at least one live fact was produced."""
        return bool(self.facts)


def _num(value: object) -> float | None:
    """Coerce a DB value to float, or None if missing/non-numeric."""
    if value is None:
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def _fmt_price(value: object) -> str:
    """Format a price as ``$1,234.56`` (or ``n/a``)."""
    num = _num(value)
    return f"${num:,.2f}" if num is not None else "n/a"


def _fmt_pct(value: object) -> str:
    """Format a percentage with sign as ``+8.20%`` (or ``n/a``)."""
    num = _num(value)
    return f"{num:+.2f}%" if num is not None else "n/a"


def find_coin(question: str) -> Coin | None:
    """Return the first whitelisted coin whose alias appears as a whole word in ``question``."""
    lowered = question.lower()
    for coin in _COINS:
        if any(re.search(rf"\b{re.escape(alias)}\b", lowered) for alias in coin.aliases):
            return coin
    return None


class GoldService:
    """Route a question to a templated Gold query and shape the result into facts."""

    def __init__(self, config: RagConfig, repo: GoldQueryRepository | None = None) -> None:
        """Initialise with config; the Gold repository is injectable for tests."""
        self._config = config
        self._repo = repo or GoldQueryRepository()

    def facts_for(self, question: str) -> GoldResult:
        """Detect intent and return live Gold facts (empty when none apply).

        Gold is a **supplementary** layer: if the query engine is unavailable (e.g. the local
        Gold DuckDB isn't built yet, or Athena errors), degrade gracefully to *no facts* so the
        assistant still answers from the retrieved documents — never fail the whole request.
        """
        coin = find_coin(question)
        intent = (
            "coin_stats"
            if coin is not None
            else ("movers" if _MOVERS_RE.search(question) else "none")
        )
        if intent == "none":
            return GoldResult(intent="none")
        try:
            if coin is not None:
                return self._coin_stats(coin, question)
            return self._movers()
        except GoldQueryError as exc:
            logger.warning(
                "Gold lookup unavailable; answering from documents only",
                extra={"intent": intent, "error": str(exc)},
            )
            return GoldResult(intent=intent)

    def _safe_run(self, sql: str) -> list[dict] | None:
        """Run a supplementary Gold query; return None (logged) if its mart is unavailable.

        Each Gold mart is independent — a missing batch table shouldn't suppress the streaming
        price, and vice versa — so sub-queries degrade individually rather than all-or-nothing.
        """
        try:
            return self._repo.run(sql)
        except GoldQueryError as exc:
            logger.warning("Gold sub-query unavailable", extra={"error": str(exc)})
            return None

    def _coin_stats(self, coin: Coin, question: str) -> GoldResult:
        """Live numbers for one coin: price + 1h/24h/7d moves + all-time high + streaming price.

        Both sources are best-effort and independent, so the assistant uses whatever Gold exists
        (e.g. a streaming-only local build still yields the live price).
        """
        facts: list[GoldFact] = []
        summary_bits: list[str] = []

        movers_sql = (
            "SELECT coin_name, coin_symbol, current_price, "
            "price_change_pct_1h, price_change_pct_24h, price_change_pct_7d, ath, ath_change_pct "
            f"FROM gold_market_movers WHERE coin_id = '{coin.coin_id}'"
        )
        movers = self._safe_run(movers_sql)
        if movers:
            row = movers[0]
            name = row.get("coin_name") or coin.coin_id.title()
            symbol = (row.get("coin_symbol") or coin.symbol).upper()
            price = GoldFact(
                f"{name} price",
                _fmt_price(row.get("current_price")),
                _num(row.get("current_price")),
                "USD",
            )
            h24 = GoldFact(
                f"{name} 24h change",
                _fmt_pct(row.get("price_change_pct_24h")),
                _num(row.get("price_change_pct_24h")),
                "%",
            )
            d7 = GoldFact(
                f"{name} 7d change",
                _fmt_pct(row.get("price_change_pct_7d")),
                _num(row.get("price_change_pct_7d")),
                "%",
            )
            facts += [price, h24, d7]
            summary_bits.append(
                f"{name} ({symbol}): price {price.value}, 24h {h24.value}, 7d {d7.value}"
            )
            if row.get("ath") is not None:
                ath = GoldFact(
                    f"{name} all-time high", _fmt_price(row.get("ath")), _num(row.get("ath")), "USD"
                )
                from_ath = GoldFact(
                    f"{name} from ATH",
                    _fmt_pct(row.get("ath_change_pct")),
                    _num(row.get("ath_change_pct")),
                    "%",
                )
                facts += [ath, from_ath]
                summary_bits.append(f"all-time high {ath.value} ({from_ath.value} from ATH)")

        latest_sql = (
            "SELECT product_id, latest_price FROM gold_latest_price "
            f"WHERE product_id = '{coin.product_id}'"
        )
        latest = self._safe_run(latest_sql)
        if latest:
            live_price = latest[0].get("latest_price")
            facts.append(
                GoldFact(
                    f"{coin.symbol} live trade price",
                    _fmt_price(live_price),
                    _num(live_price),
                    "USD",
                )
            )
            if not movers:  # streaming-only build: surface the live price in the summary
                summary_bits.append(f"{coin.symbol} latest trade price {_fmt_price(live_price)}")

        if not facts:
            logger.warning("No Gold data for coin", extra={"coin_id": coin.coin_id})
            return GoldResult(intent="coin_stats")
        return GoldResult(
            intent="coin_stats", facts=tuple(facts), summary="; ".join(summary_bits) + "."
        )

    def _movers(self) -> GoldResult:
        """Top 24h movers across the market (from gold_market_movers, ranked)."""
        sql = (
            "SELECT coin_name, coin_symbol, current_price, price_change_pct_24h, change_rank_24h "
            f"FROM gold_market_movers ORDER BY change_rank_24h LIMIT {_MOVERS_LIMIT}"
        )
        rows = self._repo.run(sql)
        if not rows:
            return GoldResult(intent="movers")
        facts = tuple(
            GoldFact(
                f"#{row.get('change_rank_24h')} {row.get('coin_name')}",
                _fmt_pct(row.get("price_change_pct_24h")),
                _num(row.get("price_change_pct_24h")),
                "%",
            )
            for row in rows
        )
        summary = "Top 24h movers: " + "; ".join(
            f"{i + 1}) {row.get('coin_name')} {_fmt_pct(row.get('price_change_pct_24h'))}"
            for i, row in enumerate(rows)
        )
        return GoldResult(intent="movers", facts=facts, summary=summary)
