"""Critical tests for the per-provider LLM handlers (offline; httpx/urllib/boto3 mocked)."""

import json
import urllib.error
import urllib.request

import httpx
import pytest
from botocore.exceptions import ClientError

import rag.app.llm._http as http_mod
import rag.app.llm.ollama_handler as oh
from rag.app.core.errors import LLMError
from rag.app.core.llm_config import GenParams, ResolvedProvider
from rag.app.llm.anthropic_handler import AnthropicHandler
from rag.app.llm.bedrock_handler import BedrockHandler
from rag.app.llm.gemini_handler import GeminiHandler
from rag.app.llm.groq_handler import GroqHandler
from rag.app.llm.openai_handler import OpenAIHandler

_PARAMS = GenParams(max_output_tokens=128, temperature=0.2, timeout_s=30)


def _provider(name: str, **kw) -> ResolvedProvider:
    return ResolvedProvider(
        name=name,
        api_key=kw.get("api_key"),
        host=kw.get("host"),
        region=kw.get("region"),
        embed_model=kw.get("embed_model"),
        gen_model=kw.get("gen_model"),
    )


# ── httpx fakes (for the REST providers) ────────────────────────────────────────
class _Resp:
    def __init__(self, status=200, payload=None, text="", nonjson=False):
        self.status_code = status
        self._payload = payload
        self.text = text
        self._nonjson = nonjson

    def json(self):
        if self._nonjson:
            raise ValueError("not json")
        return self._payload


class _StreamResp:
    def __init__(self, status=200, lines=None, body=b""):
        self.status_code = status
        self._lines = lines or []
        self._body = body

    def iter_lines(self):
        yield from self._lines

    def read(self):
        return self._body


class _StreamCtx:
    def __init__(self, resp):
        self._resp = resp

    def __enter__(self):
        return self._resp

    def __exit__(self, *_):
        return False


def _patch_post(monkeypatch, *responses):
    it = iter(responses)
    monkeypatch.setattr(httpx, "post", lambda *a, **k: next(it))


def _patch_stream(monkeypatch, resp):
    monkeypatch.setattr(httpx, "stream", lambda *a, **k: _StreamCtx(resp))


def _sse(obj) -> str:
    return f"data: {json.dumps(obj)}"


@pytest.fixture(autouse=True)
def _no_backoff(monkeypatch):
    monkeypatch.setattr(http_mod.time, "sleep", lambda _s: None)
    monkeypatch.setattr(oh.time, "sleep", lambda _s: None)


# ── Ollama (urllib mocked) ──────────────────────────────────────────────────────
class _OllamaResp:
    def __init__(self, body=b"", lines=None):
        self._body = body
        self._lines = lines or []

    def read(self):
        return self._body

    def __iter__(self):
        return iter(self._lines)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False


def _ollama() -> oh.OllamaHandler:
    return oh.OllamaHandler(
        _provider("ollama", host="http://x:11434", embed_model="nomic-embed-text", gen_model="m"),
        _PARAMS,
    )


def test_ollama_embed_and_model_id(monkeypatch):
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *a, **k: _OllamaResp(json.dumps({"embeddings": [[0.1]]}).encode()),
    )
    handler = _ollama()
    assert handler.embed(["hi"]) == [[0.1]]
    assert handler.embed_model_id == "ollama:nomic-embed-text"


def test_ollama_generate_and_stream(monkeypatch):
    monkeypatch.setattr(
        urllib.request,
        "urlopen",
        lambda *a, **k: _OllamaResp(json.dumps({"response": " Hi "}).encode()),
    )
    assert _ollama().generate("q") == "Hi"
    lines = [
        json.dumps({"response": "He"}).encode(),
        json.dumps({"response": "llo", "done": True}).encode(),
    ]
    monkeypatch.setattr(urllib.request, "urlopen", lambda *a, **k: _OllamaResp(lines=lines))
    assert list(_ollama().generate_stream("q")) == ["He", "llo"]


