"""MarketPulse RAG chatbot — a polished Streamlit chat UI over the FastAPI service.

Modern chat UX (streaming answers, source citations, live Gold metrics) modelled on leading
assistant UIs: a clean centred conversation, example prompts, and a connection-aware sidebar. It
talks to the API over HTTP (``RAG_API_URL``), so UI and service stay decoupled.

Run:  ``streamlit run rag/ui/streamlit_app.py``  (with the API up: ``uvicorn rag.app.main:app``)
"""

from __future__ import annotations

import json
import os
from collections.abc import Iterator

import httpx
import streamlit as st

API_URL = os.getenv("RAG_API_URL", "http://localhost:8000").rstrip("/")
_TIMEOUT = httpx.Timeout(120.0, connect=5.0)

_EXAMPLES = [
    "What moved Bitcoin this week and by how much?",
    "What's the latest Ethereum news?",
    "What are today's top movers?",
    "Summarise the latest crypto headlines.",
]

_CSS = """
<style>
.block-container { max-width: 820px; padding-top: 2.2rem; }
[data-testid="stChatMessage"] { border-radius: 14px; padding: 0.2rem 0.4rem; }
[data-testid="stMetric"] {
    background: #F4F5FB; border: 1px solid #ECECF6; border-radius: 12px; padding: 10px 14px;
}
div[data-testid="stMetricValue"] { font-size: 1.15rem; }
footer { visibility: hidden; }
</style>
"""


def _health_ok() -> bool:
    """Return True if the API health endpoint responds 200."""
    try:
        return httpx.get(f"{API_URL}/api/v1/health", timeout=3.0).status_code == 200
    except httpx.HTTPError:
        return False


def _stream_answer(question: str, top_k: int, holder: dict) -> Iterator[str]:
    """Yield answer text deltas from the SSE endpoint; stash sources/gold/status into ``holder``."""
    payload = {"question": question, "top_k": top_k}
    with httpx.stream(
        "POST", f"{API_URL}/api/v1/ask/stream", json=payload, timeout=_TIMEOUT
    ) as response:
        if response.status_code >= 400:
            # Read the error body NOW (the stream is still open) — reading it after the
            # `with` block exits raises httpx.ResponseNotRead.
            holder["error"] = _error_from_response(response)
            return
        for line in response.iter_lines():
            if not line or not line.startswith("data: "):
                continue
            try:
                event = json.loads(line[6:])
            except json.JSONDecodeError:
                continue  # ignore a malformed SSE line rather than crashing the render
            kind = event.get("type")
            if kind == "sources":
                holder["citations"] = event.get("citations", [])
                holder["gold"] = event.get("gold")
            elif kind == "delta":
                text = event.get("text")
                if text:
                    yield text
            elif kind == "error":
                holder["error"] = event.get("detail") or "The assistant failed to answer."
                return
            elif kind == "done":
                holder["done"] = True
                holder["used_gold"] = event.get("used_gold", False)
                return


def _render_gold(gold: dict | None) -> None:
    """Render live Gold metrics as metric cards (if any were used)."""
    if not gold or not gold.get("used"):
        return
    facts = gold.get("facts", [])
    if not facts:
        return
    st.caption("📊 Live market data (Gold)")
    columns = st.columns(min(len(facts), 4))
    for column, fact in zip(columns, facts[:4], strict=False):
        column.metric(fact["label"], fact["value"])


def _render_citations(citations: list[dict] | None) -> None:
    """Render the cited news sources in a collapsible panel."""
    if not citations:
        return
    with st.expander(f"📰 Sources ({len(citations)})"):
        for citation in citations:
            st.markdown(
                f"**[{citation['n']}] [{citation['title']}]({citation['url']})** — "
                f"_{citation['source']}_  \n{citation['snippet']}"
            )


def _render_message(message: dict) -> None:
    """Render one stored chat message (with its gold + citations for assistant turns)."""
    with st.chat_message(message["role"]):
        st.markdown(message["content"])
        if message["role"] == "assistant":
            _render_gold(message.get("gold"))
            _render_citations(message.get("citations"))


def _sidebar() -> int:
    """Render the sidebar (status, examples, controls); return the selected top_k."""
    with st.sidebar:
        st.markdown("### ⚡ MarketPulse")
        st.caption("Hybrid RAG over crypto news + live Gold metrics.")
        if _health_ok():
            st.success(f"API connected · {API_URL}")
        else:
            st.error(f"API unreachable at {API_URL}.\nStart it: `uvicorn rag.app.main:app`")

        st.markdown("#### Try an example")
        for i, example in enumerate(_EXAMPLES):
            if st.button(example, key=f"ex_{i}", use_container_width=True):
                st.session_state["_pending"] = example
                st.rerun()

        top_k = st.slider("Sources to retrieve", min_value=2, max_value=8, value=4)
        if st.button("🗑️ Clear chat", use_container_width=True):
            st.session_state["messages"] = []
            st.rerun()
        return top_k


def _answer(question: str, top_k: int) -> None:
    """Stream and render the assistant's answer, then persist it to history."""
    with st.chat_message("assistant"):
        holder: dict = {}
        try:
            text = st.write_stream(_stream_answer(question, top_k, holder))
        except httpx.HTTPError:
            # Connection-level failure (API down / network). HTTP error *statuses* are handled
            # in-stream via holder["error"] below.
            text = "⚠️ Could not reach the assistant API."
            st.error(
                f"Could not reach the API at {API_URL}. Is `uvicorn rag.app.main:app` running?"
            )
            holder = {}
        else:
            text = text or ""
            if holder.get("error"):
                st.error(holder["error"])
                text = f"{text}\n\n⚠️ {holder['error']}".strip()
                holder = {}  # don't render gold/citations for a failed answer
            elif not holder.get("done"):
                st.warning("The response was interrupted before completing.")
                text = f"{text}\n\n⚠️ (response interrupted)".strip()
            else:
                _render_gold(holder.get("gold"))
                _render_citations(holder.get("citations"))
    st.session_state["messages"].append(
        {
            "role": "assistant",
            "content": text,
            "gold": holder.get("gold"),
            "citations": holder.get("citations"),
        }
    )


def _error_from_response(response: httpx.Response) -> str:
    """Read + parse the API's structured error body (call while the stream is still open)."""
    try:
        data = json.loads(response.read())
        return data.get("detail") or data.get("error") or f"HTTP {response.status_code}"
    except (ValueError, json.JSONDecodeError, httpx.HTTPError):
        return f"HTTP {response.status_code} from the assistant API."


def main() -> None:
    """Render the chat application."""
    st.set_page_config(page_title="MarketPulse Assistant", page_icon="⚡", layout="centered")
    st.markdown(_CSS, unsafe_allow_html=True)
    st.title("⚡ MarketPulse Assistant")
    st.caption("Ask about crypto markets — answers cite news sources and live Gold metrics.")

    top_k = _sidebar()
    st.session_state.setdefault("messages", [])
    for message in st.session_state["messages"]:
        _render_message(message)

    question = st.chat_input("Ask about crypto markets…") or st.session_state.pop("_pending", None)
    if question:
        st.session_state["messages"].append({"role": "user", "content": question})
        with st.chat_message("user"):
            st.markdown(question)
        _answer(question, top_k)


if __name__ == "__main__":
    main()
