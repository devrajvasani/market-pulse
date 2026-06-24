"""Critical tests for RAG config parsing + range validation."""

import pytest

from rag.app.core.config import get_config
from rag.app.core.errors import ConfigError


def test_defaults_build_cleanly(monkeypatch):
    for var in ("RAG_CHUNK_SIZE", "RAG_CHUNK_OVERLAP", "RAG_TOP_K"):
        monkeypatch.delenv(var, raising=False)
    cfg = get_config()
    assert cfg.chunk_size > 0
    assert 0 <= cfg.chunk_overlap < cfg.chunk_size
    assert cfg.top_k >= 1


def test_zero_chunk_size_raises_configerror(monkeypatch):
    monkeypatch.setenv("RAG_CHUNK_SIZE", "0")
    with pytest.raises(ConfigError):
        get_config()


def test_overlap_not_less_than_size_raises_configerror(monkeypatch):
    monkeypatch.setenv("RAG_CHUNK_SIZE", "100")
    monkeypatch.setenv("RAG_CHUNK_OVERLAP", "100")
    with pytest.raises(ConfigError):
        get_config()


def test_top_k_below_one_raises_configerror(monkeypatch):
    monkeypatch.setenv("RAG_TOP_K", "0")
    with pytest.raises(ConfigError):
        get_config()