def test_ollama_unreachable_raises(monkeypatch):
    def _boom(*_a, **_k):
        raise urllib.error.URLError("refused")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    with pytest.raises(LLMError):
        _ollama().embed(["hi"])


def test_ollama_cloud_sends_bearer_auth(monkeypatch):
    captured = {}

    def _capture(request, *_a, **_k):
        captured["auth"] = request.headers.get("Authorization")
        return _OllamaResp(json.dumps({"embeddings": [[0.1]]}).encode())

    monkeypatch.setattr(urllib.request, "urlopen", _capture)
    handler = oh.OllamaHandler(
        _provider("ollama", host="https://ollama.com", api_key="secret", embed_model="e"), _PARAMS
    )
    handler.embed(["hi"])
    assert captured["auth"] == "Bearer secret"  # cloud bearer token sent


# ── Gemini (httpx mocked) ───────────────────────────────────────────────────────
def _gemini() -> GeminiHandler:
    return GeminiHandler(
        _provider("gemini", api_key="k", embed_model="text-embedding-004", gen_model="g"), _PARAMS
    )


def test_gemini_requires_key():
    with pytest.raises(LLMError):
        GeminiHandler(_provider("gemini", api_key=None), _PARAMS)


def test_gemini_embed(monkeypatch):
    _patch_post(monkeypatch, _Resp(payload={"embeddings": [{"values": [0.1, 0.2]}]}))
    assert _gemini().embed(["hi"]) == [[0.1, 0.2]]
    assert _gemini().embed_model_id == "gemini:text-embedding-004"


def test_gemini_generate_and_stream(monkeypatch):
    _patch_post(
        monkeypatch, _Resp(payload={"candidates": [{"content": {"parts": [{"text": "Hi"}]}}]})
    )
    assert _gemini().generate("q") == "Hi"
    _patch_stream(
        monkeypatch,
        _StreamResp(
            lines=[
                _sse({"candidates": [{"content": {"parts": [{"text": "He"}]}}]}),
                _sse({"candidates": [{"content": {"parts": [{"text": "llo"}]}}]}),
            ]
        ),
    )
    assert list(_gemini().generate_stream("q")) == ["He", "llo"]


def test_gemini_http_error_raises(monkeypatch):
    _patch_post(monkeypatch, _Resp(status=400, text="bad request"))
    with pytest.raises(LLMError, match="400"):
        _gemini().embed(["hi"])


def test_gemini_stream_safety_block_raises(monkeypatch):
    # A 200 stream that blocks the prompt must raise (not silently yield nothing).
    _patch_stream(
        monkeypatch, _StreamResp(lines=[_sse({"promptFeedback": {"blockReason": "SAFETY"}})])
    )
    with pytest.raises(LLMError, match="blocked"):
        list(_gemini().generate_stream("q"))


def test_sse_malformed_data_line_raises(monkeypatch):
    # A corrupt `data:` line is a protocol error -> LLMError, not a silently dropped token.
    _patch_stream(monkeypatch, _StreamResp(lines=["data: {not valid json"]))
    with pytest.raises(LLMError, match="malformed SSE"):
        list(_gemini().generate_stream("q"))


# ── OpenAI + Groq (OpenAI-compatible, httpx mocked) ─────────────────────────────
def _openai() -> OpenAIHandler:
    return OpenAIHandler(_provider("openai", api_key="k"), _PARAMS)


def test_openai_embed_sorted_by_index(monkeypatch):
    _patch_post(
        monkeypatch,
        _Resp(
            payload={"data": [{"embedding": [2.0], "index": 1}, {"embedding": [1.0], "index": 0}]}
        ),
    )
    assert _openai().embed(["a", "b"]) == [[1.0], [2.0]]  # reordered by index
    assert _openai().embed_model_id == "openai:text-embedding-3-small"


def test_openai_generate_and_stream(monkeypatch):
    _patch_post(monkeypatch, _Resp(payload={"choices": [{"message": {"content": "Hi"}}]}))
    assert _openai().generate("q", system="s") == "Hi"
    _patch_stream(
        monkeypatch,
        _StreamResp(
            lines=[
                _sse({"choices": [{"delta": {"content": "He"}}]}),
                _sse({"choices": [{"delta": {"content": "llo"}}]}),
                "data: [DONE]",
            ]
        ),
    )
    assert list(_openai().generate_stream("q")) == ["He", "llo"]


