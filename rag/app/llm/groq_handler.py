"""Groq implementation of :class:`LLMHandler` — generation only (OpenAI-compatible, very fast)."""

from __future__ import annotations

from rag.app.llm._openai_compat import OpenAICompatHandler


class GroqHandler(OpenAICompatHandler):
    """Generation via Groq's OpenAI-compatible API (no embeddings endpoint)."""

    provider_name = "Groq"
    base_url = "https://api.groq.com/openai/v1"
    supports_embeddings = False
    default_gen_model = "llama-3.3-70b-versatile"
    default_embed_model = None
