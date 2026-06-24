"""The one shared seam with the core project: run templated SQL over the Gold marts.

Delegates to ``config.settings.get_gold_query_engine()`` — Athena over the Glue catalog on AWS,
DuckDB over the dbt-materialised Gold database locally. This is the *only* import from outside the
``rag/`` package; when the project is extracted, swap this single module for the new home's engine.
"""

from __future__ import annotations

from typing import Any

from config import settings  # shared core seam (Athena on aws / DuckDB-on-dbt-db local)
from rag.app.core.errors import GoldQueryError
from rag.app.core.logging import get_logger

logger = get_logger(__name__)


class GoldQueryRepository:
    """Execute read-only SQL against the active Gold query engine."""

    def __init__(self, engine: object | None = None) -> None:
        """Initialise; ``engine`` is injectable for tests (defaults to the configured engine)."""
        self._engine = engine

    def run(self, sql: str) -> list[dict[str, Any]]:
        """Run ``sql`` and return rows as dicts.

        Raises:
            GoldQueryError: if the underlying query engine fails.
        """
        try:
            engine = self._engine or settings.get_gold_query_engine()
            return engine.run_sql(sql)
        except GoldQueryError:
            raise
        except (
            Exception
        ) as exc:  # wrap any engine error (TransformError, boto, duckdb) with context
            raise GoldQueryError(f"Gold query failed [{type(exc).__name__}]: {exc}") from exc
