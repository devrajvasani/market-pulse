"""QueryEngine port - the SQL interface every query backend implements.

The rest of the codebase runs SQL through this interface without knowing
whether it executes on AWS Athena (cloud) or DuckDB (local). See Section B.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class QueryEngine(ABC):
    """Abstract SQL query engine (AthenaBackend in cloud, DuckDBBackend local)."""

    @abstractmethod
    def run_sql(self, sql: str) -> list[dict[str, Any]]:
        """Execute a SQL statement and return result rows.

        Args:
            sql: The SQL text to execute.

        Returns:
            Rows as a list of ``column -> value`` mappings.

        Raises:
            TransformError: If the query fails.
        """
        raise NotImplementedError
