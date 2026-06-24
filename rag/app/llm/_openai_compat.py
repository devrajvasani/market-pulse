"""Base handler for OpenAI-compatible chat APIs (shared by OpenAI and Groq).

Both expose the same ``/chat/completions`` (+ SSE streaming) and ``/embeddings`` shapes, so the
provider-specific subclasses only set the base URL, default models, and whether embeddings exist.
REST over httpx with the shared retry helper; the API key is a Bearer token.
"""

from __future__ import annotations

from collections.abc import Iterator

from rag.app.core.errors import LLMError
from rag.app.core.llm_config import GenParams, ResolvedProvider
from rag.app.llm import _http
from rag.app.llm.base import LLMHandler


class OpenAICompatHandler(LLMHandler):
    """Generation (and optionally embeddings) via an OpenAI-compatible endpoint."""

    provider_name: str = "OpenAI"
    base_url: str = "https://api.openai.com/v1"
    supports_embeddings: bool = True
    default_gen_model: str = "gpt-4o-mini"
    default_embed_model: str | None = "text-embedding-3-small"

    def __init__(self, provider: ResolvedProvider, params: GenParams) -> None:
        """Initialise from the resolved provider (key + models) + generation params."""
        if not provider.api_key:
            raise LLMError(f"{self.provider_name} requires an API key.")
        self._gen_model = provider.gen_model or self.default_gen_model
        self._embed_model = provider.embed_model or self.default_embed_model
        self._params = params
        self._headers = {
            "authorization": f"Bearer {provider.api_key}",
            "content-type": "application/json",
        }

    @property
    def embed_model_id(self) -> str:
        """The embedding model id (empty when this provider has no embeddings API)."""
        if not self.supports_embeddings or not self._embed_model:
            return ""
        return f"{self.provider_name.lower()}:{self._embed_model}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        """See :meth:`LLMHandler.embed`. Uses ``/embeddings``."""
        if not self.supports_embeddings:
            raise LLMError(f"{self.provider_name} does not support embeddings.")
        if not self._embed_model:
            raise LLMError(f"{self.provider_name} has no embed_model configured.")
        if not texts:
            return []
        url = f"{self.base_url}/embeddings"
        payload = {"model": self._embed_model, "input": texts}
        data = _http.post_json(
            self.provider_name,
            url,
            headers=self._headers,
            payload=payload,
            timeout=self._params.timeout_s,
        )
        try:
            rows = sorted(data["data"], key=lambda row: row["index"])
            vectors = [row["embedding"] for row in rows]
        except (KeyError, TypeError) as exc:
            raise LLMError(f"{self.provider_name} embed: unexpected response shape.") from exc
        if len(vectors) != len(texts):
            raise LLMError(
                f"{self.provider_name} embed returned {len(vectors)} vectors "
                f"for {len(texts)} inputs."
            )
        return vectors

    def _payload(self, prompt: str, system: str | None, *, stream: bool) -> dict:
        """Build the chat-completions request body."""
        messages = []
        if system:
            messages.append({"role": "system", "content": system})
        messages.append({"role": "user", "content": prompt})
        return {
            "model": self._gen_model,
            "messages": messages,
            "max_tokens": self._params.max_output_tokens,
            "temperature": self._params.temperature,
            "stream": stream,
        }

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        """See :meth:`LLMHandler.generate`. Uses ``/chat/completions``."""
        url = f"{self.base_url}/chat/completions"
        data = _http.post_json(
            self.provider_name,
            url,
            headers=self._headers,
            payload=self._payload(prompt, system, stream=False),
            timeout=self._params.timeout_s,
        )
        try:
            return (data["choices"][0]["message"]["content"] or "").strip()
        except (KeyError, IndexError, TypeError) as exc:
            raise LLMError(
                f"{self.provider_name} generate: unexpected response shape: {data}"
            ) from exc

    def generate_stream(self, prompt: str, *, system: str | None = None) -> Iterator[str]:
        """See :meth:`LLMHandler.generate_stream`. Uses streaming ``/chat/completions``."""
        url = f"{self.base_url}/chat/completions"
        for event in _http.iter_sse(
            self.provider_name,
            url,
            headers=self._headers,
            payload=self._payload(prompt, system, stream=True),
            timeout=self._params.timeout_s,
        ):
            for choice in event.get("choices", []):
                text = (choice.get("delta") or {}).get("content")
                if text:
                    yield text
