"""Critical tests for the assistant orchestrator (fake retrieval, gold, and LLM)."""

import pytest

from rag.app.core.config import get_config
from rag.app.core.errors import IndexNotFoundError, LLMError
from rag.app.domain.models import Chunk
from rag.app.services.assistant import AssistantService
from rag.app.services.gold import GoldFact, GoldResult
from rag.app.services.retrieval import RetrievedChunk


class _FakeRetrieval:
    def __init__(self, hits, *, error=None):
        self._hits = hits
        self._error = error
        self.last_k = "unset"

    def retrieve(self, _question, k=None):
        self.last_k = k
        if self._error is not None:
            raise self._error
        return self._hits


class _FakeGold:
    def __init__(self, result):
        self._result = result

    def facts_for(self, _question):
        return self._result


class _FakeLLM:
    def __init__(self, *, stream_error=None):
        self.prompt = None
        self.system = None
        self._stream_error = stream_error

    def generate(self, prompt, *, system=None):
        self.prompt, self.system = prompt, system
        return "Bitcoin rose 8.2% this week [1]."

    def generate_stream(self, prompt, *, system=None):
        self.prompt, self.system = prompt, system
        yield "Bitcoin "
        if self._stream_error is not None:
            raise self._stream_error
        yield "rose."

    def embed(self, texts):  # pragma: no cover - not used here
        return [[0.0] for _ in texts]


def _chunk() -> Chunk:
    return Chunk(
        chunk_id="c1",
        doc_id="d1",
        index=0,
        source="example.test",
        title="BTC rallies",
        url="https://example.test/btc",
        text="Bitcoin rallied strongly this week.",
    )


def _service(retrieval, gold, llm):
    return AssistantService(get_config(), retrieval=retrieval, gold=gold, llm=llm)


def test_answer_combines_chunks_and_gold():
    gold = GoldResult(
        intent="coin_stats",
        facts=(GoldFact("Bitcoin 7d change", "+8.20%", 8.2, "%"),),
        summary="Bitcoin (BTC): price $65,000.00, 24h +2.10%, 7d +8.20%.",
    )
    llm = _FakeLLM()
    answer = _service(_FakeRetrieval([RetrievedChunk(_chunk(), 0.9)]), _FakeGold(gold), llm).answer(
        "What moved Bitcoin?"
    )

    assert answer.used_gold
    assert len(answer.citations) == 1
    assert answer.citations[0].title == "BTC rallies"
    assert "7d +8.20%" in llm.prompt
    assert "[1] BTC rallies" in llm.prompt
    assert llm.system


def test_answer_threads_top_k_to_retrieval():
    retrieval = _FakeRetrieval([])
    _service(retrieval, _FakeGold(GoldResult(intent="none")), _FakeLLM()).answer("hi", top_k=7)
    assert retrieval.last_k == 7


def test_stream_emits_sources_then_deltas_then_done():
    gold = GoldResult(
        intent="coin_stats",
        facts=(GoldFact("Bitcoin price", "$65,000.00", 65000.0, "USD"),),
        summary="Bitcoin (BTC): price $65,000.00.",
    )
    svc = _service(_FakeRetrieval([RetrievedChunk(_chunk(), 0.9)]), _FakeGold(gold), _FakeLLM())
    events = list(svc.stream(svc.prepare("btc?")))

    assert events[0]["type"] == "sources"
    assert events[0]["citations"][0]["title"] == "BTC rallies"
    assert events[0]["gold"]["used"] is True
    deltas = [e["text"] for e in events if e["type"] == "delta"]
    assert "".join(deltas) == "Bitcoin rose."
    assert events[-1] == {"type": "done", "used_gold": True}


def test_stream_emits_error_event_on_midstream_llm_failure():
    llm = _FakeLLM(stream_error=LLMError("ollama died"))
    svc = _service(
        _FakeRetrieval([RetrievedChunk(_chunk(), 0.9)]), _FakeGold(GoldResult("none")), llm
    )
    events = list(svc.stream(svc.prepare("btc?")))

    assert events[0]["type"] == "sources"
    assert events[1] == {"type": "delta", "text": "Bitcoin "}  # partial output before the failure
    assert events[-1]["type"] == "error"
    assert events[-1]["code"] == "llm_error"
    assert not any(e["type"] == "done" for e in events)


def test_prepare_propagates_retrieval_error():
    # Retrieval errors must surface (you can't answer without the index) — not be swallowed.
    svc = _service(
        _FakeRetrieval([], error=IndexNotFoundError("no index")),
        _FakeGold(GoldResult(intent="none")),
        _FakeLLM(),
    )
    with pytest.raises(IndexNotFoundError):
        svc.prepare("hi")


def test_answer_without_context_still_answers():
    llm = _FakeLLM()
    answer = _service(_FakeRetrieval([]), _FakeGold(GoldResult(intent="none")), llm).answer("hello")

    assert not answer.used_gold
    assert answer.citations == ()
    assert "(no relevant news found)" in llm.prompt
    assert "(no live metrics relevant to this question)" in llm.prompt
