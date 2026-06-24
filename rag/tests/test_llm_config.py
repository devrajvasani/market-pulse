"""Critical tests for the YAML LLM registry: loading, env overrides, and validation."""

import pytest

from rag.app.core.errors import ConfigError
from rag.app.core.llm_config import load_llm_config
from rag.app.llm.composite import CompositeLLM
from rag.app.llm.factory import get_llm_handler

_VALID_YAML = """
embed:
  provider: ollama
generate:
  provider: ollama
max_output_tokens: 256
temperature: {temp}
http_timeout_s: 60
providers:
  ollama:
    host_env: OLLAMA_HOST
    embed_model: nomic-embed-text
    gen_model: llama3.2:3b
  gemini:
    api_key_env: GEMINI_API_KEY
    embed_model: text-embedding-004
    gen_model: gemini-2.0-flash
"""


def _write_yaml(tmp_path, temp="0.2"):
    path = tmp_path / "llm.yaml"
    path.write_text(_VALID_YAML.format(temp=temp), encoding="utf-8")
    return path


def test_load_default_registry():
    cfg = load_llm_config()  # the real rag/config/llm.yaml
    # Default: Gemini embeddings (Ollama Cloud has no embeddings API), Ollama Cloud generation.
    assert cfg.embed_provider == "gemini"
    assert cfg.gen_provider == "ollama"
    assert {"ollama", "gemini", "openai", "anthropic", "groq", "bedrock"} <= set(cfg.providers)


def test_env_overrides_active_provider(monkeypatch):
    monkeypatch.setenv("RAG_GEN_PROVIDER", "gemini")
    monkeypatch.setenv("RAG_GEN_FALLBACK", "ollama")
    cfg = load_llm_config()
    assert cfg.gen_provider == "gemini"
    assert cfg.gen_fallback == "ollama"


def test_unknown_provider_raises(monkeypatch):
    monkeypatch.setenv("RAG_GEN_PROVIDER", "nope")
    with pytest.raises(ConfigError, match="not defined"):
        load_llm_config()


def test_embed_provider_must_support_embeddings(monkeypatch):
    monkeypatch.setenv("RAG_EMBED_PROVIDER", "groq")  # groq is generation-only
    with pytest.raises(ConfigError, match="no embeddings API"):
        load_llm_config()


def test_embed_fallback_is_rejected(monkeypatch):
    # A different embedder = incompatible vector space, so an embed fallback is refused.
    monkeypatch.setenv("RAG_EMBED_FALLBACK", "gemini")
    with pytest.raises(ConfigError, match="embed fallback is not supported"):
        load_llm_config()


def test_missing_api_key_fails_fast(monkeypatch, tmp_path):
    monkeypatch.delenv("GEMINI_API_KEY", raising=False)
    cfg = load_llm_config(_write_yaml(tmp_path))
    with pytest.raises(ConfigError, match="GEMINI_API_KEY"):
        cfg.require_resolved("gemini", role="generate")


def test_placeholder_key_rejected(monkeypatch, tmp_path):
    # A leftover comment loaded as a "key" (spaces / non-ASCII) is caught with a clear error.
    monkeypatch.setenv("GEMINI_API_KEY", "# Google AI Studio (free tier) — https://x")
    cfg = load_llm_config(_write_yaml(tmp_path))
    with pytest.raises(ConfigError, match="doesn't look like a valid key"):
        cfg.require_resolved("gemini", role="generate")


def test_temperature_out_of_range_in_yaml(tmp_path):
    with pytest.raises(ConfigError, match="temperature"):
        load_llm_config(_write_yaml(tmp_path, temp="5"))


def test_missing_config_file_raises(tmp_path):
    with pytest.raises(ConfigError, match="not found"):
        load_llm_config(tmp_path / "does-not-exist.yaml")


def test_factory_builds_default_composite(monkeypatch):
    # Default = Gemini embed + Ollama Cloud generate (+ Gemini gen fallback); build only, no calls.
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")
    monkeypatch.setenv("OLLAMA_API_KEY", "test-key")
    handler = get_llm_handler()
    assert isinstance(handler, CompositeLLM)
    assert handler.embed_model_id.startswith("gemini:")


def test_factory_uses_overridden_gen_provider(monkeypatch):
    monkeypatch.setenv("RAG_GEN_PROVIDER", "gemini")  # all-Gemini: embed + generate
    monkeypatch.setenv("GEMINI_API_KEY", "test-key")  # build only; no network call
    handler = get_llm_handler()
    assert isinstance(handler, CompositeLLM)
    assert handler.embed_model_id.startswith("gemini:")
