"""Backend adapters (the ports-and-adapters layer).

Concrete cloud/local implementations of the QueryEngine interface (Athena vs DuckDB).
Code never imports these directly - it asks ``config.settings`` for the active backend
(Section B). The LLM adapter (Bedrock vs Ollama) lives in the standalone ``rag/`` project.
"""
