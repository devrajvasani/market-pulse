"""Application settings and backend selection - the portability core (Section B).

Reads environment variables (loaded from ``config/environments/*.env``) and
returns the active backend implementation. Switching cloud <-> local means
changing ``APP_ENV`` and loading a different ``.env`` - never a logic rewrite.

Env is read at call time (not import time) so a process can switch targets and
so tests can set ``APP_ENV`` per case.
"""

from __future__ import annotations

import os

from src.backends.llm_client import LLMClient
from src.backends.query_engine import QueryEngine
from src.common.errors import ConfigError

PROJECT = "marketpulse"
_VALID_APP_ENVS = ("aws", "local")


def active_env() -> str:
    """Return the active deployment target (``"aws"`` or ``"local"``).

    Returns:
        The validated ``APP_ENV`` value (defaults to ``"local"``).

    Raises:
        ConfigError: If ``APP_ENV`` is set to an unsupported value.
    """
    env = os.getenv("APP_ENV", "local").lower()
    if env not in _VALID_APP_ENVS:
        raise ConfigError(f"APP_ENV must be one of {list(_VALID_APP_ENVS)}, got {env!r}.")
    return env


def environment() -> str:
    """Return the resource-environment slug used in names + tags (default ``"dev"``)."""
    return os.getenv("ENVIRONMENT", "dev")


def aws_region() -> str:
    """Return the AWS region (default ``"us-east-1"``)."""
    return os.getenv("AWS_DEFAULT_REGION", "us-east-1")


def aws_endpoint_url() -> str | None:
    """Return the AWS endpoint override (LocalStack) when set, else ``None``."""
    return os.getenv("AWS_ENDPOINT_URL")


def bucket_name(layer: str) -> str:
    """Build a medallion bucket name per the Section B2 naming standard.

    Args:
        layer: One of ``"bronze"``, ``"silver"``, ``"gold"``.

    Returns:
        e.g. ``"marketpulse-dev-bucket-bronze"``.
    """
    return f"{PROJECT}-{environment()}-bucket-{layer}"


def get_query_engine() -> QueryEngine:
    """Return the active QueryEngine backend (Athena on ``aws``, DuckDB on ``local``)."""
    if active_env() == "aws":
        from src.backends.athena_backend import AthenaBackend

        return AthenaBackend()
    from src.backends.duckdb_backend import DuckDBBackend

    return DuckDBBackend()


def get_llm_client() -> LLMClient:
    """Return the active LLMClient backend (Bedrock on ``aws``, Ollama on ``local``)."""
    if active_env() == "aws":
        from src.backends.bedrock_backend import BedrockBackend

        return BedrockBackend()
    from src.backends.ollama_backend import OllamaBackend

    return OllamaBackend()
