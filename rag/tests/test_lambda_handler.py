"""Critical tests for the Lambda handlers (services monkeypatched; no AWS)."""

import json

import rag.lambda_handler as lh
from rag.app.core.errors import IndexNotFoundError
from rag.app.services.assistant import AssistantAnswer, Citation
from rag.app.services.gold import GoldFact, GoldResult


def _answer() -> AssistantAnswer:
    gold = GoldResult("coin_stats", (GoldFact("Bitcoin 7d change", "+8.20%", 8.2, "%"),), "summary")
    citation = Citation(1, "BTC", "https://x/btc", "example.test", 0.9, "snippet")
    return AssistantAnswer("Bitcoin rose 8.2% [1].", (citation,), gold, True)


class _FakeAssistant:
    def __init__(self, *, error=None):
        self._error = error

    def answer(self, _question):
        if self._error is not None:
            raise self._error
        return _answer()


def test_assistant_handler_direct_event(monkeypatch):
    monkeypatch.setattr(lh, "AssistantService", lambda *a, **k: _FakeAssistant())
    response = lh.assistant_handler({"question": "What moved Bitcoin?"})
    assert response["statusCode"] == 200
    body = json.loads(response["body"])
    assert body["used_gold"] is True
    assert body["citations"][0]["title"] == "BTC"
    assert body["gold"]["facts"][0]["value"] == "+8.20%"


def test_assistant_handler_apigw_body(monkeypatch):
    monkeypatch.setattr(lh, "AssistantService", lambda *a, **k: _FakeAssistant())
    event = {"body": json.dumps({"question": "hi"})}
    response = lh.assistant_handler(event)
    assert response["statusCode"] == 200


def test_assistant_handler_missing_question_returns_400():
    response = lh.assistant_handler({"foo": "bar"})
    assert response["statusCode"] == 400
    assert json.loads(response["body"])["code"] == "missing_question"


def test_assistant_handler_maps_rag_error(monkeypatch):
    fake = _FakeAssistant(error=IndexNotFoundError("no index"))
    monkeypatch.setattr(lh, "AssistantService", lambda *a, **k: fake)
    response = lh.assistant_handler({"question": "hi"})
    assert response["statusCode"] == 409
    assert json.loads(response["body"])["code"] == "index_not_found"


def test_assistant_handler_unhandled_error_returns_500(monkeypatch):
    class _Boom:
        def answer(self, _question):
            raise ValueError("boom")

    monkeypatch.setattr(lh, "AssistantService", lambda *a, **k: _Boom())
    response = lh.assistant_handler({"question": "hi"})
    assert response["statusCode"] == 500
    assert json.loads(response["body"])["code"] == "internal_error"


def test_ingest_handler(monkeypatch):
    class _Result:
        feeds, documents, new = 1, 5, 3

    class _FakeIngest:
        def __init__(self, *a, **k):
            pass

        def ingest(self):
            return _Result()

    monkeypatch.setattr(lh, "IngestionService", _FakeIngest)
    response = lh.ingest_handler({})
    assert response["statusCode"] == 200
    assert json.loads(response["body"]) == {"feeds": 1, "documents": 5, "new": 3}
