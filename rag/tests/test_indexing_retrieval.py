"""Critical tests for FAISS index build + retrieval (fake embedder + moto S3)."""

import boto3
import pytest
from moto import mock_aws

from rag.app.core.config import get_config
from rag.app.core.errors import IndexNotFoundError, RAGError, RetrievalError
from rag.app.domain.models import Document
from rag.app.repositories.index_store import IndexStore
from rag.app.services.indexing import IndexingService
from rag.app.services.retrieval import RetrievalService
from rag.app.services.vectorstore import build_index, search

_BUCKET = "marketpulse-test-gold"


class _FakeEmbedder:
    """Deterministic bag-of-words embedder so retrieval is predictable (+bias dim, never zero)."""

    VOCAB = ("bitcoin", "ethereum", "price", "upgrade")
    embed_model_id = "fake:test"

    def embed(self, texts: list[str]) -> list[list[float]]:
        return [[float(t.lower().count(w)) for w in self.VOCAB] + [0.1] for t in texts]


class _FakeDocs:
    def __init__(self, docs: list[Document]):
        self._docs = docs

    def list_documents(self) -> list[Document]:
        return self._docs


def _doc(doc_id: str, title: str, text: str) -> Document:
    return Document(
        doc_id=doc_id,
        source="example.test",
        title=title,
        url=f"https://example.test/{doc_id}",
        published=None,
        fetched_at="2026-06-16T00:00:00+00:00",
        text=text,
    )


def _aws_env(monkeypatch):
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("GOLD_BUCKET", _BUCKET)
    monkeypatch.setenv("RAG_CHUNK_SIZE", "2000")  # short docs -> one chunk each
    monkeypatch.setenv("RAG_CHUNK_OVERLAP", "0")


def test_vectorstore_search_orders_by_similarity():
    index = build_index([[1, 0, 0, 0, 0.1], [0, 1, 0, 0, 0.1]])
    hits = search(index, [1, 0, 0, 0, 0.1], k=2)
    assert hits[0][0] == 0  # closest is the first vector
    assert hits[0][1] >= hits[1][1]  # scores are descending


def test_vectorstore_search_caps_k_at_index_size():
    index = build_index([[1, 0, 0, 0, 0.1]])
    assert len(search(index, [1, 0, 0, 0, 0.1], k=10)) == 1


@mock_aws
def test_build_then_retrieve_finds_relevant_chunk(monkeypatch):
    _aws_env(monkeypatch)
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=_BUCKET)
    docs = [
        _doc("btc", "Bitcoin rally", "Bitcoin price soared this week"),
        _doc("eth", "Ethereum upgrade", "Ethereum upgrade shipped"),
    ]
    cfg = get_config()
    embedder = _FakeEmbedder()
    result = IndexingService(
        cfg, doc_store=_FakeDocs(docs), index_store=IndexStore(cfg), llm=embedder
    ).build()
    assert result.documents == 2
    assert result.chunks == 2

    retriever = RetrievalService(cfg, index_store=IndexStore(cfg), llm=embedder)
    hits = retriever.retrieve("tell me about bitcoin", k=1)
    assert len(hits) == 1
    assert hits[0].chunk.doc_id == "btc"


@mock_aws
def test_retrieve_without_index_raises(monkeypatch):
    _aws_env(monkeypatch)
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=_BUCKET)
    cfg = get_config()
    with pytest.raises(IndexNotFoundError):
        RetrievalService(cfg, index_store=IndexStore(cfg), llm=_FakeEmbedder()).retrieve("hi")


@mock_aws
def test_build_with_no_documents_raises(monkeypatch):
    _aws_env(monkeypatch)
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=_BUCKET)
    cfg = get_config()
    with pytest.raises(RAGError):
        IndexingService(
            cfg, doc_store=_FakeDocs([]), index_store=IndexStore(cfg), llm=_FakeEmbedder()
        ).build()


class _EmptyEmbedder:
    def embed(self, _texts):
        return []  # contract violation: zero vectors for non-empty chunks


@mock_aws
def test_build_with_mismatched_embed_count_raises(monkeypatch):
    _aws_env(monkeypatch)
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=_BUCKET)
    cfg = get_config()
    docs = [_doc("btc", "B", "Bitcoin price soared")]
    with pytest.raises(RAGError):
        IndexingService(
            cfg, doc_store=_FakeDocs(docs), index_store=IndexStore(cfg), llm=_EmptyEmbedder()
        ).build()


@mock_aws
def test_retrieve_detects_index_meta_mismatch(monkeypatch):
    _aws_env(monkeypatch)
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=_BUCKET)
    cfg = get_config()
    store = IndexStore(cfg)
    # 2 vectors in the index, but metadata claims only 1 chunk (a torn write / stale pairing).
    store.save(
        build_index([[1, 0, 0, 0, 0.1], [0, 1, 0, 0, 0.1]]),
        {
            "count": 1,
            "chunks": [
                {
                    "chunk_id": "c",
                    "doc_id": "d",
                    "index": 0,
                    "source": "s",
                    "title": "t",
                    "url": "u",
                    "text": "x",
                }
            ],
        },
    )
    with pytest.raises(RetrievalError, match="inconsistent"):
        RetrievalService(cfg, index_store=IndexStore(cfg), llm=_FakeEmbedder()).retrieve("hi")


class _OtherModelEmbedder(_FakeEmbedder):
    embed_model_id = "fake:other"  # a different embed model -> incompatible vector space


@mock_aws
def test_retrieve_rejects_mismatched_embed_model(monkeypatch):
    _aws_env(monkeypatch)
    boto3.client("s3", region_name="us-east-1").create_bucket(Bucket=_BUCKET)
    cfg = get_config()
    docs = [_doc("btc", "B", "Bitcoin price soared")]
    IndexingService(
        cfg, doc_store=_FakeDocs(docs), index_store=IndexStore(cfg), llm=_FakeEmbedder()
    ).build()
    # The index was built with fake:test; retrieving with fake:other must refuse + ask to rebuild.
    with pytest.raises(RetrievalError, match="Rebuild"):
        RetrievalService(cfg, index_store=IndexStore(cfg), llm=_OtherModelEmbedder()).retrieve(
            "btc"
        )


@mock_aws
def test_retrieve_corrupt_meta_raises_retrieval_error(monkeypatch):
    _aws_env(monkeypatch)
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=_BUCKET)
    cfg = get_config()
    store = IndexStore(cfg)
    s3.put_object(Bucket=_BUCKET, Key=store.index_key, Body=build_index([[1, 0, 0, 0, 0.1]]))
    s3.put_object(Bucket=_BUCKET, Key=store.meta_key, Body=b"{ not valid json")
    with pytest.raises(RetrievalError, match="corrupt"):
        RetrievalService(cfg, index_store=IndexStore(cfg), llm=_FakeEmbedder()).retrieve("hi")
