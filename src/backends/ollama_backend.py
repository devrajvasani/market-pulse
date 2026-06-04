"""Ollama implementation of the LLMClient port (local, free)."""

from __future__ import annotations

from src.backends.llm_client import LLMClient


class OllamaBackend(LLMClient):
    """Embeddings + generation via local Ollama - the free Bedrock stand-in.

    Wired up in Stage 5.
    """

    def embed(self, texts: list[str]) -> list[list[float]]:
        """See :meth:`LLMClient.embed`. Placeholder until Stage 5."""
        raise NotImplementedError("OllamaBackend.embed is implemented in Stage 5 (RAG).")

    def generate(self, prompt: str) -> str:
        """See :meth:`LLMClient.generate`. Placeholder until Stage 5."""
        raise NotImplementedError("OllamaBackend.generate is implemented in Stage 5 (RAG).")
