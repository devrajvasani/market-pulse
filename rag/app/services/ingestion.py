"""Idempotent document ingestion: fetch crypto-news RSS, clean it, land it in ``bronze/docs/``.

``urllib`` + ``defusedxml`` plus a tiny retry/backoff — the same dependency-light idiom as the
batch ingestion client. RSS 2.0 and Atom are both handled. Each item becomes a :class:`Document`
with a deterministic id (hash of its guid/link), so re-ingesting is idempotent. ``defusedxml``
guards against entity-expansion / external-entity attacks in untrusted feed XML.
"""

from __future__ import annotations

import hashlib
import html
import re
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import UTC, datetime
from urllib.parse import urlparse
from xml.etree.ElementTree import Element, ParseError

import defusedxml.ElementTree as defused_et
from defusedxml.common import DefusedXmlException

from rag.app.core.config import RagConfig
from rag.app.core.errors import IngestionError
from rag.app.core.logging import get_logger
from rag.app.domain.models import Document
from rag.app.repositories.doc_store import DocStore

logger = get_logger(__name__)

_MAX_ATTEMPTS = 3
_BACKOFF_BASE_SECONDS = 1
_ATOM = "{http://www.w3.org/2005/Atom}"
_SCRIPT_STYLE_RE = re.compile(r"(?is)<(script|style)\b.*?>.*?</\1>")
_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")
_USER_AGENT = "MarketPulse-RAG/1.0 (+https://github.com/devrajvasani/market-pulse)"


@dataclass(frozen=True)
class IngestionResult:
    """Summary of one ingestion run."""

    feeds: int
    documents: int
    new: int


def strip_html(raw: str) -> str:
    """Remove tags/scripts, unescape entities, and collapse whitespace from feed HTML."""
    if not raw:
        return ""
    text = _SCRIPT_STYLE_RE.sub(" ", raw)
    text = _TAG_RE.sub(" ", text)
    text = html.unescape(text)
    return _WS_RE.sub(" ", text).strip()


class IngestionService:
    """Fetch configured RSS feeds and persist cleaned documents (idempotently)."""

    def __init__(self, config: RagConfig, doc_store: DocStore | None = None) -> None:
        """Initialise with config and an optional injected :class:`DocStore`."""
        self._config = config
        self._store = doc_store or DocStore(config)

    def ingest(self, feed_urls: list[str] | None = None) -> IngestionResult:
        """Fetch each feed, parse items, and save new/updated documents.

        Args:
            feed_urls: Feeds to ingest (defaults to ``config.feed_urls``).

        Returns:
            An :class:`IngestionResult` with counts.

        Raises:
            IngestionError: if a feed cannot be fetched or parsed, or a write fails.
        """
        urls = list(feed_urls) if feed_urls else list(self._config.feed_urls)
        documents: list[Document] = []
        for url in urls:
            xml = self._fetch(url)
            documents.extend(self._parse_feed(xml, source=urlparse(url).netloc or url))
        new = sum(1 for doc in documents if self._store.save(doc))
        logger.info(
            "Ingested documents",
            extra={"feeds": len(urls), "documents": len(documents), "new": new},
        )
        return IngestionResult(feeds=len(urls), documents=len(documents), new=new)

    def _fetch(self, url: str) -> bytes:
        """GET a feed with retry/backoff on transient (429/5xx/network) errors."""
        last_error: Exception | None = None
        for attempt in range(1, _MAX_ATTEMPTS + 1):
            request = urllib.request.Request(
                url,
                headers={
                    "user-agent": _USER_AGENT,
                    "accept": "application/rss+xml, application/xml;q=0.9, */*;q=0.8",
                },
            )
            try:
                with urllib.request.urlopen(  # noqa: S310 - fixed http(s) feed URL from config
                    request, timeout=self._config.http_timeout_s
                ) as resp:
                    return resp.read()
            except urllib.error.HTTPError as exc:
                if exc.code != 429 and exc.code < 500:
                    raise IngestionError(f"Feed {url} returned HTTP {exc.code}") from exc
                last_error = exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_error = exc
            if attempt < _MAX_ATTEMPTS:
                time.sleep(_BACKOFF_BASE_SECONDS * 2 ** (attempt - 1))
        raise IngestionError(
            f"Failed fetching feed {url} after {_MAX_ATTEMPTS} attempts: {last_error}"
        )

    @staticmethod
    def _parse_feed(xml_bytes: bytes, source: str) -> list[Document]:
        """Parse RSS 2.0 or Atom bytes into documents (skipping title-less items)."""
        try:
            root = defused_et.fromstring(xml_bytes)
        except (ParseError, DefusedXmlException) as exc:
            raise IngestionError(f"Feed {source} is not valid/safe XML: {exc}") from exc

        fetched_at = datetime.now(UTC).isoformat()
        rss_items = root.findall(".//item")
        if rss_items:
            return [
                IngestionService._rss_item(item, source, fetched_at)
                for item in rss_items
                if item.findtext("title")
            ]
        return [
            IngestionService._atom_entry(entry, source, fetched_at)
            for entry in root.findall(f".//{_ATOM}entry")
            if entry.findtext(f"{_ATOM}title")
        ]

    @staticmethod
    def _build(
        *,
        guid: str | None,
        title: str | None,
        url: str | None,
        published: str | None,
        summary: str | None,
        source: str,
        fetched_at: str,
    ) -> Document:
        """Assemble a Document with a deterministic id and cleaned text."""
        key = (guid or url or title or "").strip()
        doc_id = hashlib.sha256(key.encode("utf-8")).hexdigest()
        clean_title = strip_html(title or "")
        text = strip_html(f"{clean_title}. {summary or ''}")
        return Document(
            doc_id=doc_id,
            source=source,
            title=clean_title,
            url=(url or "").strip(),
            published=published,
            fetched_at=fetched_at,
            text=text,
        )

    @classmethod
    def _rss_item(cls, item: Element, source: str, fetched_at: str) -> Document:
        """Map one RSS 2.0 ``<item>`` to a Document."""
        return cls._build(
            guid=item.findtext("guid"),
            title=item.findtext("title"),
            url=item.findtext("link"),
            published=item.findtext("pubDate"),
            summary=item.findtext("description"),
            source=source,
            fetched_at=fetched_at,
        )

    @staticmethod
    def _atom_link(entry: Element) -> str | None:
        """Pick the article URL from an Atom entry: prefer rel="alternate" (or no rel)."""
        links = entry.findall(f"{_ATOM}link")
        for link in links:
            if link.get("rel", "alternate") == "alternate":
                return link.get("href")
        return links[0].get("href") if links else None

    @classmethod
    def _atom_entry(cls, entry: Element, source: str, fetched_at: str) -> Document:
        """Map one Atom ``<entry>`` to a Document."""
        url = cls._atom_link(entry)
        summary = entry.findtext(f"{_ATOM}summary") or entry.findtext(f"{_ATOM}content")
        return cls._build(
            guid=entry.findtext(f"{_ATOM}id"),
            title=entry.findtext(f"{_ATOM}title"),
            url=url,
            published=entry.findtext(f"{_ATOM}updated"),
            summary=summary,
            source=source,
            fetched_at=fetched_at,
        )
