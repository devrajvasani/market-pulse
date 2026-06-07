"""DuckDB implementation of the QueryEngine port (local, free).

The free stand-in for Athena: it reads the SAME Bronze Parquet directly with
``read_parquet`` (Hive partitioning on ``snapshot_date`` / ``snapshot_hour``) and exposes
it as a ``bronze_prices`` view, so the *identical* SQL runs on both engines (Section B).
"""

from __future__ import annotations

from typing import Any

import duckdb

from config import settings
from src.backends.query_engine import QueryEngine
from src.common.errors import TransformError


class DuckDBBackend(QueryEngine):
    """Run SQL on local DuckDB over the Bronze Parquet — the free Athena twin."""

    def __init__(self, bronze_location: str | None = None) -> None:
        """Initialise the backend.

        Args:
            bronze_location: Prefix of the Bronze prices Parquet — an ``s3://…/prices`` URI
                (real AWS or LocalStack) or a local directory (used by tests). Defaults to
                ``settings.bronze_data_location()`` (resolved lazily at query time).
        """
        self._bronze_location = bronze_location

    def _connect(self) -> duckdb.DuckDBPyConnection:
        """Open a connection and register ``bronze_prices`` over the Parquet."""
        con = duckdb.connect()
        location = (self._bronze_location or settings.bronze_data_location()).replace("\\", "/")
        if location.startswith("s3://"):
            self._configure_s3(con)
        glob = f"{location.rstrip('/')}/**/*.parquet"
        # hive_types_autocast = 0 keeps snapshot_date / snapshot_hour as strings, matching the
        # Glue table's string partition types (so the same SQL behaves identically on Athena).
        con.execute(
            "CREATE VIEW bronze_prices AS "
            f"SELECT * FROM read_parquet('{glob}', hive_partitioning = true, "
            "hive_types_autocast = 0)"
        )
        return con

    @staticmethod
    def _configure_s3(con: duckdb.DuckDBPyConnection) -> None:
        """Point DuckDB's httpfs at the active S3 target (LocalStack or real AWS).

        Credentials always come from the standard AWS chain (env vars / profile) — never
        embedded — so nothing secret is written into SQL.
        """
        con.execute("INSTALL httpfs; LOAD httpfs")
        con.execute("INSTALL aws; LOAD aws")
        region = settings.aws_region()
        endpoint = settings.aws_endpoint_url()
        if endpoint:  # LocalStack: path-style, no TLS; creds resolve from env (test/test)
            host = endpoint.split("://", 1)[-1]
            con.execute(
                "CREATE SECRET (TYPE s3, PROVIDER credential_chain, "
                f"REGION '{region}', ENDPOINT '{host}', URL_STYLE 'path', USE_SSL false)"
            )
        else:  # real AWS: resolve creds via the standard chain (env / profile)
            con.execute(f"CREATE SECRET (TYPE s3, PROVIDER credential_chain, REGION '{region}')")

    def run_sql(self, sql: str) -> list[dict[str, Any]]:
        """Execute ``sql`` on DuckDB and return the result rows.

        Args:
            sql: The SQL text to execute (``bronze_prices`` is available as a view).

        Returns:
            Rows as a list of ``column -> value`` mappings.

        Raises:
            TransformError: If the DuckDB query fails.
        """
        try:
            con = self._connect()
            try:
                cursor = con.execute(sql)
                columns = [col[0] for col in cursor.description]
                rows = [dict(zip(columns, record, strict=True)) for record in cursor.fetchall()]
            finally:
                con.close()
        except Exception as exc:  # duckdb raises many subtypes; wrap with context
            raise TransformError(f"DuckDB query failed [{type(exc).__name__}]: {exc}") from exc
        return rows
