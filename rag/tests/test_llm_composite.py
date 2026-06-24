"""Critical tests for CompositeLLM routing + auto-fallback behaviour."""

from collections.abc import Iterator

import pytest

from rag.app.core.errors import LLMError
from rag.app.llm.base import LLMHandler
from rag.app.llm.composite import CompositeLLM


class _Fixed(LLMHandler):
    """A handler that always succeeds with canned output."""

    def __init__(self, *, tag: str):
        self._tag = tag

    @property
    def embed_model_id(self) -> str:
        return f"{self._tag}:embed"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[1.0] for _ in texts]

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        return self._tag

    def generate_stream(self, prompt: str, *, system: str | None = None) -> Iterator[str]:
        yield self._tag


class _Fail(LLMHandler):
    """A handler that always raises LLMError (before yielding for streams)."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        raise LLMError("embed boom")

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        raise LLMError("gen boom")

    def generate_stream(self, prompt: str, *, system: str | None = None) -> Iterator[str]:
        raise LLMError("stream boom")
        yield  # pragma: no cover - makes this a generator


class _MidFail(LLMHandler):
    """Yields one token, then fails mid-stream."""

    def embed(self, texts):  # pragma: no cover - unused
        return []

    def generate(self, prompt, *, system=None):  # pragma: no cover - unused
        return ""

    def generate_stream(self, prompt: str, *, system: str | None = None) -> Iterator[str]:
        yield "He"
        raise LLMError("dropped")


class _EmptyStream(LLMHandler):
    """Streams zero tokens without erroring (a degenerate/blocked completion)."""

    def embed(self, texts):  # pragma: no cover - unused
        return []

    def generate(self, prompt, *, system=None):  # pragma: no cover - unused
        return ""

    def generate_stream(self, prompt: str, *, system: str | None = None) -> Iterator[str]:
        return iter(())  # yields nothing


def test_embed_has_no_fallback_and_propagates():
    # Embeddings never fall back (a different embedder is incompatible) -> error propagates.
    composite = CompositeLLM(embedder=_Fail(), generator=_Fixed(tag="g"))
    with pytest.raises(LLMError, match="embed boom"):
        composite.embed(["x"])


def test_generate_uses_fallback_on_failure():
    composite = CompositeLLM(
        embedder=_Fixed(tag="e"), generator=_Fail(), generate_fallback=_Fixed(tag="fb")
    )
    assert composite.generate("q") == "fb"


def test_no_fallback_propagates_error():
    composite = CompositeLLM(embedder=_Fixed(tag="e"), generator=_Fail())
    with pytest.raises(LLMError, match="gen boom"):
        composite.generate("q")


def test_both_primary_and_fallback_fail():
    composite = CompositeLLM(embedder=_Fixed(tag="e"), generator=_Fail(), generate_fallback=_Fail())
    with pytest.raises(LLMError, match="both primary and fallback"):
        composite.generate("q")


def test_stream_falls_back_before_first_token():
    composite = CompositeLLM(
        embedder=_Fixed(tag="e"), generator=_Fail(), generate_fallback=_Fixed(tag="fb")
    )
    assert list(composite.generate_stream("q")) == ["fb"]


def test_stream_does_not_fall_back_mid_stream():
    composite = CompositeLLM(
        embedder=_Fixed(tag="e"), generator=_MidFail(), generate_fallback=_Fixed(tag="fb")
    )
    collected = []
    with pytest.raises(LLMError, match="dropped"):
        for token in composite.generate_stream("q"):
            collected.append(token)
    assert collected == ["He"]  # the first token was delivered; no fallback after that


def test_stream_zero_tokens_falls_back():
    composite = CompositeLLM(
        embedder=_Fixed(tag="e"), generator=_EmptyStream(), generate_fallback=_Fixed(tag="fb")
    )
    assert list(composite.generate_stream("q")) == ["fb"]  # empty primary -> fallback


def test_stream_zero_tokens_no_fallback_returns_empty():
    composite = CompositeLLM(embedder=_Fixed(tag="e"), generator=_EmptyStream())
    assert list(composite.generate_stream("q")) == []  # empty completion is not an error


def test_embed_model_id_comes_from_embedder():
    composite = CompositeLLM(embedder=_Fixed(tag="primary"), generator=_Fixed(tag="g"))
    assert composite.embed_model_id == "primary:embed"
