"""Shared HTTP plumbing for the REST-based LLM providers (Gemini/OpenAI/Groq/Anthropic).

One place for: JSON POST with retry/backoff on transient failures, and SSE streaming that retries
only the *initial connection* (never mid-stream, which would duplicate already-yielded tokens).
Every failure is wrapped as :class:`LLMError` so the port contract holds across providers.
"""

from __future__ import annotations

import json
import time
from collections.abc import Iterator

import httpx

from rag.app.core.errors import LLMError
from rag.app.core.logging import get_logger

logger = get_logger(__name__)

_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 1
_RETRYABLE_STATUS = {408, 409, 429, 500, 502, 503, 504}


def _backoff(attempt: int) -> None:
    """Sleep with exponential backoff before the next attempt."""
    time.sleep(_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))


def post_json(
    provider: str, url: str, *, headers: dict[str, str], payload: dict, timeout: float
) -> dict:
    """POST ``payload`` as JSON and return the parsed response, retrying transient failures.

    Raises:
        LLMError: on a non-retryable HTTP error, a non-JSON body, or exhausted retries.
    """
    last: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        try:
            response = httpx.post(url, headers=headers, json=payload, timeout=timeout)
        except httpx.HTTPError as exc:
            last = exc
        else:
            if response.status_code in _RETRYABLE_STATUS:
                last = LLMError(f"{provider} HTTP {response.status_code}: {response.text[:300]}")
            elif response.status_code >= 400:
                raise LLMError(f"{provider} HTTP {response.status_code}: {response.text[:300]}")
            else:
                try:
                    return response.json()
                except (ValueError, json.JSONDecodeError) as exc:
                    raise LLMError(f"{provider} returned a non-JSON response.") from exc
        if attempt < _MAX_ATTEMPTS:
            logger.warning("%s request failed; retrying", provider, extra={"attempt": attempt})
            _backoff(attempt)
    raise LLMError(f"{provider} request failed after {_MAX_ATTEMPTS} attempts: {last}")


def iter_sse(
    provider: str, url: str, *, headers: dict[str, str], payload: dict, timeout: float
) -> Iterator[dict]:
    """Stream Server-Sent-Events, yielding the parsed JSON of each ``data:`` line.

    Retries only the initial connection (status/connect errors before any token); once tokens
    start flowing, a mid-stream failure is raised (never retried — that would duplicate output).

    Raises:
        LLMError: on a non-retryable error, a mid-stream interruption, or exhausted retries.
    """
    last: Exception | None = None
    for attempt in range(1, _MAX_ATTEMPTS + 1):
        started = False
        try:
            with httpx.stream("POST", url, headers=headers, json=payload, timeout=timeout) as resp:
                if resp.status_code in _RETRYABLE_STATUS:
                    resp.read()
                    last = LLMError(f"{provider} HTTP {resp.status_code}")
                elif resp.status_code >= 400:
                    body = resp.read().decode("utf-8", "replace")[:300]
                    raise LLMError(f"{provider} HTTP {resp.status_code}: {body}")
                else:
                    for event in _parse_sse(resp, provider):
                        started = True
                        yield event
                    return
        except httpx.HTTPError as exc:
            if started:
                raise LLMError(f"{provider} stream interrupted mid-response: {exc}") from exc
            last = exc
        if attempt < _MAX_ATTEMPTS:
            logger.warning("%s stream open failed; retrying", provider, extra={"attempt": attempt})
            _backoff(attempt)
    raise LLMError(f"{provider} stream failed after {_MAX_ATTEMPTS} attempts: {last}")


def _parse_sse(response: httpx.Response, provider: str) -> Iterator[dict]:
    """Yield the JSON payload of each ``data:`` SSE line (``[DONE]`` ends the stream).

    Blank lines, ``:`` comments, and non-``data:`` frames (e.g. Anthropic's ``event:`` lines) are
    skipped. A ``data:`` line whose body is non-empty, not ``[DONE]``, and fails to parse is a
    protocol violation — raised as :class:`LLMError` rather than silently dropped (which would lose
    tokens). All supported providers emit JSON-only ``data:`` payloads.
    """
    for raw in response.iter_lines():
        line = raw.strip()
        if not line or not line.startswith("data:"):
            continue
        data = line[len("data:") :].strip()
        if data == "[DONE]":
            break
        try:
            yield json.loads(data)
        except (ValueError, json.JSONDecodeError) as exc:
            raise LLMError(f"{provider} sent a malformed SSE data line.") from exc
