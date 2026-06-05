"""AWS Lambda entry point for batch ingestion (triggered by EventBridge)."""

from __future__ import annotations

from typing import Any

from src.common.logging_config import get_logger
from src.ingestion.batch.ingest import run_ingestion

logger = get_logger(__name__)


def handler(event: dict[str, Any], context: Any) -> dict[str, Any]:
    """Run one batch-ingestion cycle.

    Args:
        event: the EventBridge event (unused — the schedule is the trigger).
        context: the Lambda runtime context (unused).

    Returns:
        A status dict containing the ingestion summary.
    """
    logger.info("Batch ingestion invoked")
    summary = run_ingestion()
    return {"statusCode": 200, "summary": summary}
