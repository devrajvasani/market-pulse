"""LLMClient port - the embeddings + generation interface for the RAG assistant.

The assistant calls this interface without knowing whether it talks to Amazon
Bedrock (cloud) or Ollama (local). See Section B.
"""

from __future__ import annotations

from abc import ABC, abstractmethod


class LLMClient(ABC):
    """Abstract LLM client (BedrockBackend in cloud, OllamaBackend local)."""

    @abstractmethod
    def embed(self, texts: list[str]) -> list[list[float]]:
        """Return an embedding vector for each input text.

        Args:
            texts: Texts to embed.

        Returns:
            One float vector per input text, in the same order.
        """
        raise NotImplementedError

    @abstractmethod
    def generate(self, prompt: str) -> str:
        """Generate a text completion for a prompt.

        Args:
            prompt: The fully-formed prompt.

        Returns:
            The model's text response.
        """
        raise NotImplementedError
