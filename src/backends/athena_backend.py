"""Amazon Athena implementation of the QueryEngine port (cloud)."""

from __future__ import annotations

from typing import Any

from src.backends.query_engine import QueryEngine


class AthenaBackend(QueryEngine):
    """Run SQL on Amazon Athena. Wired up in Stage 2/3."""

    def run_sql(self, sql: str) -> list[dict[str, Any]]:
        """See :meth:`QueryEngine.run_sql`. Placeholder until Stage 2/3."""
        raise NotImplementedError(
            "AthenaBackend.run_sql is implemented in Stage 2/3 (catalog + SQL ELT)."
        )
