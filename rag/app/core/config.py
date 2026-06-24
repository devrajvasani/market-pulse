"""RAG assistant configuration — read from the environment, self-contained.

Mirrors the core project's "read env at call time" approach (so tests can set env per case and a
process can switch targets) while keeping the ``rag/`` package free of imports from the core
``config`` module — except the one documented seam, the Gold ``QueryEngine`` (see
``repositories/gold_query.py``). Switching cloud <-> local is just ``APP_ENV`` + a different
``.env`` — never a logic change.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from urllib.parse import urlparse

from rag.app.core.errors import ConfigError

_VALID_APP_ENVS = ("aws", "local")
_DEFAULT_FEEDS = ("https://cointelegraph.com/rss",)


def _csv(name: str, default: tuple[str, ...]) -> tuple[str, ...]:
    """Parse a comma-separated env var into a tuple, falling back to ``default``."""
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    return tuple(item.strip() for item in raw.split(",") if item.strip())


def _int(name: str, default: int) -> int:
    """Parse an int env var, falling back to ``default`` on missing/blank/invalid."""
    raw = (os.getenv(name) or "").strip()
    try:
        return int(raw) if raw else default
    except ValueError as exc:
        raise ConfigError(f"{name} must be an integer, got {raw!r}.") from exc


def _feed_urls() -> tuple[str, ...]:
    """Parse ``RAG_FEED_URLS``, allowing only ``http(s)`` schemes (SSRF guard)."""
    urls = _csv("RAG_FEED_URLS", _DEFAULT_FEEDS)
    for url in urls:
        if urlparse(url).scheme not in ("http", "https"):
            raise ConfigError(f"RAG_FEED_URLS entries must be http(s) URLs; got {url!r}.")
    return urls


@dataclass(frozen=True)
class RagConfig:
    """Immutable snapshot of the RAG service configuration for one call/request."""

    app_env: str
    aws_region: str
    aws_endpoint_url: str | None
    bronze_bucket: str | None
    gold_bucket: str | None
    index_prefix: str
    feed_urls: tuple[str, ...]
    chunk_size: int
    chunk_overlap: int
    top_k: int
    http_timeout_s: int
    cors_origins: tuple[str, ...] = field(default_factory=tuple)

    def __post_init__(self) -> None:
        """Validate numeric ranges at the boundary so misconfig fails fast with a clear error."""
        if self.chunk_size <= 0:
            raise ConfigError(f"RAG_CHUNK_SIZE must be positive, got {self.chunk_size}.")
        if not 0 <= self.chunk_overlap < self.chunk_size:
            raise ConfigError(
                f"RAG_CHUNK_OVERLAP must be in [0, RAG_CHUNK_SIZE); "
                f"got overlap={self.chunk_overlap}, size={self.chunk_size}."
            )
        if self.top_k < 1:
            raise ConfigError(f"RAG_TOP_K must be >= 1, got {self.top_k}.")
        if self.http_timeout_s <= 0:
            raise ConfigError(f"RAG_HTTP_TIMEOUT_S must be positive, got {self.http_timeout_s}.")

    @property
    def is_aws(self) -> bool:
        """True when targeting real AWS S3/Athena, else local (LocalStack + DuckDB)."""
        return self.app_env == "aws"

    def require_bronze_bucket(self) -> str:
        """Return the Bronze bucket or raise a clear ConfigError if unset."""
        if not self.bronze_bucket:
            raise ConfigError(
                "BRONZE_BUCKET is not set. Terraform sets it on the function; "
                "for local runs export it from `terraform output`."
            )
        return self.bronze_bucket

    def require_gold_bucket(self) -> str:
        """Return the Gold bucket (also holds the RAG index) or raise a clear ConfigError."""
        if not self.gold_bucket:
            raise ConfigError(
                "GOLD_BUCKET is not set. Terraform sets it on the function; "
                "for local runs export it from `terraform output`."
            )
        return self.gold_bucket


def get_config() -> RagConfig:
    """Build a :class:`RagConfig` from the current environment.

    Read fresh each call (cheap) so tests and a running process can switch targets via env.

    Raises:
        ConfigError: if ``APP_ENV`` is set to an unsupported value or a numeric var is invalid.
    """
    app_env = (os.getenv("APP_ENV") or "").strip().lower() or "local"
    if app_env not in _VALID_APP_ENVS:
        raise ConfigError(f"APP_ENV must be one of {list(_VALID_APP_ENVS)}, got {app_env!r}.")

    return RagConfig(
        app_env=app_env,
        aws_region=(os.getenv("AWS_DEFAULT_REGION") or "").strip() or "us-east-1",
        aws_endpoint_url=(os.getenv("AWS_ENDPOINT_URL") or "").strip() or None,
        bronze_bucket=(os.getenv("BRONZE_BUCKET") or "").strip() or None,
        gold_bucket=(os.getenv("GOLD_BUCKET") or "").strip() or None,
        index_prefix=(os.getenv("RAG_INDEX_PREFIX") or "").strip() or f"rag/{app_env}",
        feed_urls=_feed_urls(),
        chunk_size=_int("RAG_CHUNK_SIZE", 900),
        chunk_overlap=_int("RAG_CHUNK_OVERLAP", 150),
        top_k=_int("RAG_TOP_K", 4),
        http_timeout_s=_int("RAG_HTTP_TIMEOUT_S", 30),
        cors_origins=_csv("RAG_CORS_ORIGINS", ("http://localhost:8501",)),
    )
