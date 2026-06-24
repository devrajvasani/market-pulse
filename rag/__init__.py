"""MarketPulse RAG assistant — a standalone, repo-extractable hybrid RAG service.

Answers plain-English questions using both ingested documents (FAISS retrieval) and live Gold
metrics (a templated query). Cloud-agnostic via the existing ``QueryEngine`` adapter and its own
LLM handlers (Ollama local / Bedrock cloud). Packaged as a self-contained ``rag/`` project so it
can later be lifted into its own repository.
"""
