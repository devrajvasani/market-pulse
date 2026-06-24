"""Deterministic, idempotent text chunking for embedding.

Chunk ids are a hash of ``(doc_id, index)`` so re-chunking the same document yields the exact
same ids — re-indexing never produces duplicate vectors. Char-based sliding window with overlap
keeps it dependency-free and identical on every machine.
"""

from __future__ import annotations

import hashlib

from rag.app.domain.models import Chunk, Document


def chunk_text(text: str, *, size: int, overlap: int) -> list[str]:
    """Split ``text`` into overlapping windows of ``size`` chars stepping by ``size - overlap``.

    Args:
        text: Text to split (whitespace is normalized first).
        size: Max characters per chunk (must be > 0).
        overlap: Characters shared between consecutive chunks (must be in ``[0, size)``).

    Returns:
        The list of chunk strings (empty if ``text`` is blank).

    Raises:
        ValueError: if ``size``/``overlap`` are out of range.
    """
    normalized = " ".join(text.split())
    if not normalized:
        return []
    if size <= 0:
        raise ValueError(f"chunk size must be positive, got {size}")
    if not 0 <= overlap < size:
        raise ValueError(f"chunk overlap must be in [0, size); got overlap={overlap}, size={size}")

    step = size - overlap
    chunks: list[str] = []
    start = 0
    length = len(normalized)
    while start < length:
        chunks.append(normalized[start : start + size])
        if start + size >= length:
            break
        start += step
    return chunks


def _chunk_id(doc_id: str, index: int) -> str:
    """Deterministic chunk id from the document id + position."""
    return hashlib.sha256(f"{doc_id}:{index}".encode()).hexdigest()


def chunk_document(doc: Document, *, size: int, overlap: int) -> list[Chunk]:
    """Chunk a :class:`Document`, carrying citation metadata onto each :class:`Chunk`."""
    pieces = chunk_text(doc.text, size=size, overlap=overlap)
    return [
        Chunk(
            chunk_id=_chunk_id(doc.doc_id, index),
            doc_id=doc.doc_id,
            index=index,
            source=doc.source,
            title=doc.title,
            url=doc.url,
            text=piece,
        )
        for index, piece in enumerate(pieces)
    ]
