"""Structured (JSON) logging for the RAG assistant (self-contained).

One line of JSON per event — friendly to CloudWatch Logs Insights on AWS and still readable
locally. Mirrors the core project's logging style so the ``rag/`` package stays extractable
without depending on ``src/common``. Never use ``print``; log critical events with context.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime

_CONFIGURED = False

# Attributes present on a bare LogRecord; anything else is a user-supplied structured field
# (passed via ``logger.info(..., extra={...})``).
_STANDARD_ATTRS = set(logging.makeLogRecord({}).__dict__) | {"message", "asctime"}


class JsonFormatter(logging.Formatter):
    """Render a :class:`logging.LogRecord` as a single line of JSON."""

    def format(self, record: logging.LogRecord) -> str:
        """Serialize ``record`` (timestamp, level, logger, message, exc, extras) to JSON."""
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
    """Attach the JSON formatter to the root logger exactly once (idempotent).

    Args:
        level: Log level name (e.g. ``"INFO"``). Defaults to ``LOG_LEVEL`` env, else ``"INFO"``.
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
    """Return a configured JSON logger for a module (typically ``__name__``)."""
    configure_logging()
    return logging.getLogger(name)
