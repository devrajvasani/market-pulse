"""Ollama implementation of :class:`LLMHandler` — Ollama Cloud (Turbo) or a local server.

Talks to the Ollama REST API using only the standard library (``urllib``) plus a tiny retry/backoff
— same dependency-light idiom as the batch ingestion client. The default host is **Ollama Cloud**
(``https://ollama.com``) with an ``OLLAMA_API_KEY`` bearer token; point ``OLLAMA_HOST`` at
``http://localhost:11434`` for a local server (no key). Embeddings use ``/api/embed`` (batch);
generation uses ``/api/generate`` (with optional native token streaming) — identical on both.
"""

from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from collections.abc import Iterator

from rag.app.core.errors import LLMError
from rag.app.core.llm_config import GenParams, ResolvedProvider
from rag.app.core.logging import get_logger
from rag.app.llm.base import LLMHandler

logger = get_logger(__name__)

_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 1
_RETRYABLE_STATUS = {429, 500, 502, 503, 504}
_DEFAULT_HOST = "http://localhost:11434"


class OllamaHandler(LLMHandler):
    """Embeddings + generation via Ollama Cloud (API key) or a local Ollama server."""

    def __init__(self, provider: ResolvedProvider, params: GenParams) -> None:
        """Initialise from the resolved provider (host + key + models) + generation params."""
        self._host = (provider.host or _DEFAULT_HOST).rstrip("/")
        self._api_key = provider.api_key  # set for Ollama Cloud; None for a local server
        self._embed_model = provider.embed_model or "nomic-embed-text"
        self._gen_model = provider.gen_model or "llama3.2:3b"
        self._timeout = params.timeout_s
        self._options = {"num_predict": params.max_output_tokens, "temperature": params.temperature}

    @property
    def embed_model_id(self) -> str:
        """The Ollama embedding model id (e.g. ``nomic-embed-text``)."""
        return f"ollama:{self._embed_model}"

    def _open(self, path: str, payload: dict) -> urllib.request.addinfourl:
        """POST JSON to ``path`` with retry/backoff; return the open response stream.

        Retries transient errors (network/timeout + HTTP 429/5xx); fails fast on other 4xx
        (e.g. a model that isn't pulled — that won't fix itself).

        Raises:
            LLMError: on a non-retryable 4xx, or after ``_MAX_ATTEMPTS`` transient failures.
        """
        url = f"{self._host}{path}"
        data = json.dumps(payload).encode("utf-8")
        headers = {"content-type": "application/json"}
        if self._api_key:  # Ollama Cloud (Turbo) bearer auth; absent for a local server
            headers["authorization"] = f"Bearer {self._api_key}"
        last_error: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            request = urllib.request.Request(url, data=data, headers=headers)
            try:
                return urllib.request.urlopen(request, timeout=self._timeout)  # noqa: S310 - fixed localhost host
            except urllib.error.HTTPError as exc:
                if exc.code not in _RETRYABLE_STATUS:
                    body = exc.read().decode("utf-8", "replace")[:500]
                    raise LLMError(
                        f"Ollama returned HTTP {exc.code} for {path}: {body} "
                        f"(is the model pulled? `ollama pull {self._gen_model}`)"
                    ) from exc
                last_error = exc  # 429/5xx — transient, retry
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
            if attempt < _MAX_ATTEMPTS:
                time.sleep(_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))
        raise LLMError(
            f"Ollama request to {path} failed after {_MAX_ATTEMPTS} attempts "
            f"(is `ollama serve` running at {self._host}?): {last_error}"
        )

    @staticmethod
    def _read_json(resp: urllib.request.addinfourl, path: str) -> dict:
        """Read + parse a JSON response body, wrapping malformed bodies as LLMError."""
        raw = resp.read()
        try:
            return json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            raise LLMError(f"Ollama returned a non-JSON response for {path}.") from exc

    def embed(self, texts: list[str]) -> list[list[float]]:
        """See :meth:`LLMHandler.embed`. Uses Ollama ``/api/embed`` (batch input)."""
        if not texts:
            return []
        with self._open("/api/embed", {"model": self._embed_model, "input": texts}) as resp:
            payload = self._read_json(resp, "/api/embed")
        if payload.get("error"):  # Ollama can return HTTP 200 with an error envelope
            raise LLMError(f"Ollama embed error: {payload['error']}")
        vectors = payload.get("embeddings")
        if not isinstance(vectors, list) or len(vectors) != len(texts):
            raise LLMError(
                f"Ollama embed returned {len(vectors) if isinstance(vectors, list) else 'no'} "
                f"vectors for {len(texts)} inputs."
            )
        return vectors

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        """See :meth:`LLMHandler.generate`. Uses Ollama ``/api/generate`` (non-streaming)."""
        body: dict[str, object] = {
            "model": self._gen_model,
            "prompt": prompt,
            "stream": False,
            "options": self._options,
        }
        if system:
            body["system"] = system
        with self._open("/api/generate", body) as resp:
            payload = self._read_json(resp, "/api/generate")
        if payload.get("error"):
            raise LLMError(f"Ollama generate error: {payload['error']}")
        return str(payload.get("response", "")).strip()

    def generate_stream(self, prompt: str, *, system: str | None = None) -> Iterator[str]:
        """See :meth:`LLMHandler.generate_stream`. Yields token deltas from ``/api/generate``."""
        body: dict[str, object] = {
            "model": self._gen_model,
            "prompt": prompt,
            "stream": True,
            "options": self._options,
        }
        if system:
            body["system"] = system
        with self._open("/api/generate", body) as resp:
            for raw_line in resp:
                line = raw_line.decode("utf-8").strip()
                if not line:
                    continue
                try:
                    event = json.loads(line)
                except (json.JSONDecodeError, ValueError) as exc:
                    raise LLMError("Ollama stream returned a non-JSON line.") from exc
                if event.get("error"):
                    raise LLMError(f"Ollama generate error: {event['error']}")
                chunk = event.get("response")
                if chunk:
                    yield chunk
                if event.get("done"):
                    break
