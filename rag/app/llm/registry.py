"""Provider registry: build the concrete :class:`LLMHandler` for a resolved provider."""

from __future__ import annotations

from rag.app.core.errors import ConfigError
from rag.app.core.llm_config import GenParams, ResolvedProvider
from rag.app.llm.base import LLMHandler


def build_handler(provider: ResolvedProvider, params: GenParams) -> LLMHandler:
    """Instantiate the handler for ``provider`` (imports are local to keep startup light).

    Raises:
        ConfigError: if the provider name is unknown.
        LLMError: if the handler can't initialise (e.g. a missing API key).
    """
    name = provider.name
    if name == "ollama":
        from rag.app.llm.ollama_handler import OllamaHandler

        return OllamaHandler(provider, params)
    if name == "gemini":
        from rag.app.llm.gemini_handler import GeminiHandler

        return GeminiHandler(provider, params)
    if name == "openai":
        from rag.app.llm.openai_handler import OpenAIHandler

        return OpenAIHandler(provider, params)
    if name == "groq":
        from rag.app.llm.groq_handler import GroqHandler

        return GroqHandler(provider, params)
    if name == "anthropic":
        from rag.app.llm.anthropic_handler import AnthropicHandler

        return AnthropicHandler(provider, params)
    if name == "bedrock":
        from rag.app.llm.bedrock_handler import BedrockHandler

        return BedrockHandler(provider, params)
    raise ConfigError(f"Unknown LLM provider: {name!r}.")
