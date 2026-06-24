"""FAISS vector-store helpers: build a cosine index and search it (pure-Python, no IO).

Vectors are L2-normalized and stored in an ``IndexFlatIP`` so inner product == cosine similarity.
Kept IO-free so it is trivial to unit-test and identical on AWS and local.
"""

from __future__ import annotations

from collections.abc import Sequence

import faiss
import numpy as np


def _matrix(vectors: Sequence[Sequence[float]]) -> np.ndarray:
    """Coerce a list of vectors into a 2-D float32 array."""
    arr = np.asarray(vectors, dtype="float32")
    if arr.ndim != 2 or arr.shape[0] == 0:
        raise ValueError(f"expected a non-empty 2-D array of vectors, got shape {arr.shape}")
    return arr


def build_index(vectors: Sequence[Sequence[float]]) -> bytes:
    """Build a normalized cosine FAISS index from ``vectors`` and return it serialized.

    Args:
        vectors: One embedding per chunk (all the same dimension).

    Returns:
        The serialized FAISS index as bytes (store this in S3).
    """
    arr = _matrix(vectors)
    faiss.normalize_L2(arr)
    index = faiss.IndexFlatIP(arr.shape[1])
    index.add(arr)
    return faiss.serialize_index(index).tobytes()


def index_ntotal(index_bytes: bytes) -> int:
    """Return the number of vectors stored in a serialized FAISS index."""
    index = faiss.deserialize_index(np.frombuffer(index_bytes, dtype="uint8").copy())
    return int(index.ntotal)


def search(index_bytes: bytes, query_vector: Sequence[float], k: int) -> list[tuple[int, float]]:
    """Search the serialized index for the ``k`` nearest chunks to ``query_vector``.

    Args:
        index_bytes: A serialized FAISS index (from :func:`build_index`).
        query_vector: The query embedding.
        k: Max neighbours to return.

    Returns:
        ``(row_index, score)`` pairs, best first; ``row_index`` aligns with the build order.
    """
    index = faiss.deserialize_index(np.frombuffer(index_bytes, dtype="uint8").copy())
    top = min(max(k, 0), index.ntotal)
    if top == 0:
        return []
    query = _matrix([query_vector])
    faiss.normalize_L2(query)
    scores, ids = index.search(query, top)
    return [
        (int(idx), float(score)) for idx, score in zip(ids[0], scores[0], strict=True) if idx != -1
    ]
