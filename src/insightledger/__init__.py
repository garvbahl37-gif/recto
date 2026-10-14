"""Recto — a reader for SEC filings that traces every figure to its page.

Multi-agent visual-RAG over financial/compliance documents. Retrieval happens
over rendered page *images* (ColQwen late-interaction) rather than lossy text
extraction, so tables, charts, stamps and layout survive. Every answer carries
region-level citations, and every change is gated by an eval harness.

The package is designed to run end-to-end with zero GPU and zero API keys via
deterministic *stub* backends, and to transparently upgrade to real backends
(ColQwen, Qdrant, Claude, LangGraph) when their dependencies and credentials
are present. Backend selection is controlled by `insightledger.config`.
"""

__version__ = "0.1.0"
