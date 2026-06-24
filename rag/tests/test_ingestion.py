"""Critical tests for RSS ingestion: parsing/cleaning, idempotent storage, fetch errors."""

import urllib.error
import urllib.request
from pathlib import Path

import boto3
import pytest
from moto import mock_aws

import rag.app.services.ingestion as ing
from rag.app.core.config import get_config
from rag.app.core.errors import ConfigError, IngestionError
from rag.app.domain.models import Document
from rag.app.repositories.doc_store import DocStore
from rag.app.services.ingestion import IngestionService, strip_html

_ATOM = b"""<?xml version="1.0" encoding="UTF-8"?>
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <title>Atom article</title>
    <id>urn:uuid:1</id>
    <link rel="self" href="https://example.test/self"/>
    <link rel="alternate" href="https://example.test/article"/>
    <summary>Body text.</summary>
    <updated>2026-06-16T00:00:00Z</updated>
  </entry>
</feed>"""

_SAMPLE = (Path(__file__).parent / "fixtures" / "sample_rss.xml").read_bytes()
_BUCKET = "marketpulse-test-bronze"


def test_strip_html_removes_tags_and_unescapes():
    assert strip_html("<p>Bitcoin <b>up</b> &amp; strong</p>") == "Bitcoin up & strong"


def test_parse_feed_extracts_and_cleans():
    docs = IngestionService._parse_feed(_SAMPLE, "example.test")
    assert len(docs) == 2
    btc = docs[0]
    assert btc.title == "Bitcoin jumps 8% this week"
    assert btc.url == "https://example.test/articles/btc-jumps"
    assert btc.source == "example.test"
    # HTML stripped, entity unescaped, summary folded into text
    assert "<" not in btc.text
    assert "rallied" in btc.text and "& led the market" in btc.text
    # deterministic id from the guid
    assert len(btc.doc_id) == 64 and btc.doc_id != docs[1].doc_id


def test_parse_feed_rejects_bad_xml():
    with pytest.raises(IngestionError):
        IngestionService._parse_feed(b"not xml <<<", "example.test")


def test_feed_urls_rejects_non_http_scheme(monkeypatch):
    monkeypatch.setenv("RAG_FEED_URLS", "file:///etc/passwd")
    with pytest.raises(ConfigError):
        get_config()


class _FakeStore:
    """Stand-in DocStore for the fetch-error path (never reached)."""

    def save(self, _doc):  # pragma: no cover - not exercised
        return True


def test_fetch_error_raises_ingestion_error(monkeypatch):
    monkeypatch.setattr(ing.time, "sleep", lambda _s: None)

    def _boom(*_a, **_k):
        raise urllib.error.URLError("connection refused")

    monkeypatch.setattr(urllib.request, "urlopen", _boom)
    service = IngestionService(get_config(), doc_store=_FakeStore())
    with pytest.raises(IngestionError, match="Failed fetching feed"):
        service.ingest(["https://feed.test/rss"])


def test_atom_link_prefers_alternate():
    docs = IngestionService._parse_feed(_ATOM, "example.test")
    assert len(docs) == 1
    assert docs[0].url == "https://example.test/article"  # the rel="alternate" link, not self


@mock_aws
def test_doc_store_skips_malformed_json(monkeypatch):
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("BRONZE_BUCKET", _BUCKET)
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=_BUCKET)
    store = DocStore(get_config())
    store.save(
        Document("d1", "example.test", "Good", "https://example.test/g", None, "2026", "Good text")
    )
    s3.put_object(Bucket=_BUCKET, Key="docs/source=example.test/corrupt.json", Body=b"{ not json")

    docs = store.list_documents()  # the corrupt object is skipped, not fatal
    assert [d.doc_id for d in docs] == ["d1"]


@mock_aws
def test_ingest_is_idempotent(monkeypatch):
    monkeypatch.delenv("AWS_ENDPOINT_URL", raising=False)
    monkeypatch.setenv("AWS_DEFAULT_REGION", "us-east-1")
    monkeypatch.setenv("AWS_ACCESS_KEY_ID", "test")
    monkeypatch.setenv("AWS_SECRET_ACCESS_KEY", "test")
    monkeypatch.setenv("BRONZE_BUCKET", _BUCKET)
    s3 = boto3.client("s3", region_name="us-east-1")
    s3.create_bucket(Bucket=_BUCKET)

    service = IngestionService(get_config())
    monkeypatch.setattr(service, "_fetch", lambda _url: _SAMPLE)

    first = service.ingest(["https://example.test/rss"])
    second = service.ingest(["https://example.test/rss"])

    assert first.documents == 2 and first.new == 2
    assert second.documents == 2 and second.new == 0  # re-ingest adds nothing new
    listed = s3.list_objects_v2(Bucket=_BUCKET, Prefix="docs/")
    assert listed["KeyCount"] == 2  # deterministic keys -> no duplicates
    assert len(service._store.list_documents()) == 2
