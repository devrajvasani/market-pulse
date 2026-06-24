"""The LLMHandler port — the embeddings + generation interface the RAG service depends on.

The assistant calls this interface without knowing which provider answers. A config-driven factory
(:func:`rag.app.llm.factory.get_llm_handler`) builds a composite that routes embeddings and
generation to the providers chosen in ``rag/config/llm.yaml`` (Ollama, Gemini, OpenAI, Anthropic,
Groq, or Bedrock), each with an optional fallback. Keeping the port here (inside ``rag/``) is what
lets the whole package be lifted into its own repo.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterator


class LLMHandler(ABC):
    """Abstract embeddings + text-generation client (one per provider, or a composite)."""

    @property
    def embed_model_id(self) -> str:
        """The embedding model identifier (empty for generation-only handlers).

        Used to tag the FAISS index so retrieval can detect (and reject) an index built with a
        different embedder, since vector spaces are model-specific.
        """
        return ""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return one embedding vector per input text, in the same order.

        Args:
            texts: Texts to embed.

        Returns:
            A list of float vectors aligned with ``texts``.

        Raises:
            LLMError: if the provider call fails.
        """
        raise NotImplementedError

    @abstractmethod
    def generate(self, prompt: str, *, system: str | None = None) -> str:
        """Generate a complete text answer for ``prompt``.

        Args:
            prompt: The fully-formed user prompt.
            system: Optional system instruction shaping the assistant's behaviour.

        Returns:
            The model's full text response.

        Raises:
            LLMError: if the provider call fails.
        """
        raise NotImplementedError

    @abstractmethod
    def generate_stream(self, prompt: str, *, system: str | None = None) -> Iterator[str]:
        """Stream the answer for ``prompt`` as incremental text chunks (for chat UX).

        Args:
            prompt: The fully-formed user prompt.
            system: Optional system instruction.

        Yields:
            Text deltas in order; concatenating them yields the full answer.

        Raises:
            LLMError: if the provider call fails.
        """
        raise NotImplementedError
