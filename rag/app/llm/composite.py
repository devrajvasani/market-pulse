"""CompositeLLM — route embeddings + generation to their providers, with generation fallback.

The services depend on a single :class:`LLMHandler`; this composite holds the chosen embed handler
and gen handler and routes calls.

Generation has an **optional fallback** (engaged on any :class:`LLMError` after the primary's own
retries; for streaming, only if the primary fails — or yields nothing — **before any token reaches
the caller**, so output is never duplicated). Embeddings have **no fallback by design**: a
different embedder produces an incompatible vector space, so silently switching would mislabel the
index and break retrieval. The embed provider is therefore the single source of ``embed_model_id``.
"""

from __future__ import annotations

from collections.abc import Iterator

from rag.app.core.errors import LLMError
from rag.app.core.logging import get_logger
from rag.app.llm.base import LLMHandler

logger = get_logger(__name__)

_UNSET = object()


class CompositeLLM(LLMHandler):
    """Delegates embed -> the embed provider, generate -> the gen provider (+ gen fallback)."""

    def __init__(
        self,
        *,
        embedder: LLMHandler,
        generator: LLMHandler,
        generate_fallback: LLMHandler | None = None,
    ) -> None:
        """Wire the active embed + gen handlers and the optional generation fallback."""
        self._embedder = embedder
        self._generator = generator
        self._generate_fallback = generate_fallback

    @property
    def embed_model_id(self) -> str:
        """The embed provider's model id — the one that actually produces every vector."""
        return self._embedder.embed_model_id

    def embed(self, texts: list[str]) -> list[list[float]]:
        """Embed via the embed provider (no fallback — a different embedder is incompatible)."""
        return self._embedder.embed(texts)

    def generate(self, prompt: str, *, system: str | None = None) -> str:
        """Generate via the gen provider, falling back to the gen fallback on LLMError."""
        try:
            return self._generator.generate(prompt, system=system)
        except LLMError as primary_exc:
            if self._generate_fallback is None:
                raise
            logger.warning(
                "LLM generate primary failed; trying fallback", extra={"error": str(primary_exc)}
            )
            try:
                return self._generate_fallback.generate(prompt, system=system)
            except LLMError as fallback_exc:
                raise LLMError(
                    f"LLM generate failed on both primary and fallback "
                    f"(primary: {primary_exc}; fallback: {fallback_exc})"
                ) from fallback_exc

    def generate_stream(self, prompt: str, *, system: str | None = None) -> Iterator[str]:
        """Stream via the gen provider; fall back if the primary errors/yields nothing pre-token."""
        stream = self._generator.generate_stream(prompt, system=system)
        try:
            first = next(stream, _UNSET)  # runs provider code up to the first token (may raise)
        except LLMError as exc:
            if self._generate_fallback is None:
                raise  # a genuine pre-token failure with no fallback -> surface it
            logger.warning(
                "LLM stream primary failed pre-token; trying fallback", extra={"error": str(exc)}
            )
            yield from self._generate_fallback.generate_stream(prompt, system=system)
            return
        if first is _UNSET:
            # Primary streamed zero tokens without erroring. Try the fallback if there is one,
            # otherwise pass the (empty) result through — an empty completion isn't an error.
            if self._generate_fallback is not None:
                logger.warning("LLM stream primary produced no tokens; trying fallback")
                yield from self._generate_fallback.generate_stream(prompt, system=system)
            return
        yield first  # type: ignore[misc]
        yield from stream  # mid-stream failures propagate (no fallback once tokens started)
