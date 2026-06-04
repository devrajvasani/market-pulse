"""Critical tests: factory returns the right backend per APP_ENV; interface shape."""

import pytest

from config import settings
from src.backends.athena_backend import AthenaBackend
from src.backends.bedrock_backend import BedrockBackend
from src.backends.duckdb_backend import DuckDBBackend
from src.backends.llm_client import LLMClient
from src.backends.ollama_backend import OllamaBackend
from src.backends.query_engine import QueryEngine


def test_local_query_engine_is_duckdb(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    engine = settings.get_query_engine()
    assert isinstance(engine, DuckDBBackend)
    assert isinstance(engine, QueryEngine)


def test_aws_query_engine_is_athena(monkeypatch):
    monkeypatch.setenv("APP_ENV", "aws")
    engine = settings.get_query_engine()
    assert isinstance(engine, AthenaBackend)
    assert isinstance(engine, QueryEngine)


def test_local_llm_client_is_ollama(monkeypatch):
    monkeypatch.setenv("APP_ENV", "local")
    client = settings.get_llm_client()
    assert isinstance(client, OllamaBackend)
    assert isinstance(client, LLMClient)


def test_aws_llm_client_is_bedrock(monkeypatch):
    monkeypatch.setenv("APP_ENV", "aws")
    client = settings.get_llm_client()
    assert isinstance(client, BedrockBackend)
    assert isinstance(client, LLMClient)


def test_query_engines_expose_run_sql_placeholder():
    for engine in (DuckDBBackend(), AthenaBackend()):
        assert hasattr(engine, "run_sql")
        with pytest.raises(NotImplementedError):
            engine.run_sql("SELECT 1")


def test_llm_clients_expose_methods_placeholder():
    for client in (OllamaBackend(), BedrockBackend()):
        assert hasattr(client, "embed")
        assert hasattr(client, "generate")
        with pytest.raises(NotImplementedError):
            client.embed(["hello"])
        with pytest.raises(NotImplementedError):
            client.generate("hello")
