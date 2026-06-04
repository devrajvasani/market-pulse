"""Custom exception types for MarketPulse.

A small, explicit hierarchy so failures carry context instead of surfacing as
bare stack traces (engineering standard L.5). Catch :class:`MarketPulseError`
to handle any project-raised error, or a specific subclass for one domain.
"""

from __future__ import annotations


class MarketPulseError(Exception):
    """Base class for every error raised by MarketPulse code."""


class ConfigError(MarketPulseError):
    """Raised when configuration is missing, invalid, or inconsistent."""


class IngestionError(MarketPulseError):
    """Raised when data ingestion (batch or streaming) fails."""


class TransformError(MarketPulseError):
    """Raised when a Bronze -> Silver -> Gold transform fails."""
