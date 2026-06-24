"""Critical tests for the Gold hybrid service: intent routing + templated queries."""

from rag.app.core.config import get_config
from rag.app.core.errors import GoldQueryError
from rag.app.services.gold import GoldService, find_coin

_MOVERS_ROW = {
    "coin_name": "Bitcoin",
    "coin_symbol": "btc",
    "current_price": 65000.0,
    "price_change_pct_1h": 0.5,
    "price_change_pct_24h": 2.1,
    "price_change_pct_7d": 8.2,
    "ath": 109000.0,
    "ath_change_pct": -40.4,
}


_LATEST_ROW = {"product_id": "BTC-USD", "latest_price": 64950.0}


class _FakeRepo:
    """Captures SQL and returns canned rows; routes batch vs streaming queries by table name."""

    def __init__(self, rows=None, *, latest_rows="default", raise_on_latest=False):
        self._rows = rows if rows is not None else [_MOVERS_ROW]
        self._latest_rows = [_LATEST_ROW] if latest_rows == "default" else latest_rows
        self._raise_on_latest = raise_on_latest
        self.queries: list[str] = []

    def run(self, sql: str):
        self.queries.append(sql)
        if "gold_latest_price" in sql:
            if self._raise_on_latest:
                raise GoldQueryError("streaming mart missing")
            return self._latest_rows
        return self._rows


def test_find_coin_detects_named_coin():
    assert find_coin("What moved Bitcoin this week?").coin_id == "bitcoin"
    assert find_coin("ethereum upgrade news").coin_id == "ethereum"


def test_find_coin_none_for_generic_question():
    assert find_coin("what is happening in the market today?") is None


def test_coin_stats_builds_facts_and_summary():
    repo = _FakeRepo()
    result = GoldService(get_config(), repo=repo).facts_for(
        "What moved Bitcoin this week and by how much?"
    )
    assert result.intent == "coin_stats"
    assert result.used
    # the DoD number: the 7d change appears as a fact
    sevend = next(f for f in result.facts if "7d" in f.label)
    assert sevend.value == "+8.20%" and sevend.numeric == 8.2
    assert "Bitcoin" in result.summary
    # templated SQL is parameterised by the whitelisted coin_id, not raw user text
    assert "coin_id = 'bitcoin'" in repo.queries[0]


def test_coin_stats_includes_all_time_high():
    repo = _FakeRepo()
    result = GoldService(get_config(), repo=repo).facts_for("what is the all-time high of BTC?")
    assert result.intent == "coin_stats"
    ath = next(f for f in result.facts if "all-time high" in f.label)
    assert ath.value == "$109,000.00" and ath.numeric == 109000.0
    assert "ath" in repo.queries[0]  # the templated coin-stats query now selects ath


def test_coin_stats_no_data_returns_empty():
    # Neither the batch nor the streaming mart has the coin -> degrade to no facts (docs-only).
    repo = _FakeRepo(rows=[], latest_rows=[])
    result = GoldService(get_config(), repo=repo).facts_for("bitcoin price?")
    assert result.intent == "coin_stats"
    assert not result.used


def test_live_price_always_included_for_coin():
    # The streaming trade price is surfaced even without an explicit "right now" cue.
    repo = _FakeRepo()
    result = GoldService(get_config(), repo=repo).facts_for("What moved Bitcoin this week?")
    assert any("live trade price" in f.label for f in result.facts)
    assert any("gold_latest_price" in q for q in repo.queries)


def test_streaming_only_build_still_yields_live_price():
    # Batch gold_market_movers missing (the local streaming-only build), but the live price shows.
    repo = _FakeRepo(rows=[])
    result = GoldService(get_config(), repo=repo).facts_for("What moved Bitcoin this week?")
    assert result.used
    assert any("live trade price" in f.label for f in result.facts)


def test_batch_failure_is_non_fatal_when_streaming_present():
    repo = _FakeRepo(raise_on_latest=True)
    result = GoldService(get_config(), repo=repo).facts_for("bitcoin price")
    # the batch facts still come back; the missing streaming mart does not break the answer
    assert result.used
    assert not any("live trade price" in f.label for f in result.facts)


def test_movers_intent_without_coin():
    rows = [
        {
            "coin_name": "Solana",
            "coin_symbol": "sol",
            "current_price": 150.0,
            "price_change_pct_24h": 12.3,
            "change_rank_24h": 1,
        },
        {
            "coin_name": "Cardano",
            "coin_symbol": "ada",
            "current_price": 0.5,
            "price_change_pct_24h": 9.1,
            "change_rank_24h": 2,
        },
    ]
    repo = _FakeRepo(rows=rows)
    result = GoldService(get_config(), repo=repo).facts_for("what are today's top movers?")
    assert result.intent == "movers"
    assert len(result.facts) == 2
    assert "ORDER BY change_rank_24h" in repo.queries[0]


def test_no_intent_skips_query():
    repo = _FakeRepo()
    result = GoldService(get_config(), repo=repo).facts_for("summarise the latest crypto headlines")
    assert result.intent == "none"
    assert not result.used
    assert repo.queries == []  # no Gold query issued


class _FailingRepo:
    """A repo whose primary query fails (e.g. Gold DuckDB not built / Athena error)."""

    def run(self, _sql):
        raise GoldQueryError("gold engine unavailable")


def test_gold_outage_degrades_gracefully_not_fatal():
    # A Gold failure on the PRIMARY query must degrade to no-facts (docs-only), never raise —
    # the assistant should still answer from retrieved documents.
    result = GoldService(get_config(), repo=_FailingRepo()).facts_for("What moved Bitcoin?")
    assert result.intent == "coin_stats"
    assert not result.used
    assert result.facts == ()
