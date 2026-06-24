"""Internal domain models for the RAG pipeline (plain, serializable dataclasses).

``Document`` is one ingested news item; ``Chunk`` is one embeddable slice of a document. Both
carry stable, deterministic ids so re-ingesting / re-chunking the same source never duplicates.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


@dataclass(frozen=True)
class Document:
    """A cleaned, normalized news document landed in ``bronze/docs/``."""

    doc_id: str  # deterministic: sha256 of the article guid/link
    source: str  # feed host, e.g. "cointelegraph.com"
    title: str
    url: str
    published: str | None  # original pubDate string (may be None)
    fetched_at: str  # ISO-8601 UTC timestamp of ingestion
    text: str  # cleaned title + summary used for embedding

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Document:
        """Rebuild a Document from its stored dict (ignoring unknown keys)."""
        fields = {"doc_id", "source", "title", "url", "published", "fetched_at", "text"}
        return cls(**{k: data.get(k) for k in fields})


@dataclass(frozen=True)
class Chunk:
    """One embeddable slice of a document, with the citation metadata it carries."""

    chunk_id: str  # deterministic: sha256 of f"{doc_id}:{index}"
    doc_id: str
    index: int  # position within the document
    source: str
    title: str
    url: str
    text: str

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-serializable dict (used for the FAISS metadata sidecar)."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Chunk:
        """Rebuild a Chunk from its stored dict."""
        fields = {"chunk_id", "doc_id", "index", "source", "title", "url", "text"}
        return cls(**{k: data.get(k) for k in fields})
