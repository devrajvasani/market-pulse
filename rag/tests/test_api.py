"""Critical tests for the FastAPI service (TestClient + dependency overrides)."""

import pytest
from fastapi.testclient import TestClient

from rag.app.api.deps import assistant_dep
from rag.app.core.errors import ConfigError, IndexNotFoundError
from rag.app.main import create_app
from rag.app.services.assistant import AssistantAnswer, Citation
from rag.app.services.gold import GoldFact, GoldResult


class _FakeAssistant:
    """Stand-in for AssistantService covering the answer / prepare+stream paths."""

    def __init__(self, *, answer=None, error=None, stream_events=None, prepare_error=None):
        self._answer = answer
        self._error = error
        self._stream_events = stream_events or []
        self._prepare_error = prepare_error

    def answer(self, _question, top_k=None):
        if self._error is not None:
            raise self._error
        return self._answer

    def prepare(self, _question, top_k=None):
        if self._prepare_error is not None:
            raise self._prepare_error
        return object()  # opaque token handed back to stream()

    def stream(self, _prepared):
        yield from self._stream_events


def _sample_answer() -> AssistantAnswer:
    gold = GoldResult(
        intent="coin_stats",
        facts=(GoldFact("Bitcoin 7d change", "+8.20%", 8.2, "%"),),
        summary="Bitcoin (BTC): 7d +8.20%.",
    )
    citation = Citation(
        1, "BTC rallies", "https://example.test/btc", "example.test", 0.9, "Bitcoin rallied"
    )
    return AssistantAnswer("Bitcoin rose 8.2% this week [1].", (citation,), gold, True)


def _client(overrides=None) -> TestClient:
    app = create_app()
    if overrides:
        app.dependency_overrides.update(overrides)
    return TestClient(app, raise_server_exceptions=False)


def test_health_ok():
    response = _client().get("/api/v1/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"


def test_ask_returns_answer_with_citations_and_gold():
    client = _client({assistant_dep: lambda: _FakeAssistant(answer=_sample_answer())})
    response = client.post("/api/v1/ask", json={"question": "What moved Bitcoin this week?"})
    assert response.status_code == 200
    body = response.json()
    assert body["used_gold"] is True
    assert body["citations"][0]["title"] == "BTC rallies"
    assert body["gold"]["facts"][0]["value"] == "+8.20%"


def test_ask_validation_rejects_empty_question():
    client = _client({assistant_dep: lambda: _FakeAssistant(answer=_sample_answer())})
    response = client.post("/api/v1/ask", json={"question": ""})
    assert response.status_code == 422
    assert response.json()["code"] == "validation_error"


def test_ask_maps_index_not_found_to_409():
    fake = _FakeAssistant(error=IndexNotFoundError("no index built"))
    response = _client({assistant_dep: lambda: fake}).post("/api/v1/ask", json={"question": "hi"})
    assert response.status_code == 409
    assert response.json()["code"] == "index_not_found"


def test_ask_maps_unexpected_error_to_500():
    fake = _FakeAssistant(error=ValueError("boom"))
    response = _client({assistant_dep: lambda: fake}).post("/api/v1/ask", json={"question": "hi"})
    assert response.status_code == 500
    assert response.json()["code"] == "internal_error"


def test_ask_stream_emits_sse_events():
    events = [
        {
            "type": "sources",
            "citations": [],
            "gold": {"intent": "none", "used": False, "summary": "", "facts": []},
        },
        {"type": "delta", "text": "Hi"},
        {"type": "done", "used_gold": False},
    ]
    client = _client({assistant_dep: lambda: _FakeAssistant(stream_events=events)})
    response = client.post("/api/v1/ask/stream", json={"question": "hi"})
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert 'data: {"type": "sources"' in response.text
    assert '"type": "delta"' in response.text
    assert '"type": "done"' in response.text


def test_ask_stream_prep_error_maps_to_clean_http_error():
    # The fallible prep runs eagerly in the route, so an index-not-built error is a clean 409,
    # not a broken 200 stream.
    fake = _FakeAssistant(prepare_error=IndexNotFoundError("no index built"))
    response = _client({assistant_dep: lambda: fake}).post(
        "/api/v1/ask/stream", json={"question": "hi"}
    )
    assert response.status_code == 409
    assert response.json()["code"] == "index_not_found"


def test_ask_stream_surfaces_midstream_error_event():
    events = [
        {
            "type": "sources",
            "citations": [],
            "gold": {"intent": "none", "used": False, "summary": "", "facts": []},
        },
        {"type": "delta", "text": "partial"},
        {"type": "error", "code": "llm_error", "detail": "ollama died"},
    ]
    client = _client({assistant_dep: lambda: _FakeAssistant(stream_events=events)})
    response = client.post("/api/v1/ask/stream", json={"question": "hi"})
    assert response.status_code == 200
    assert '"type": "error"' in response.text
    assert "llm_error" in response.text


def test_ready_false_when_index_unconfigured(monkeypatch):
    monkeypatch.delenv("GOLD_BUCKET", raising=False)
    response = _client().get("/api/v1/ready")
    assert response.status_code == 200
    assert response.json()["ready"] is False


def test_cors_wildcard_origin_rejected(monkeypatch):
    monkeypatch.setenv("RAG_CORS_ORIGINS", "*")
    with pytest.raises(ConfigError):
        create_app()
