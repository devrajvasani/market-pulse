"""Build the FAISS index from stored documents and persist it (index + metadata) to S3."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

from rag.app.core.config import RagConfig
from rag.app.core.errors import RAGError
from rag.app.core.logging import get_logger
from rag.app.domain.models import Chunk
from rag.app.llm.base import LLMHandler
from rag.app.llm.factory import get_llm_handler
from rag.app.repositories.doc_store import DocStore
from rag.app.repositories.index_store import IndexStore
from rag.app.services.chunking import chunk_document
from rag.app.services.vectorstore import build_index

logger = get_logger(__name__)


@dataclass(frozen=True)
class IndexResult:
    """Summary of an index build."""

    documents: int
    chunks: int
    dim: int


class IndexingService:
    """Read documents, chunk + embed them, and store a FAISS index + metadata sidecar."""

    def __init__(
        self,
        config: RagConfig,
        *,
        doc_store: DocStore | None = None,
        index_store: IndexStore | None = None,
        llm: LLMHandler | None = None,
    ) -> None:
        """Initialise with config; repositories + LLM handler are injectable for tests."""
        self._config = config
        self._docs = doc_store or DocStore(config)
        self._index = index_store or IndexStore(config)
        self._llm = llm or get_llm_handler()

    def build(self) -> IndexResult:
        """Build and upload the index from every stored document.

        Raises:
            RAGError: if there are no documents to index (run ``ingest`` first).
            LLMError / RetrievalError: on embedding or upload failure.
        """
        documents = self._docs.list_documents()
        chunks: list[Chunk] = []
        for doc in documents:
            chunks.extend(
                chunk_document(
                    doc, size=self._config.chunk_size, overlap=self._config.chunk_overlap
                )
            )
        if not chunks:
            raise RAGError("No documents to index. Run `rag ingest` first.")

        vectors = self._llm.embed([chunk.text for chunk in chunks])
        if len(vectors) != len(chunks):
            raise RAGError(
                f"Embedding returned {len(vectors)} vectors for {len(chunks)} chunks "
                "(provider contract violation)."
            )
        index_bytes = build_index(vectors)
        meta = {
            "model": self._llm.embed_model_id,
            "dim": len(vectors[0]),
            "count": len(chunks),
            "built_at": datetime.now(UTC).isoformat(),
            "chunks": [chunk.to_dict() for chunk in chunks],
        }
        self._index.save(index_bytes, meta)
        logger.info(
            "Built RAG index",
            extra={
                "documents": len(documents),
                "chunks": len(chunks),
                "dim": len(vectors[0]),
                "embed_model": self._llm.embed_model_id,
            },
        )
        return IndexResult(documents=len(documents), chunks=len(chunks), dim=len(vectors[0]))
