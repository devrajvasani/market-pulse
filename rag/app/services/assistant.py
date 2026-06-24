"""The hybrid assistant orchestrator: retrieve chunks + Gold facts, compose a cited answer.

Combines document retrieval (FAISS) with live Gold metrics and asks the LLM to compose an answer
that cites news sources by bracket number and uses the exact live numbers. Exposes both a blocking
:meth:`answer` and a streaming :meth:`answer_stream` (typed events) for the chat UI.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

from rag.app.core.config import RagConfig, get_config
from rag.app.core.errors import RAGError
from rag.app.core.logging import get_logger
from rag.app.llm.base import LLMHandler
from rag.app.llm.factory import get_llm_handler
from rag.app.services.gold import GoldResult, GoldService
from rag.app.services.retrieval import RetrievalService, RetrievedChunk

logger = get_logger(__name__)

_SNIPPET_CHARS = 240

_SYSTEM_PROMPT = (
    "You are MarketPulse's market-intelligence assistant. Answer the user's question concisely "
    "and accurately using ONLY the provided news context and live market metrics. Cite news "
    "sources inline by their bracket number, e.g. [1]. When live metrics are provided, use those "
    "exact numbers. If the context does not contain the answer, say so plainly. Never invent "
    "prices, percentages, or facts."
)


@dataclass(frozen=True)
class Citation:
    """A cited news source backing the answer."""

    n: int
    title: str
    url: str
    source: str
    score: float
    snippet: str


@dataclass(frozen=True)
class AssistantAnswer:
    """The assembled answer: text + the citations and live Gold facts that informed it."""

    answer: str
    citations: tuple[Citation, ...]
    gold: GoldResult
    used_gold: bool


@dataclass(frozen=True)
class PreparedAnswer:
    """The fallible pre-LLM work (retrieval + Gold + prompt), done eagerly before streaming.

    Separating this from generation lets the API run it *before* committing the 200 streaming
    response, so retrieval/index/gold errors map to clean HTTP errors instead of a broken stream.
    """

    citations: tuple[Citation, ...]
    gold: GoldResult
    prompt: str

    @property
    def used_gold(self) -> bool:
        """Whether live Gold facts were attached."""
        return self.gold.used


def _snippet(text: str) -> str:
    """First ~240 chars of a chunk, for display in the citation."""
    text = text.strip()
    return text if len(text) <= _SNIPPET_CHARS else text[:_SNIPPET_CHARS].rstrip() + "…"


def _citation_dict(citation: Citation) -> dict:
    """Serialize a citation for an SSE event / JSON response."""
    return {
        "n": citation.n,
        "title": citation.title,
        "url": citation.url,
        "source": citation.source,
        "score": citation.score,
        "snippet": citation.snippet,
    }


def _gold_dict(gold: GoldResult) -> dict:
    """Serialize the Gold result for an SSE event / JSON response."""
    return {
        "intent": gold.intent,
        "used": gold.used,
        "summary": gold.summary,
        "facts": [
            {"label": f.label, "value": f.value, "numeric": f.numeric, "unit": f.unit}
            for f in gold.facts
        ],
    }


def serialize_answer(answer: AssistantAnswer) -> dict:
    """Serialize a full answer (text + citations + gold) to a JSON-ready dict (Lambda/API)."""
    return {
        "answer": answer.answer,
        "used_gold": answer.used_gold,
        "citations": [_citation_dict(c) for c in answer.citations],
        "gold": _gold_dict(answer.gold),
    }


class AssistantService:
    """Orchestrates retrieval + Gold + generation into a single answer."""

    def __init__(
        self,
        config: RagConfig | None = None,
        *,
        retrieval: RetrievalService | None = None,
        gold: GoldService | None = None,
        llm: LLMHandler | None = None,
    ) -> None:
        """Initialise with config; sub-services + LLM handler are injectable for tests."""
        self._config = config or get_config()
        self._retrieval = retrieval or RetrievalService(self._config)
        self._gold = gold or GoldService(self._config)
        self._llm = llm or get_llm_handler()

    def prepare(self, question: str, top_k: int | None = None) -> PreparedAnswer:
        """Do the fallible pre-LLM work: retrieve chunks + Gold facts + build the prompt.

        Call this eagerly (outside a streaming response) so retrieval/index/Gold errors surface
        as normal exceptions the API can map to clean HTTP errors.

        Raises:
            IndexNotFoundError / RetrievalError: if retrieval fails. (Gold failures degrade.)
        """
        chunks = self._retrieval.retrieve(question, k=top_k)
        gold = self._gold.facts_for(question)
        citations = tuple(
            Citation(
                n=index + 1,
                title=hit.chunk.title,
                url=hit.chunk.url,
                source=hit.chunk.source,
                score=round(hit.score, 4),
                snippet=_snippet(hit.chunk.text),
            )
            for index, hit in enumerate(chunks)
        )
        prompt = self._build_prompt(question, chunks, gold)
        logger.info(
            "Prepared answer",
            extra={"citations": len(citations), "gold_intent": gold.intent, "used_gold": gold.used},
        )
        return PreparedAnswer(citations=citations, gold=gold, prompt=prompt)

    def answer(self, question: str, top_k: int | None = None) -> AssistantAnswer:
        """Return the full (non-streaming) answer with citations + Gold facts."""
        prepared = self.prepare(question, top_k)
        text = self._llm.generate(prepared.prompt, system=_SYSTEM_PROMPT)
        return AssistantAnswer(
            answer=text,
            citations=prepared.citations,
            gold=prepared.gold,
            used_gold=prepared.gold.used,
        )

    def stream(self, prepared: PreparedAnswer) -> Iterator[dict]:
        """Stream a prepared answer as typed events: ``sources`` → ``delta``* → ``done``/``error``.

        Mid-stream LLM failures are emitted as a terminal ``error`` event (the 200 + headers are
        already sent, so they can't become an HTTP error) — the client always gets a clean signal.
        """
        yield {
            "type": "sources",
            "citations": [_citation_dict(c) for c in prepared.citations],
            "gold": _gold_dict(prepared.gold),
        }
        try:
            for delta in self._llm.generate_stream(prepared.prompt, system=_SYSTEM_PROMPT):
                yield {"type": "delta", "text": delta}
        except RAGError as exc:
            logger.warning("Streaming generation failed", extra={"code": exc.code})
            yield {"type": "error", "code": exc.code, "detail": str(exc)}
            return
        except Exception:
            logger.exception("Unexpected streaming failure")
            yield {
                "type": "error",
                "code": "internal_error",
                "detail": "An unexpected error occurred while generating the answer.",
            }
            return
        yield {"type": "done", "used_gold": prepared.gold.used}

    @staticmethod
    def _build_prompt(question: str, chunks: list[RetrievedChunk], gold: GoldResult) -> str:
        """Assemble the user prompt from the question, live metrics, and numbered news context."""
        metrics = gold.summary or "(no live metrics relevant to this question)"
        if chunks:
            context = "\n".join(
                f"[{index + 1}] {hit.chunk.title} — {hit.chunk.text}"
                for index, hit in enumerate(chunks)
            )
        else:
            context = "(no relevant news found)"
        return (
            f"Question: {question}\n\n"
            f"Live market metrics:\n{metrics}\n\n"
            f"News context:\n{context}\n\n"
            "Answer:"
        )
