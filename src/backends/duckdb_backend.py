"""DuckDB implementation of the QueryEngine port (local, free)."""

from __future__ import annotations

from typing import Any

from src.backends.query_engine import QueryEngine


class DuckDBBackend(QueryEngine):
    """Run SQL on local DuckDB - the free Athena stand-in. Wired up in Stage 2/3."""

    def run_sql(self, sql: str) -> list[dict[str, Any]]:
        """See :meth:`QueryEngine.run_sql`. Placeholder until Stage 2/3."""
        raise NotImplementedError(
            "DuckDBBackend.run_sql is implemented in Stage 2/3 (local query path)."
        )
