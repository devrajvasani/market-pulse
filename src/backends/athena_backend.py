"""Amazon Athena implementation of the QueryEngine port (cloud)."""

from __future__ import annotations

from typing import Any

import awswrangler as wr
import boto3

from config import settings
from src.backends.query_engine import QueryEngine
from src.common.errors import TransformError


class AthenaBackend(QueryEngine):
    """Run SQL on Amazon Athena, inside the bytes-scanned-capped workgroup (Section B)."""

    def __init__(self, database: str | None = None, workgroup: str | None = None) -> None:
        """Initialise the backend.

        Args:
            database: Glue database to query (defaults to ``settings.athena_database()``).
            workgroup: Athena workgroup enforcing the scan cap + results location
                (defaults to ``settings.athena_workgroup()``).
        """
        self._database = database or settings.athena_database()
        self._workgroup = workgroup or settings.athena_workgroup()
        self._session = boto3.Session(region_name=settings.aws_region())

    def run_sql(self, sql: str) -> list[dict[str, Any]]:
        """Execute ``sql`` on Athena and return the result rows.

        Args:
            sql: The SQL text to execute.

        Returns:
            Rows as a list of ``column -> value`` mappings.

        Raises:
            TransformError: If the Athena query fails.
        """
        try:
            frame = wr.athena.read_sql_query(
                sql,
                database=self._database,
                workgroup=self._workgroup,
                ctas_approach=False,  # plain query (no CTAS temp table) — safe for ad-hoc reads
                boto3_session=self._session,
            )
        except Exception as exc:  # awswrangler/botocore raise many subtypes; wrap with context
            raise TransformError(f"Athena query failed [{type(exc).__name__}]: {exc}") from exc
        return frame.to_dict("records")
