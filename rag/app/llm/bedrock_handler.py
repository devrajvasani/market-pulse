"""Amazon Bedrock implementation of :class:`LLMHandler` — the cloud LLM.

Embeddings use Titan Text Embeddings (``invoke_model``); generation uses the model-agnostic
**Converse** API (``converse`` / ``converse_stream``) so the generation model can be swapped via
config without code changes. Throttling is retried with exponential backoff.

Auth: this handler uses an **Amazon Bedrock API key (bearer token)** rather than SigV4 — set
``AWS_BEARER_TOKEN_BEDROCK`` in your ``.env`` and boto3 picks it up automatically (no access
key/secret needed). boto3 is kept here (not raw httpx) because it parses Bedrock's binary
event-stream for streaming. Unit-tested with a stubbed client so the path is ready before a key.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from rag.app.core.errors import LLMError
from rag.app.core.llm_config import GenParams, ResolvedProvider
from rag.app.core.logging import get_logger
from rag.app.llm.base import LLMHandler

logger = get_logger(__name__)

_MAX_ATTEMPTS = 4
_BACKOFF_BASE_SECONDS = 1
_THROTTLE_CODES = {
    "ThrottlingException",
    "TooManyRequestsException",
    "ServiceQuotaExceededException",
}


class BedrockHandler(LLMHandler):
    """Embeddings + generation via Amazon Bedrock (Titan embeddings + Converse generation)."""

    def __init__(
        self, provider: ResolvedProvider, params: GenParams, client: Any | None = None
    ) -> None:
        """Initialise from the resolved provider (region + models) + params; client for tests."""
        self._embed_model = provider.embed_model or "amazon.titan-embed-text-v2:0"
        self._gen_model = provider.gen_model or "anthropic.claude-3-5-haiku-20241022-v1:0"
        self._params = params
        region = provider.region or "us-east-1"
        self._client = client or boto3.client("bedrock-runtime", region_name=region)

    @property
    def embed_model_id(self) -> str:
        """The Bedrock (Titan) embedding model id."""
        return f"bedrock:{self._embed_model}"

    def _with_retry(self, label: str, call: Any) -> Any:
        """Run ``call``; retry throttling with backoff, wrap other failures as LLMError."""
        last_error: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            try:
                return call()
            except ClientError as exc:
                code = exc.response.get("Error", {}).get("Code", "")
                last_error = exc
                if code in _THROTTLE_CODES and attempt < _MAX_ATTEMPTS:
                    time.sleep(_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))
                    continue
                raise LLMError(f"Bedrock {label} failed [{code or 'ClientError'}]: {exc}") from exc
            except BotoCoreError as exc:
                raise LLMError(f"Bedrock {label} failed [{type(exc).__name__}]: {exc}") from exc
        raise LLMError(f"Bedrock {label} failed after {_MAX_ATTEMPTS} attempts: {last_error}")

    def embed(self, texts: list[str]) -> list[list[float]]:
        """See :meth:`LLMHandler.embed`. Titan embeds one text per call, so we loop."""
        vectors: list[list[float]] = []
        for text in texts:
            body = json.dumps({"inputText": text})

            def _call(body: str = body) -> dict:
                resp = self._client.invoke_model(
                    modelId=self._embed_model,
                    body=body,
                    accept="application/json",
                    contentType="application/json",
                )
                return json.loads(resp["body"].read())

            payload = self._with_retry("embed", _call)
            try:
                vectors.append(payload["embedding"])
            except (KeyError, TypeError) as exc:
                raise LLMError(
                    f"Bedrock embed returned an unexpected response shape: {exc}"
                ) from exc
        return vectors

    def _messages(self, prompt: str, system: str | None) -> dict[str, Any]:
        """Build the Converse request kwargs shared by generate + generate_stream."""
        kwargs: dict[str, Any] = {
            "modelId": self._gen_model,
            "messages": [{"role": "user", "content": [{"text": prompt}]}],
            "inferenceConfig": {
                "maxTokens": self._params.max_output_tokens,
                "temperature": self._params.temperature,
            },
        }
        if system:
            kwargs["system"] = [{"text": system}]
        return kwargs

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        """See :meth:`LLMHandler.generate`. Uses the Converse API (non-streaming)."""
        kwargs = self._messages(prompt, system)
        response = self._with_retry("generate", lambda: self._client.converse(**kwargs))
        try:
            blocks = response["output"]["message"]["content"]
            return "".join(b.get("text", "") for b in blocks).strip()
        except (KeyError, TypeError) as exc:
            raise LLMError(
                f"Bedrock generate returned an unexpected response shape: {exc}"
            ) from exc

    def generate_stream(self, prompt: str, *, system: str | None = None) -> Iterator[str]:
        """See :meth:`LLMHandler.generate_stream`. Uses Converse streaming deltas."""
        kwargs = self._messages(prompt, system)
        response = self._with_retry(
            "generate_stream", lambda: self._client.converse_stream(**kwargs)
        )
        # The event stream is materialised lazily; errors can surface mid-iteration.
        try:
            for event in response["stream"]:
                delta = event.get("contentBlockDelta", {}).get("delta", {})
                text = delta.get("text")
                if text:
                    yield text
        except (ClientError, BotoCoreError) as exc:
            raise LLMError(f"Bedrock generate_stream failed mid-stream: {exc}") from exc
