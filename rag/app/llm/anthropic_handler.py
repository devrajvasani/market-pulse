"""Anthropic implementation of :class:`LLMHandler` — generation only (Messages API).

Anthropic has no embeddings endpoint, so :meth:`embed` raises; the composite never routes
embeddings here. Generation uses ``/v1/messages`` (and its SSE event stream for streaming).
"""

from __future__ import annotations

from collections.abc import Iterator

from rag.app.core.errors import LLMError
from rag.app.core.llm_config import GenParams, ResolvedProvider
from rag.app.llm import _http
from rag.app.llm.base import LLMHandler

_BASE = "https://api.anthropic.com/v1"
_VERSION = "2023-06-01"


class AnthropicHandler(LLMHandler):
    """Generation via the Anthropic Messages API (no embeddings)."""

    def __init__(self, provider: ResolvedProvider, params: GenParams) -> None:
        """Initialise from the resolved provider (key + model) + generation params."""
        if not provider.api_key:
            raise LLMError("Anthropic requires an API key (ANTHROPIC_API_KEY).")
        self._gen_model = provider.gen_model or "claude-haiku-4-5"
        self._params = params
        self._headers = {
            "x-api-key": provider.api_key,
            "anthropic-version": _VERSION,
            "content-type": "application/json",
        }

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Anthropic has no embeddings API."""
        raise LLMError("Anthropic does not support embeddings; choose another embed provider.")

    def _payload(self, prompt: str, system: str | None, *, stream: bool) -> dict:
        """Build the Messages request body."""
        body: dict = {
            "model": self._gen_model,
            "max_tokens": self._params.max_output_tokens,
            "temperature": self._params.temperature,
            "messages": [{"role": "user", "content": prompt}],
            "stream": stream,
        }
        if system:
            body["system"] = system
        return body

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        """See :meth:`LLMHandler.generate`. Uses ``/v1/messages``."""
        data = _http.post_json(
            "Anthropic",
            f"{_BASE}/messages",
            headers=self._headers,
            payload=self._payload(prompt, system, stream=False),
            timeout=self._params.timeout_s,
        )
        try:
            blocks = data["content"]
        except (KeyError, TypeError) as exc:
            raise LLMError(f"Anthropic generate: unexpected response shape: {data}") from exc
        return "".join(b.get("text", "") for b in blocks if b.get("type") == "text").strip()

    def generate_stream(self, prompt: str, *, system: str | None = None) -> Iterator[str]:
        """See :meth:`LLMHandler.generate_stream`. Uses the Messages SSE event stream."""
        for event in _http.iter_sse(
            "Anthropic",
            f"{_BASE}/messages",
            headers=self._headers,
            payload=self._payload(prompt, system, stream=True),
            timeout=self._params.timeout_s,
        ):
            etype = event.get("type")
            if etype == "error":
                message = (event.get("error") or {}).get("message", "stream error")
                raise LLMError(f"Anthropic stream error: {message}")
            if etype == "content_block_delta":
                delta = event.get("delta") or {}
                if delta.get("type") == "text_delta" and delta.get("text"):
                    yield delta["text"]
