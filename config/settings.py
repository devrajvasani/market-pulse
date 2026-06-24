"""Application settings and backend selection - the portability core (Section B).

On import it auto-loads env files (root ``.env`` for gitignored local secrets,
then ``config/environments/<APP_ENV>.env`` for committed non-secret config)
without overriding anything already set in the real environment. It then
returns the active backend implementation; switching cloud <-> local is just
changing ``APP_ENV`` - never a logic rewrite.

Values are read at call time (not cached) so a process can switch targets and
so tests can set ``APP_ENV`` per case.
"""

from __future__ import annotations

import os
from pathlib import Path

try:
    from dotenv import load_dotenv
except ImportError:  # not installed on Lambda; there env vars are set directly, no .env files
    load_dotenv = None  # type: ignore[assignment]

from src.backends.query_engine import QueryEngine
from src.common.errors import ConfigError

PROJECT = "marketpulse"
_VALID_APP_ENVS = ("aws", "local")
_REPO_ROOT = Path(__file__).resolve().parents[1]


def _load_env_files() -> None:
    """Load env files into ``os.environ`` once, at import.

    Precedence (highest first): the real shell/OS environment > root ``.env``
    (gitignored local secrets + overrides) > ``config/environments/<APP_ENV>.env``
    (committed, non-secret mode config). ``override=False`` so an already-set
    variable (shell, CI, Lambda) always wins; missing files are ignored.
    """
    if load_dotenv is None:
        return  # e.g. on Lambda: no .env files; env vars are provided directly
    load_dotenv(_REPO_ROOT / ".env", override=False)
    env = (os.getenv("APP_ENV") or "").strip().lower() or "local"
    load_dotenv(_REPO_ROOT / "config" / "environments" / f"{env}.env", override=False)


_load_env_files()


def active_env() -> str:
    """Return the active deployment target (``"aws"`` or ``"local"``).

    Returns:
        The validated ``APP_ENV`` value (defaults to ``"local"``).

    Raises:
        ConfigError: If ``APP_ENV`` is set to an unsupported value.
    """
    env = (os.getenv("APP_ENV") or "").strip().lower() or "local"
    if env not in _VALID_APP_ENVS:
        raise ConfigError(f"APP_ENV must be one of {list(_VALID_APP_ENVS)}, got {env!r}.")
    return env


def environment() -> str:
    """Return the resource-environment slug used in names + tags (default ``"dev"``)."""
    return (os.getenv("ENVIRONMENT") or "").strip() or "dev"


def aws_region() -> str:
    """Return the AWS region (default ``"us-east-1"``)."""
    return (os.getenv("AWS_DEFAULT_REGION") or "").strip() or "us-east-1"


def aws_endpoint_url() -> str | None:
    """Return the AWS endpoint override (LocalStack) when set, else ``None``."""
    return (os.getenv("AWS_ENDPOINT_URL") or "").strip() or None


def bucket_name(layer: str) -> str:
    """Build a medallion bucket name per the Section B2 naming standard.

    Args:
        layer: One of ``"bronze"``, ``"silver"``, ``"gold"``.

    Returns:
        e.g. ``"marketpulse-dev-bucket-bronze"``.
    """
    return f"{PROJECT}-{environment()}-bucket-{layer}"


def bronze_bucket() -> str:
    """Return the Bronze bucket name.

    Terraform sets ``BRONZE_BUCKET`` on the Lambda (real bucket names carry a unique
    suffix). For local runs, export it from ``terraform output``.

    Raises:
        ConfigError: if ``BRONZE_BUCKET`` is not set.
    """
    name = (os.getenv("BRONZE_BUCKET") or "").strip()
    if not name:
        raise ConfigError(
            "BRONZE_BUCKET is not set. Terraform sets it on the Lambda; "
            "for local runs export it from `terraform output`."
        )
    return name


def marketdata_secret_name() -> str:
    """Return the market-data API-key secret name (deterministic; env-overridable)."""
    return (os.getenv("MARKETDATA_SECRET_NAME") or "").strip() or (
        f"{PROJECT}-{environment()}-secret-marketdata-apikey"
    )


def gold_bucket() -> str:
    """Return the Gold bucket name (the durable serving layer; also holds the RAG index).

    Terraform sets ``GOLD_BUCKET`` on the function (real bucket names carry a unique suffix);
    for local runs export it from ``terraform output``.

    Raises:
        ConfigError: if ``GOLD_BUCKET`` is not set.
    """
    name = (os.getenv("GOLD_BUCKET") or "").strip()
    if not name:
        raise ConfigError(
            "GOLD_BUCKET is not set. Terraform sets it on the function; "
            "for local runs export it from `terraform output`."
        )
    return name


def gold_duckdb_path() -> str:
    """Return the path to the dbt-materialised local Gold DuckDB database (the Gold Athena twin).

    On the local (``duckdb``) dbt target, dbt writes the Gold marts into this database file
    (see ``src/transform/dbt/profiles.yml``); the RAG assistant reads it for live numbers.
    Override with ``GOLD_DUCKDB_PATH``.
    """
    override = (os.getenv("GOLD_DUCKDB_PATH") or "").strip()
    if override:
        return override
    return str(_REPO_ROOT / "src" / "transform" / "dbt" / "target" / "marketpulse.duckdb")


def bronze_data_location() -> str:
    """Return the S3 URI of the Bronze prices prefix (the DuckDB twin reads this).

    Returns:
        e.g. ``"s3://marketpulse-dev-bucket-bronze-…/prices"`` (no trailing slash).
    """
    return f"s3://{bronze_bucket()}/prices"


def trades_stream_name() -> str:
    """Return the Kinesis trades-stream name (default ``marketpulse-dev-stream-trades``)."""
    return (os.getenv("STREAM_NAME") or "").strip() or f"{PROJECT}-{environment()}-stream-trades"


def athena_database() -> str:
    """Return the Glue/Athena database name (default ``marketpulse_dev``; env-overridable)."""
    return (os.getenv("ATHENA_DATABASE") or "").strip() or f"{PROJECT}_{environment()}"


def athena_workgroup() -> str:
    """Return the Athena workgroup name (default ``marketpulse-dev-athena-analytics``)."""
    return (os.getenv("ATHENA_WORKGROUP") or "").strip() or (
        f"{PROJECT}-{environment()}-athena-analytics"
    )


def get_query_engine() -> QueryEngine:
    """Return the active QueryEngine over **Bronze** (Athena on ``aws``, DuckDB on ``local``)."""
    if active_env() == "aws":
        from src.backends.athena_backend import AthenaBackend

        return AthenaBackend()
    from src.backends.duckdb_backend import DuckDBBackend

    return DuckDBBackend()


def get_gold_query_engine() -> QueryEngine:
    """Return a QueryEngine over the **Gold** marts (the RAG assistant's live-numbers source).

    On ``aws`` this is Athena over the Glue catalog (Gold Iceberg tables). Locally, Gold is
    materialised by dbt into a DuckDB database file, so we open that read-only — the Bronze
    Parquet view is irrelevant for Gold. The LLM adapter now lives in the standalone ``rag/``
    project; this Gold QueryEngine is the only backend seam it shares with the core.
    """
    if active_env() == "aws":
        from src.backends.athena_backend import AthenaBackend

        return AthenaBackend()
    from src.backends.duckdb_backend import DuckDBBackend

    return DuckDBBackend(database_path=gold_duckdb_path())
