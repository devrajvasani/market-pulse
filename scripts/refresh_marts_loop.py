#!/usr/bin/env python
"""Periodic Silver/Gold refresh for the continuous-streaming demo (cloud-agnostic).

Loops every ``REFRESH_SECONDS``:
  1. ``dbt build --select silver_trades gold_latest_price`` — rebuild the marts from the
     trades that have streamed into Bronze so far (runs the DQ tests too).
  2. ``dbt show --select gold_latest_price`` — print the current latest price per product.

Runs against whatever dbt target is configured (``DBT_TARGET``: ``duckdb`` locally,
``athena`` on AWS) — there is NO emulator/cloud-specific code here, only dbt. This is the
"continuous transformation" companion to the continuous producer; on AWS the same refresh
is driven by the Stage-6 EventBridge schedule instead of this loop.

Prereqs (local): the same env a normal local dbt run needs --
``DBT_TRADES_GLOB`` (the Bronze trades glob), ``AWS_ENDPOINT_URL`` + ``test`` creds so
DuckDB can read Bronze from LocalStack S3.

    $env:DBT_TRADES_GLOB = "s3://<bronze-bucket>/trades/**/*.json"
    $env:REFRESH_SECONDS = "10"
    uv run python scripts/refresh_marts_loop.py
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from subprocess import run  # noqa: S404 -- invoking our own dbt CLI, fixed args

_REPO = Path(__file__).resolve().parents[1]
_DBT_DIR = _REPO / "src" / "transform" / "dbt"
_SELECT = (os.getenv("REFRESH_SELECT") or "silver_trades gold_latest_price").split()
_INTERVAL = int((os.getenv("REFRESH_SECONDS") or "10").strip())


def _dbt(*args: str) -> int:
    """Run a dbt subcommand from the dbt project dir; return its exit code."""
    return run(  # noqa: S603 -- fixed dbt CLI, no shell, args not user-derived
        ["dbt", *args, "--profiles-dir", ".", "--project-dir", "."],
        cwd=_DBT_DIR,
    ).returncode


def main() -> None:
    """Loop: rebuild the streaming marts and print the latest price, until Ctrl+C."""
    target = os.getenv("DBT_TARGET", "duckdb")
    print(f"Refreshing Silver+Gold every ~{_INTERVAL}s on target '{target}' (Ctrl+C to stop).")
    cycle = 0
    try:
        while True:
            cycle += 1
            print(f"\n=== refresh #{cycle}: dbt build {' '.join(_SELECT)} ===", flush=True)
            if _dbt("build", "--select", *_SELECT) == 0:
                print("--- gold_latest_price (current) ---", flush=True)
                _dbt("show", "--select", "gold_latest_price", "--limit", "10")
            else:
                print("(dbt build failed this cycle -- retrying next interval)", flush=True)
            time.sleep(_INTERVAL)
    except KeyboardInterrupt:
        print("\nStopped.")


if __name__ == "__main__":
    main()
