"""Structured (JSON) logging setup for MarketPulse.

Every module logs critical events with useful detail (Section L.4) via these
helpers - never with ``print``. JSON output is friendly to CloudWatch Logs
Insights on AWS and is still readable one-line-per-event locally.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime

_CONFIGURED = False

# Attributes present on a bare LogRecord; anything else on a record is treated
# as a user-supplied structured field (passed via ``logger.info(..., extra=...)``).
_STANDARD_ATTRS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Render a :class:`logging.LogRecord` as a single line of JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize ``record`` to compact JSON.

        Args:
            record: The log record to format.

        Returns:
            A JSON string with timestamp, level, logger name, message, any
            exception text, and any ``extra`` structured fields.
        """
        payload: dict[str, object] = {
            "timestamp": datetime.fromtimestamp(record.created, tz=UTC).isoformat(),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        for key, value in record.__dict__.items():
            if key not in _STANDARD_ATTRS:
                payload[key] = value
        return json.dumps(payload, default=str)


def configure_logging(level: str | None = None) -> None:
    """Attach the JSON formatter to the root logger exactly once.

    Idempotent (engineering standard L.3): safe to call from any entry point;
    repeated calls are no-ops.

    Args:
        level: Log level name (e.g. ``"INFO"``). Defaults to the ``LOG_LEVEL``
            environment variable, falling back to ``"INFO"``.
    """
    global _CONFIGURED
    if _CONFIGURED:
        return
    handler = logging.StreamHandler()
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level or os.getenv("LOG_LEVEL", "INFO").upper())
    _CONFIGURED = True


def get_logger(name: str) -> logging.Logger:
    """Return a configured logger for a module.

    Args:
        name: Typically ``__name__`` of the calling module.

    Returns:
        A logger that emits JSON-formatted records.
    """
    configure_logging()
    return logging.getLogger(name)
