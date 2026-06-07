"""CLI: run SQL through the active QueryEngine (Athena on aws, DuckDB local).

Examples:
    uv run python -m src.query.run
    uv run python -m src.query.run --engine duckdb "SELECT count(*) FROM bronze_prices"
"""

from __future__ import annotations

import argparse

from config import settings
from src.backends.query_engine import QueryEngine
from src.common.logging_config import get_logger

logger = get_logger(__name__)

_DEFAULT_SQL = "SELECT count(*) AS row_count FROM bronze_prices"


def _select_engine(name: str | None) -> QueryEngine:
    """Return the requested engine, or the APP_ENV-driven default."""
    if name == "athena":
        from src.backends.athena_backend import AthenaBackend

        return AthenaBackend()
    if name == "duckdb":
        from src.backends.duckdb_backend import DuckDBBackend

        return DuckDBBackend()
    return settings.get_query_engine()


def main() -> None:
    """Run a SQL statement via the chosen QueryEngine and print the rows."""
    parser = argparse.ArgumentParser(description="Run SQL via the active QueryEngine.")
    parser.add_argument("sql", nargs="?", default=_DEFAULT_SQL, help="SQL to run.")
    parser.add_argument(
        "--engine",
        choices=["athena", "duckdb"],
        default=None,
        help="Force an engine; the default picks by APP_ENV (aws->Athena, local->DuckDB).",
    )
    args = parser.parse_args()

    engine = _select_engine(args.engine)
    rows = engine.run_sql(args.sql)
    logger.info("Query complete", extra={"engine": type(engine).__name__, "rows": len(rows)})
    for row in rows:
        print(row)


if __name__ == "__main__":
    main()
