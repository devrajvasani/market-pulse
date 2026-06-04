"""Amazon Bedrock implementation of the LLMClient port (cloud)."""

from __future__ import annotations

from src.backends.llm_client import LLMClient


class BedrockBackend(LLMClient):
    """Embeddings + generation via Amazon Bedrock. Wired up in Stage 5."""

    def embed(self, texts: list[str]) -> list[list[float]]:
        """See :meth:`LLMClient.embed`. Placeholder until Stage 5."""
        raise NotImplementedError("BedrockBackend.embed is implemented in Stage 5 (RAG).")

    def generate(self, prompt: str) -> str:
        """See :meth:`LLMClient.generate`. Placeholder until Stage 5."""
        raise NotImplementedError("BedrockBackend.generate is implemented in Stage 5 (RAG).")
