"""Critical tests for deterministic, idempotent chunking."""

import pytest

from rag.app.domain.models import Document
from rag.app.services.chunking import chunk_document, chunk_text


def _doc(text: str) -> Document:
    return Document(
        doc_id="doc1",
        source="example.test",
        title="t",
        url="https://example.test/a",
        published=None,
        fetched_at="2026-06-16T00:00:00+00:00",
        text=text,
    )


def test_chunk_text_windows_with_overlap():
    # "abcdefghij" (10), size 4, overlap 1 -> step 3 -> [0:4],[3:7],[6:10] (stops, no tail dup)
    assert chunk_text("abcdefghij", size=4, overlap=1) == ["abcd", "defg", "ghij"]


def test_chunk_text_normalizes_whitespace():
    assert chunk_text("  a\n b   c ", size=100, overlap=0) == ["a b c"]


def test_chunk_text_empty_returns_empty():
    assert chunk_text("   ", size=10, overlap=0) == []


def test_chunk_text_rejects_bad_overlap():
    with pytest.raises(ValueError):
        chunk_text("abc", size=4, overlap=4)


def test_chunk_document_is_deterministic_and_unique():
    doc = _doc("x" * 50)
    first = chunk_document(doc, size=20, overlap=5)
    second = chunk_document(doc, size=20, overlap=5)
    # identical ids across runs (idempotent re-indexing)
    assert [c.chunk_id for c in first] == [c.chunk_id for c in second]
    # ids are unique within the document
    assert len({c.chunk_id for c in first}) == len(first)
    # citation metadata is carried onto each chunk
    assert all(c.url == "https://example.test/a" and c.doc_id == "doc1" for c in first)
