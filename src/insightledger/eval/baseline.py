"""Naive text-RAG baseline for an honest side-by-side comparison.

This is the "everyone's portfolio project": single-vector-ish top-1 chunk
retrieval, no layout/regions, no verifier, no abstention — it just stuffs the
top chunk into a template answer. It exists so the README can show what the
multi-agent visual pipeline *buys* you (recall, citation grounding, and far
lower hallucination on unanswerable questions), measured on the same golden
set with the same metrics.
"""
from __future__ import annotations

from typing import Optional

from ..config import Settings, get_settings
from ..knowledge import ensure_sample_index
from ..observability.tracing import get_tracer
from ..retrieval import Retriever
from ..schemas import (Answer, Citation, Difficulty, EvalItem, EvalReport,
                       Query, RetrievedPage)
from .golden_set import load_golden
from .metrics import aggregate, score_item


def _baseline_answer(question: str, retriever: Retriever,
                     doc_ids: Optional[list[str]]) -> Answer:
    # top-1 retrieval, no region awareness, no verification, no abstention
    q = Query(text=question, top_k=1, doc_ids=doc_ids)
    pages: list[RetrievedPage] = retriever.retrieve(q)
    if not pages:
        return Answer(query=question, text="", model_used="baseline")
    top = pages[0]
    # naive RAG: hand the top retrieved chunk back verbatim (no extraction,
    # no region grounding, no verification, no abstention).
    snippet = " ".join((top.text or "").split())[:400]
    # page-level citation only (no bbox) — that's the whole point of the contrast
    cite = Citation(doc_id=top.doc_id, page_number=top.page_number, snippet=snippet)
    return Answer(
        query=question,
        text=f"{snippet}",
        citations=[cite],
        claims=[],                       # no grounded/verified claims
        confidence=0.5,
        difficulty=Difficulty.simple,
        retrieved=pages,
        model_used="baseline-textrag",
    )


def run_baseline(items: Optional[list[EvalItem]] = None,
                 settings: Optional[Settings] = None) -> EvalReport:
    from .harness import eval_settings

    s = eval_settings(settings)
    retriever = ensure_sample_index(settings=s, force=True)
    items = items or load_golden(s=s)
    results = []
    for it in items:
        import time
        t0 = time.perf_counter()
        ans = _baseline_answer(it.question, retriever, it.doc_ids or None)
        ans.latency_ms = (time.perf_counter() - t0) * 1000
        ans.trace_id = get_tracer().trace_id
        results.append(score_item(it, ans))
    return aggregate(results, backends={**s.backend_report(), "mode": "baseline-textrag"})


def compare(settings: Optional[Settings] = None) -> dict:
    from .harness import run_eval

    base = run_baseline(settings=settings)
    full = run_eval(settings=settings, save=False)
    return {"baseline": base, "insightledger": full}


def format_compare(cmp: dict) -> str:
    b: EvalReport = cmp["baseline"]
    f: EvalReport = cmp["insightledger"]
    rows = [
        ("recall@k", b.recall_at_k, f.recall_at_k),
        ("faithfulness", b.faithfulness, f.faithfulness),
        ("answer_accuracy", b.answer_accuracy, f.answer_accuracy),
        ("citation_iou", b.mean_citation_iou, f.mean_citation_iou),
        ("hallucination", b.hallucination_rate, f.hallucination_rate),
    ]
    out = ["", f"{'metric':<18}{'text-RAG':>12}{'Recto':>16}{'delta':>10}",
           "-" * 56]
    for name, bv, fv in rows:
        out.append(f"{name:<18}{bv:>12.3f}{fv:>16.3f}{(fv-bv):>+10.3f}")
    return "\n".join(out)