def test_groq_is_generation_only(monkeypatch):
    groq = GroqHandler(_provider("groq", api_key="k"), _PARAMS)
    assert groq.embed_model_id == ""
    with pytest.raises(LLMError, match="does not support embeddings"):
        groq.embed(["hi"])
    _patch_post(monkeypatch, _Resp(payload={"choices": [{"message": {"content": "fast"}}]}))
    assert groq.generate("q") == "fast"


# ── Anthropic (httpx mocked) ────────────────────────────────────────────────────
def _anthropic() -> AnthropicHandler:
    return AnthropicHandler(_provider("anthropic", api_key="k", gen_model="claude"), _PARAMS)


def test_anthropic_no_embeddings():
    with pytest.raises(LLMError, match="embeddings"):
        _anthropic().embed(["hi"])
    assert _anthropic().embed_model_id == ""


def test_anthropic_generate_and_stream(monkeypatch):
    _patch_post(monkeypatch, _Resp(payload={"content": [{"type": "text", "text": "Hi"}]}))
    assert _anthropic().generate("q", system="s") == "Hi"
    _patch_stream(
        monkeypatch,
        _StreamResp(
            lines=[
                _sse(
                    {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "He"}}
                ),
                _sse(
                    {"type": "content_block_delta", "delta": {"type": "text_delta", "text": "llo"}}
                ),
                _sse({"type": "message_stop"}),
            ]
        ),
    )
    assert list(_anthropic().generate_stream("q")) == ["He", "llo"]


def test_anthropic_stream_error_event_raises(monkeypatch):
    _patch_stream(
        monkeypatch,
        _StreamResp(lines=[_sse({"type": "error", "error": {"message": "overloaded"}})]),
    )
    with pytest.raises(LLMError, match="overloaded"):
        list(_anthropic().generate_stream("q"))


# ── Bedrock (boto3 client stubbed) ──────────────────────────────────────────────
class _Body:
    def __init__(self, payload):
        self._payload = payload

    def read(self):
        return json.dumps(self._payload).encode()


class _FakeBedrock:
    def invoke_model(self, **_kw):
        return {"body": _Body({"embedding": [0.1, 0.2, 0.3]})}

    def converse(self, **_kw):
        return {"output": {"message": {"content": [{"text": "Hi"}]}}}

    def converse_stream(self, **_kw):
        return {
            "stream": [
                {"contentBlockDelta": {"delta": {"text": "He"}}},
                {"contentBlockDelta": {"delta": {"text": "llo"}}},
                {"messageStop": {}},
            ]
        }


def _bedrock(client) -> BedrockHandler:
    return BedrockHandler(
        _provider("bedrock", region="us-east-1", embed_model="titan", gen_model="claude"),
        _PARAMS,
        client=client,
    )


def test_bedrock_embed_generate_stream():
    handler = _bedrock(_FakeBedrock())
    assert handler.embed(["a", "b"]) == [[0.1, 0.2, 0.3], [0.1, 0.2, 0.3]]
    assert handler.embed_model_id == "bedrock:titan"
    assert handler.generate("q") == "Hi"
    assert list(handler.generate_stream("q")) == ["He", "llo"]


def test_bedrock_retries_throttling_then_succeeds(monkeypatch):
    monkeypatch.setattr("rag.app.llm.bedrock_handler.time.sleep", lambda _s: None)

    class _ThrottleOnce:
        def __init__(self):
            self.calls = 0

        def converse(self, **_kw):
            self.calls += 1
            if self.calls < 2:
                raise ClientError({"Error": {"Code": "ThrottlingException"}}, "Converse")
            return {"output": {"message": {"content": [{"text": "ok"}]}}}

    assert _bedrock(_ThrottleOnce()).generate("q") == "ok"
