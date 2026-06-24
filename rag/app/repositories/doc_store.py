"""S3 repository for ingested documents (``bronze/docs/``).

Boto3 only, ``AWS_ENDPOINT_URL``-aware, so it targets LocalStack locally and real S3 on AWS with
no code change. Keys are **deterministic** (``docs/source=<source>/<doc_id>.json``) so re-ingesting
the same article overwrites the same object — idempotent, never duplicated.
"""

from __future__ import annotations

import json

import boto3
from botocore.exceptions import BotoCoreError, ClientError

from rag.app.core.config import RagConfig
from rag.app.core.errors import IngestionError
from rag.app.core.logging import get_logger
from rag.app.domain.models import Document

logger = get_logger(__name__)

_PREFIX = "docs"


class DocStore:
    """Read/write cleaned documents under the Bronze ``docs/`` prefix."""

    def __init__(self, config: RagConfig, client: object | None = None) -> None:
        """Initialise with the Bronze bucket + an S3 client (injectable for tests)."""
        self._bucket = config.require_bronze_bucket()
        self._s3 = client or boto3.client(
            "s3", endpoint_url=config.aws_endpoint_url, region_name=config.aws_region
        )

    @staticmethod
    def key_for(doc: Document) -> str:
        """Return the deterministic S3 key for a document."""
        return f"{_PREFIX}/source={doc.source}/{doc.doc_id}.json"

    def _exists(self, key: str) -> bool:
        """Return True if an object already exists at ``key``."""
        try:
            self._s3.head_object(Bucket=self._bucket, Key=key)
            return True
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in ("404", "NoSuchKey", "NotFound"):
                return False
            raise IngestionError(f"S3 head_object failed for {key}: {exc}") from exc

    def save(self, doc: Document) -> bool:
        """Write ``doc`` to S3 (overwrite — idempotent by key). Return True if it was new.

        Raises:
            IngestionError: if the S3 write fails.
        """
        key = self.key_for(doc)
        is_new = not self._exists(key)
        body = json.dumps(doc.to_dict()).encode("utf-8")
        try:
            self._s3.put_object(
                Bucket=self._bucket, Key=key, Body=body, ContentType="application/json"
            )
        except (ClientError, BotoCoreError) as exc:
            raise IngestionError(
                f"Failed writing document to s3://{self._bucket}/{key}: {exc}"
            ) from exc
        return is_new

    def list_documents(self) -> list[Document]:
        """Load every stored document (used by the indexer).

        Raises:
            IngestionError: if listing or reading fails.
        """
        documents: list[Document] = []
        try:
            paginator = self._s3.get_paginator("list_objects_v2")
            for page in paginator.paginate(Bucket=self._bucket, Prefix=f"{_PREFIX}/"):
                for obj in page.get("Contents", []):
                    raw = self._s3.get_object(Bucket=self._bucket, Key=obj["Key"])["Body"].read()
                    try:
                        documents.append(Document.from_dict(json.loads(raw)))
                    except (json.JSONDecodeError, TypeError, ValueError) as exc:
                        # A single corrupt object shouldn't fail the whole index build — skip + log.
                        logger.warning(
                            "Skipping malformed document",
                            extra={"key": obj["Key"], "error": str(exc)},
                        )
        except (ClientError, BotoCoreError) as exc:
            raise IngestionError(f"Failed listing documents in s3://{self._bucket}: {exc}") from exc
        return documents
