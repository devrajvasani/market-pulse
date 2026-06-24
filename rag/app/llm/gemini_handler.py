"""Google Gemini implementation of :class:`LLMHandler` (Google AI Studio API).

Embeddings via ``:batchEmbedContents``; generation via ``:generateContent`` (and
``:streamGenerateContent?alt=sse`` for streaming). REST over httpx with the shared retry helper;
the API key is sent in the ``x-goog-api-key`` header.
"""

from __future__ import annotations

from collections.abc import Iterator

from rag.app.core.errors import LLMError
from rag.app.core.llm_config import GenParams, ResolvedProvider
from rag.app.llm import _http
from rag.app.llm.base import LLMHandler

_BASE = "https://generativelanguage.googleapis.com/v1beta"
_BLOCKING_FINISH = {"SAFETY", "RECITATION", "BLOCKLIST", "PROHIBITED_CONTENT", "SPII"}


class GeminiHandler(LLMHandler):
    """Embeddings + generation via the Gemini API."""

    def __init__(self, provider: ResolvedProvider, params: GenParams) -> None:
        """Initialise from the resolved provider (key + models) + generation params."""
        if not provider.api_key:
            raise LLMError("Gemini requires an API key (GEMINI_API_KEY).")
        self._embed_model = provider.embed_model or "text-embedding-004"
        self._gen_model = provider.gen_model or "gemini-2.0-flash"
        self._params = params
        self._headers = {"x-goog-api-key": provider.api_key, "content-type": "application/json"}

    @property
    def embed_model_id(self) -> str:
        """The Gemini embedding model id."""
        return f"gemini:{self._embed_model}"

    def embed(self, texts: list[str]) -> list[list[float]]:
        """See :meth:`LLMHandler.embed`. Uses ``:batchEmbedContents``."""
        if not texts:
            return []
        url = f"{_BASE}/models/{self._embed_model}:batchEmbedContents"
        payload = {
            "requests": [
                {"model": f"models/{self._embed_model}", "content": {"parts": [{"text": text}]}}
                for text in texts
            ]
        }
        data = _http.post_json(
            "Gemini", url, headers=self._headers, payload=payload, timeout=self._params.timeout_s
        )
        try:
            vectors = [item["values"] for item in data["embeddings"]]
        except (KeyError, TypeError) as exc:
            raise LLMError(f"Gemini embed: unexpected response shape: {data}") from exc
        if len(vectors) != len(texts):
            raise LLMError(f"Gemini embed returned {len(vectors)} vectors for {len(texts)} inputs.")
        return vectors

    def _payload(self, prompt: str, system: str | None) -> dict:
        """Build the generateContent request body."""
        body: dict = {
            "contents": [{"role": "user", "parts": [{"text": prompt}]}],
            "generationConfig": {
                "maxOutputTokens": self._params.max_output_tokens,
                "temperature": self._params.temperature,
            },
        }
        if system:
            body["systemInstruction"] = {"parts": [{"text": system}]}
        return body

    @staticmethod
    def _text_from(candidate: dict) -> str:
        """Join the text parts of one Gemini candidate."""
        parts = candidate.get("content", {}).get("parts", [])
        return "".join(part.get("text", "") for part in parts)

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        """See :meth:`LLMHandler.generate`. Uses ``:generateContent``."""
        url = f"{_BASE}/models/{self._gen_model}:generateContent"
        data = _http.post_json(
            "Gemini",
            url,
            headers=self._headers,
            payload=self._payload(prompt, system),
            timeout=self._params.timeout_s,
        )
        candidates = data.get("candidates")
        if not candidates:
            raise LLMError(f"Gemini generate returned no candidates: {data}")
        return self._text_from(candidates[0]).strip()

    def generate_stream(self, prompt: str, *, system: str | None = None) -> Iterator[str]:
        """See :meth:`LLMHandler.generate_stream`. Uses ``:streamGenerateContent?alt=sse``."""
        url = f"{_BASE}/models/{self._gen_model}:streamGenerateContent?alt=sse"
        for event in _http.iter_sse(
            "Gemini",
            url,
            headers=self._headers,
            payload=self._payload(prompt, system),
            timeout=self._params.timeout_s,
        ):
            block = event.get("promptFeedback", {}).get("blockReason")
            if block:
                raise LLMError(f"Gemini blocked the prompt ({block}).")
            for candidate in event.get("candidates", []):
                text = self._text_from(candidate)
                if text:
                    yield text
                elif candidate.get("finishReason") in _BLOCKING_FINISH:
                    raise LLMError(f"Gemini stopped without output ({candidate['finishReason']}).")
