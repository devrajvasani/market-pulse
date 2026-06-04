"""Backend adapters (the ports-and-adapters layer).

Concrete cloud/local implementations of the QueryEngine and LLMClient
interfaces. Code never imports these directly - it asks ``config.settings``
for the active backend (Section B).
"""
