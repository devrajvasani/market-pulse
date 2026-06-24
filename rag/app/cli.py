"""Command-line entry point for the RAG assistant: ``ingest``, ``build-index``, ``ask``.

Run with ``python -m rag.app.cli <command>`` against the active target (``APP_ENV``). The CLI is a
thin wrapper over the same services the API uses, so behaviour is identical across interfaces.
"""

from __future__ import annotations

import argparse
import sys

from rag.app.core.config import get_config
from rag.app.core.errors import RAGError
from rag.app.core.logging import get_logger
from rag.app.services.assistant import AssistantService
from rag.app.services.indexing import IndexingService
from rag.app.services.ingestion import IngestionService

logger = get_logger(__name__)


def _cmd_ingest(_args: argparse.Namespace) -> None:
    """Fetch the configured RSS feeds into ``bronze/docs/``."""
    result = IngestionService(get_config()).ingest()
    print(f"Ingested {result.documents} document(s) from {result.feeds} feed(s); {result.new} new.")


def _cmd_build_index(_args: argparse.Namespace) -> None:
    """Embed all chunks and (re)build the FAISS index in S3."""
    result = IndexingService(get_config()).build()
    print(
        f"Built index: {result.chunks} chunk(s) from {result.documents} document(s) "
        f"(dim={result.dim})."
    )


def _cmd_ask(args: argparse.Namespace) -> None:
    """Answer a question, printing the answer, live metrics, and sources."""
    answer = AssistantService().answer(args.question)
    print(answer.answer)
    if answer.gold.used:
        print("\nLive metrics:")
        for fact in answer.gold.facts:
            print(f"  - {fact.label}: {fact.value}")
    if answer.citations:
        print("\nSources:")
        for citation in answer.citations:
            print(f"  [{citation.n}] {citation.title} ({citation.url})")


def build_parser() -> argparse.ArgumentParser:
    """Build the argument parser with the three subcommands."""
    parser = argparse.ArgumentParser(prog="rag", description="MarketPulse RAG assistant")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("ingest", help="Fetch RSS feeds into bronze/docs/").set_defaults(
        func=_cmd_ingest
    )
    sub.add_parser("build-index", help="Embed chunks and build the FAISS index").set_defaults(
        func=_cmd_build_index
    )
    ask = sub.add_parser("ask", help="Ask the assistant a question")
    ask.add_argument("question", help="The question to ask, in quotes")
    ask.set_defaults(func=_cmd_ask)
    return parser


def _force_utf8_stdout() -> None:
    """Emit UTF-8 so model output with non-ASCII chars prints on a Windows cp1252 console."""
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")  # type: ignore[union-attr]
        except (AttributeError, ValueError):
            pass


def main(argv: list[str] | None = None) -> int:
    """Parse args and dispatch; map RAG errors to a clear message + non-zero exit."""
    _force_utf8_stdout()
    args = build_parser().parse_args(argv)
    try:
        args.func(args)
    except RAGError as exc:
        print(f"Error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
