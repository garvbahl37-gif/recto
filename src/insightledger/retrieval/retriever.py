"""Query-time retrieval: embed query -> MaxSim search -> RetrievedPage list."""
from __future__ import annotations

from typing import Optional

from ..config import Settings, get_settings
from ..observability.tracing import Tracer
from ..schemas import Query, Region, RetrievedPage
from .embedder import Embedder, get_embedder
from .lineitems import line_item_hits, question_phrases
from .store import VectorStore, get_store

_RERANK_DEPTH = 80       # candidates the line-item pass may re-order
_LINE_ITEM_LIFT = 0.75   # score multiplier per phrase printed as a line item


class Retriever:
    def __init__(self, embedder: Optional[Embedder] = None,
                 store: Optional[VectorStore] = None,
                 settings: Optional[Settings] = None):
        self.s = settings or get_settings()
        self.embedder = embedder or get_embedder(self.s)
        self.store = store or get_store(self.s, dim=getattr(self.embedder, "dim", 128))

    def retrieve(self, query: Query, tracer: Optional[Tracer] = None) -> list[RetrievedPage]:
        qv = self.embedder.embed_query(query.text)
        # The stub's hashed vectors match only on identical words, which makes
        # rarity weighting exact. Real visual embeddings keep plain MaxSim.
        weighting = "idf" if getattr(self.embedder, "name", "") == "stub" else "none"
        # Pull a deeper candidate list than asked for, then let line items
        # re-order it: the page that prints "Net income $ 58,471" should beat
        # forty pages that only talk about net income.
        depth = max(query.top_k, _RERANK_DEPTH)
        hits = self.store.search(qv, top_k=depth, doc_ids=query.doc_ids,
                                 weighting=weighting)
        phrases = question_phrases(query.text)
        if phrases and hits:
            def boosted(h):
                n = line_item_hits(h.payload.get("text", ""), phrases)
                return h.score * (1.0 + _LINE_ITEM_LIFT * min(n, 2))
            rescored = sorted(((boosted(h), i, h) for i, h in enumerate(hits)),
                              key=lambda t: (-t[0], t[1]))
            hits = [h.__class__(key=h.key, score=sc, payload=h.payload)
                    for sc, _, h in rescored]
        hits = hits[:query.top_k]
        pages: list[RetrievedPage] = []
        for h in hits:
            p = h.payload
            regions = [Region.model_validate(r) for r in p.get("regions", [])]
            pages.append(RetrievedPage(
                doc_id=p.get("doc_id", ""),
                page_number=int(p.get("page_number", 0)),
                score=h.score,
                image_path=p.get("image_path"),
                text=p.get("text", ""),
                regions=regions,
            ))
        if tracer:
            tracer.event("retrieved", k=len(pages),
                         pages=[f"{p.doc_id} p.{p.page_number}" for p in pages])
        return pages
