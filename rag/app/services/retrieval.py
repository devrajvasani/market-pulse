"""Retrieve the top-k document chunks for a query from the FAISS index."""

from __future__ import annotations

from dataclasses import dataclass

from rag.app.core.config import RagConfig
from rag.app.core.errors import RetrievalError
from rag.app.core.logging import get_logger
from rag.app.domain.models import Chunk
from rag.app.llm.base import LLMHandler
from rag.app.llm.factory import get_llm_handler
from rag.app.repositories.index_store import IndexStore
from rag.app.services.vectorstore import index_ntotal, search

logger = get_logger(__name__)


@dataclass(frozen=True)
class RetrievedChunk:
    """A chunk returned from retrieval together with its similarity score."""

    chunk: Chunk
    score: float


class RetrievalService:
    """Embed a query and return the most similar chunks, caching the loaded index."""

    def __init__(
        self,
        config: RagConfig,
        *,
        index_store: IndexStore | None = None,
        llm: LLMHandler | None = None,
    ) -> None:
        """Initialise with config; index store + LLM handler are injectable for tests."""
        self._config = config
        self._store = index_store or IndexStore(config)
        self._llm = llm or get_llm_handler()
        self._cache: tuple[bytes, dict] | None = None

    def refresh(self) -> None:
        """Drop the cached index so the next retrieve reloads it (after a rebuild)."""
        self._cache = None

    def _index(self) -> tuple[bytes, dict]:
        """Return the cached (index_bytes, meta), loading + validating from S3 on first use.

        Validates that the FAISS vector count matches the metadata chunk count — a torn write
        (index + meta are separate S3 objects) would otherwise silently drop or mislabel
        citations. Fail loud instead.
        """
        if self._cache is None:
            index_bytes, meta = self._store.load()  # raises IndexNotFoundError if not built
            chunk_count = len(meta.get("chunks", []))
            ntotal = index_ntotal(index_bytes)
            if ntotal != chunk_count:
                raise RetrievalError(
                    f"RAG index is inconsistent: {ntotal} vectors vs {chunk_count} metadata "
                    "chunks (rebuild it: `rag build-index`)."
                )
            self._cache = (index_bytes, meta)
        return self._cache

    def retrieve(self, query: str, k: int | None = None) -> list[RetrievedChunk]:
        """Return the top-``k`` chunks most similar to ``query``.

        Args:
            query: The natural-language query.
            k: Number of chunks (defaults to ``config.top_k``).

        Returns:
            Best-first list of :class:`RetrievedChunk`.

        Raises:
            RetrievalError: if the query is empty or embedding returns nothing.
            IndexNotFoundError: if the index has not been built.
        """
        cleaned = (query or "").strip()
        if not cleaned:
            raise RetrievalError("Query is empty.")
        index_bytes, meta = self._index()
        active_model = self._llm.embed_model_id
        index_model = meta.get("model", "")
        if active_model and index_model and active_model != index_model:
            raise RetrievalError(
                f"Index was built with embed model {index_model!r}, but the active embed model is "
                f"{active_model!r}. Rebuild it: `rag build-index`."
            )
        vectors = self._llm.embed([cleaned])
        if not vectors:
            raise RetrievalError("Embedding the query returned no vector.")
        try:
            hits = search(index_bytes, vectors[0], k or self._config.top_k)
        except Exception as exc:  # e.g. FAISS dimension mismatch -> clear, actionable error
            raise RetrievalError(
                f"Query embedding is incompatible with the index ({type(exc).__name__}); "
                "rebuild it with `rag build-index`."
            ) from exc
        chunk_dicts = meta.get("chunks", [])
        results = [
            RetrievedChunk(Chunk.from_dict(chunk_dicts[idx]), score)
            for idx, score in hits
            if 0 <= idx < len(chunk_dicts)
        ]
        logger.info("Retrieved chunks", extra={"query_len": len(cleaned), "hits": len(results)})
        return results
