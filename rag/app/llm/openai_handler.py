"""OpenAI implementation of :class:`LLMHandler` (chat + embeddings)."""

from __future__ import annotations

from rag.app.llm._openai_compat import OpenAICompatHandler


class OpenAIHandler(OpenAICompatHandler):
    """Generation + embeddings via the OpenAI API."""

    provider_name = "OpenAI"
    base_url = "https://api.openai.com/v1"
    supports_embeddings = True
    default_gen_model = "gpt-4o-mini"
    default_embed_model = "text-embedding-3-small"
