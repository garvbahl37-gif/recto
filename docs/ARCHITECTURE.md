# Architecture

Recto is a **visual-RAG** system: retrieval and reasoning happen over
rendered document *pages* rather than extracted plaintext, and a multi-agent
graph turns retrieved pages into a grounded, cited, verified answer — or a
principled abstention.

## Data flow

```
                  ┌─────────────── ingestion ───────────────┐
 SEC EDGAR / PDF ─▶ render pages ─▶ layout regions ─▶ Document(pages[, regions])
                  └──────────────────────┬───────────────────┘
                                         ▼
                     Indexer: page ─▶ multi-vector embedding ─▶ VectorStore
                                         │
 question ───────────────────────────────┼──────────────── query time ─────────
                                         ▼
    router ─▶ retriever ─▶ extractor ─▶ verifier ─┐(conf < τ, retries left)
       ▲          (MaxSim)   (VLM read)  (grounds  │   widen top_k
       └───────── retriever ◀──────────── claims)  │
                                                    └▶ synthesizer ─▶ Answer{
                                                          text, citations[bbox],
                                                          claims, confidence,
                                                          cost, latency, trace }
```

## Key design decisions

### 1. Late-interaction (multi-vector) retrieval as the core interface
Both embedders return a `(tokens, dim)` matrix and are scored with **MaxSim**
(`Σ_i max_j ⟨q_i, d_j⟩`) — the ColPali/ColQwen scoring function. The real
backend embeds page images with ColQwen2; the stub hashes tokens into unit
vectors so MaxSim degrades to soft lexical late-interaction. Same store, same
scorer, same `Retriever` — swapping backends changes nothing downstream.
See `retrieval/embedder.py`.

### 2. The provider exposes *semantic operations*, not raw text→text
`assess_difficulty · extract_claims · verify_claim · synthesize`. Real (Claude)
and stub implement the same interface, so the agent graph is backend-agnostic
and the stub can be **grounded and deterministic** — it locates answer text in
the retrieved pages and builds region citations, which is what makes the full
pipeline runnable and the eval meaningful with zero credentials.
See `llm/provider.py`.

### 3. A real cycle, not a linear chain
The verifier's confidence gates a conditional edge back to the retriever
(widening `top_k` on retry). This is the LangGraph feature that separates
senior orchestration from a prompt chain, and it's what recovers the tail of
hard cross-page questions. The linear fallback runs the identical node
functions in a `while` loop so behaviour is preserved without LangGraph.
See `agents/orchestrator.py`.

### 4. Abstention is a first-class outcome
A claim survives only if it hits a **focus keyword** — a question content word
that isn't a generic finance term, a number/year, or an entity identifier
derived from the doc IDs. A question whose focus term ("compensation",
"dividend") is absent from the corpus yields no claims, so the system abstains
instead of fabricating. This is measured directly by adversarial golden items.
See `StubProvider._focus_keywords` and the `abstain`-tagged eval items.

### 5. Cost-aware routing
The router labels each question `simple | complex`. The Claude provider sends
simple lookups to a cheap model (Haiku) and complex reasoning to a strong model
(Opus), and every call is metered (`observability/cost.py`) so cost-per-query
is a reported number, not a guess.

### 6. Eval-gated, observable
Every request writes a JSONL span trace (`observability/tracing.py`,
surfaced at `GET /trace/{id}`). The eval harness computes recall@k,
faithfulness, answer accuracy, citation IoU, hallucination rate, and
latency/cost percentiles, then a **gate** (`eval/harness.py`) fails CI on
regression. A naive text-RAG baseline runs the same metrics for an honest
contrast (`eval/baseline.py`).

## Metrics, precisely

| Metric | Definition |
|---|---|
| recall@k | gold page ∈ retrieved top-k |
| faithfulness | answer backed by ≥1 verified citation (or a correct abstention) |
| answer accuracy | all expected substrings present in the answer |
| citation IoU | best IoU of a cited region vs. the gold region |
| hallucination rate | fabricated/ungrounded claim, or citing on an unanswerable Q |

## Extending

- **New document source** → return `Document` (see `ingestion/pipeline.py`); the
  index/retrieve/agent stack is unchanged.
- **New agent** (e.g. a table-normalizer) → add a node + edge in
  `agents/orchestrator.py`.
- **Real vision stack** → `pip install -e .[vision]`; `IL_EMBEDDER=auto` picks
  ColQwen when `torch`, `transformers`, `colpali-engine` are present.
- **Production store** → `pip install -e .[qdrant]` + `IL_VECTOR_STORE=qdrant`.
