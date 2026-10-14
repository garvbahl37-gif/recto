# Roadmap — toward an institutional AI research platform

Recto today is a **multi-agent visual-RAG system over live SEC filings**:
full-universe company/filing discovery, LangGraph orchestration (router →
retriever → extractor → verifier ⟲ → synthesizer), region-grounded citations,
abstention, an eval harness + CI gate, and a premium React dashboard — running
**fully local, no API key** (optional Claude).

This roadmap evolves it toward a Bloomberg-Terminal-class research platform
**incrementally and honestly** — each phase is self-contained, backwards
compatible, and testable. Items are marked ✅ done · 🔨 next · 🧱 needs infra.

## Phase 1 — Deeper agency (local, no new services)
- 🔨 **Planner agent** — decompose "compare NVDA vs AMD" into sub-tasks
  (revenue, margin, cash flow, segments, risks) that fan out in parallel.
- 🔨 **Financial Reasoning agent** — deterministic compute of YoY, QoQ, CAGR,
  gross/operating margin, FCF, ROE/ROA, debt ratios **from extracted numbers**
  (never LLM-arithmetic). Pure Python + a small numeric-extraction pass.
- 🔨 **Critic agent** — post-synthesis pass that checks unsupported claims,
  contradictions, and bad math, and can trigger re-retrieval (extends the
  existing verifier loop).
- 🔨 **Report generator** — Markdown/HTML investment-memo & risk-report export
  from verified claims (PDF/DOCX via a converter).
- 🔨 **Multi-company comparison** endpoint + split-view UI (fan-out over Phase 1
  planner).

## Phase 2 — Retrieval & structure (local models / DuckDB)
- 🔨 **Hybrid retrieval + RRF** — add BM25 (pure Python) fused with the existing
  MaxSim embedder via Reciprocal Rank Fusion; cross-encoder rerank when a local
  GPU is available. (No API.)
- 🔨 **Table Intelligence agent** — parse financial tables (merged cells,
  multi-level headers, units/currencies) into structured rows.
- 🔨 **SQL agent (DuckDB)** — persist extracted figures; NL→SQL over them
  (*"Apple's avg gross margin over 5 years"*), executed locally.
- 🧱 **Chart Intelligence (ChartQA)** — VLM chart reading (needs a vision model +
  GPU).

## Phase 3 — Knowledge & memory
- 🔨 **Long-term memory** — semantic store over prior questions/answers/summaries
  (reuses the vector store).
- 🧱 **Knowledge Graph agent (Neo4j)** — companies, execs, segments, competitors,
  suppliers, risks; graph traversal + an explorer UI. (Needs Neo4j service.)
- 🔨 **Time-aware research** — trend analysis across 2021–2025 filings
  (multi-document reasoning is already partially supported).

## Phase 4 — Live data & multi-source
- 🔨 **SEC EDGAR** — ✅ live (full universe, all forms). Add 8-K/transcripts.
- 🧱 **Market/macro data** — Yahoo Finance / FMP / Polygon / AlphaVantage / FRED.
  (Each is an external API — added behind flags, off by default to keep the
  "no-API" local mode intact.)

## Phase 5 — Platform / MLOps (needs infrastructure)
- ✅ Tracing (JSONL spans), cost accounting, eval gate in CI.
- 🧱 **OpenTelemetry + Prometheus + Grafana** dashboards.
- 🧱 **MLflow** experiment tracking; continuous evaluation.
- 🧱 **Ray / Celery + Redis** for parallel agent execution & background ingestion;
  **Kafka** for streaming ingest.
- 🔨 **MCP server** exposing tools (search_filing, compare_companies, run_sql,
  generate_report, extract_table, research_company, export_report).
- 🔨 Async/streaming responses, caching, batch embeddings, incremental indexing,
  circuit breakers.

## Principle
Every 🧱 (infra) item ships **behind a feature flag, off by default**, so the
core stays **fully local, no-API, single-command runnable**. Nothing here breaks
existing functionality — it's additive.
