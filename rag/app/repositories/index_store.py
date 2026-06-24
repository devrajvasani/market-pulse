"""S3 repository for the FAISS index + its metadata sidecar (``gold/rag/<env>/``).

The index is a derived serving artifact, so it lives in the Gold bucket under an env-namespaced
prefix (the dimension differs per embedding model, so each target gets its own index). Boto3 only,
``AWS_ENDPOINT_URL``-aware — LocalStack locally, real S3 on AWS.
"""

from __future__ import annotations

import json
from typing import Any

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from rag.app.core.config import RagConfig
from rag.app.core.errors import IndexNotFoundError, RetrievalError
from rag.app.core.logging import get_logger

logger = get_logger(__name__)

_MISSING_CODES = ("404", "NoSuchKey", "NotFound")


class IndexStore:
    """Read/write the serialized FAISS index and its JSON metadata sidecar."""

    def __init__(self, config: RagConfig, client: object | None = None) -> None:
        """Initialise with the Gold bucket + index prefix + an S3 client (injectable)."""
        self._bucket = config.require_gold_bucket()
        self._prefix = config.index_prefix.rstrip("/")
        self._s3 = client or boto3.client(
            "s3", endpoint_url=config.aws_endpoint_url, region_name=config.aws_region
        )

    @property
    def index_key(self) -> str:
        """S3 key of the serialized FAISS index."""
        return f"{self._prefix}/index.faiss"

    @property
    def meta_key(self) -> str:
        """S3 key of the JSON metadata sidecar (chunks + manifest)."""
        return f"{self._prefix}/meta.json"

    @property
    def location(self) -> str:
        """Human-readable ``s3://…`` location of the index prefix (for logs/health)."""
        return f"s3://{self._bucket}/{self._prefix}/"

    def save(self, index_bytes: bytes, meta: dict[str, Any]) -> None:
        """Upload the index + metadata (overwrite — rebuilds are idempotent).

        Raises:
            RetrievalError: if either upload fails.
        """
        try:
            self._s3.put_object(Bucket=self._bucket, Key=self.index_key, Body=index_bytes)
            self._s3.put_object(
                Bucket=self._bucket,
                Key=self.meta_key,
                Body=json.dumps(meta).encode("utf-8"),
                ContentType="application/json",
            )
        except (ClientError, BotoCoreError) as exc:
            raise RetrievalError(f"Failed saving index to {self.location}: {exc}") from exc
        logger.info(
            "Saved RAG index", extra={"location": self.location, "chunks": meta.get("count")}
        )

    def load(self) -> tuple[bytes, dict[str, Any]]:
        """Download the index + metadata.

        Raises:
            IndexNotFoundError: if the index has not been built yet.
            RetrievalError: on any other S3 failure.
        """
        try:
            index_bytes = self._s3.get_object(Bucket=self._bucket, Key=self.index_key)[
                "Body"
            ].read()
            meta_raw = self._s3.get_object(Bucket=self._bucket, Key=self.meta_key)["Body"].read()
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _MISSING_CODES:
                raise IndexNotFoundError(
                    f"No RAG index at {self.location}. Build it first: `rag build-index`."
                ) from exc
            raise RetrievalError(f"Failed loading index from {self.location}: {exc}") from exc
        except BotoCoreError as exc:
            raise RetrievalError(f"Failed loading index from {self.location}: {exc}") from exc
        try:
            meta = json.loads(meta_raw)
        except (json.JSONDecodeError, ValueError) as exc:
            raise RetrievalError(
                f"RAG index metadata at {self.location} is corrupt (rebuild it: `rag build-index`)."
            ) from exc
        return index_bytes, meta

    def exists(self) -> bool:
        """Return True if the index metadata is present (readiness probe)."""
        try:
            self._s3.head_object(Bucket=self._bucket, Key=self.meta_key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _MISSING_CODES:
                return False
            raise RetrievalError(f"Failed probing index at {self.location}: {exc}") from exc
