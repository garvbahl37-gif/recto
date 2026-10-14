"""FastAPI service exposing the visual-RAG pipeline.

    GET  /health              backends + index size
    POST /query               {question, top_k?, doc_ids?} -> grounded Answer
    GET  /documents           registered documents
    GET  /documents/{id}      full document (pages + regions) for UI rendering
    GET  /trace/{trace_id}    per-request agent span trace
    POST /eval                run the eval harness on demand
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from ..agents import Orchestrator
from ..config import get_settings
from ..knowledge import (ensure_live_index, ensure_sample_index, ingest_filing,
                         ingest_ticker, load_registry)
from ..schemas import Answer

app = FastAPI(title="Recto", version="0.2.0",
              description="Visual-first document intelligence (visual-RAG + multi-agent).")

_orch: Optional[Orchestrator] = None


def _orchestrator() -> Orchestrator:
    global _orch
    if _orch is None:
        s = get_settings()
        # the app serves LIVE SEC EDGAR filings; 'sample' is offline test mode
        if s.bootstrap == "sample":
            ensure_sample_index()
        else:
            ensure_live_index()
        _orch = Orchestrator()
    return _orch


class QueryRequest(BaseModel):
    # A question is a sentence or two; the caps keep one request from reading
    # every page on the shelf or filling the model's context.
    question: str = Field(max_length=2000)
    top_k: Optional[int] = Field(default=None, ge=1, le=20)
    doc_ids: Optional[list[str]] = Field(default=None, max_length=50)


class IngestRequest(BaseModel):
    ticker: str
    form: str = "10-K"
    # optional: ingest one SPECIFIC filing (from the company browser) instead of latest
    accession: Optional[str] = None
    primary_doc: Optional[str] = None
    date: Optional[str] = None


_STATIC = Path(__file__).parent / "static"
_ASSETS = _STATIC / "assets"
if _ASSETS.is_dir():
    app.mount("/assets", StaticFiles(directory=str(_ASSETS)), name="assets")


def _page() -> str:
    return (_STATIC / "index.html").read_text(encoding="utf-8")


@app.get("/favicon.svg")
def favicon():
    from fastapi.responses import FileResponse
    return FileResponse(_STATIC / "favicon.svg", media_type="image/svg+xml")


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    """Landing page (client-side routed)."""
    return _page()


@app.get("/app", response_class=HTMLResponse)
@app.get("/app/{_rest:path}", response_class=HTMLResponse)
def console(_rest: str = "") -> str:
    """Research console. Same SPA shell — the client routes on pathname."""
    return _page()


@app.get("/health")
def health() -> dict:
    s = get_settings()
    orch = _orchestrator()
    return {
        "status": "ok",
        "backends": s.backend_report(),
        "indexed_pages": orch.retriever.store.count(),
    }


@app.post("/query", response_model=Answer)
def query(req: QueryRequest) -> Answer:
    if not req.question.strip():
        raise HTTPException(400, "question is required")
    return _orchestrator().answer(req.question, top_k=req.top_k, doc_ids=req.doc_ids)


@app.post("/query/stream")
def query_stream(req: QueryRequest) -> StreamingResponse:
    """The same pipeline as /query, reported stage by stage as Server-Sent
    Events: router, retriever (with the pages it chose), extractor, verifier,
    synthesizer, then the answer. A model call can take twenty seconds; the
    reader should watch it work rather than stare at a spinner."""
    import queue
    import threading

    if not req.question.strip():
        raise HTTPException(400, "question is required")
    events: "queue.Queue[Optional[dict]]" = queue.Queue()

    def emit(stage: str, payload: dict) -> None:
        events.put({"stage": stage, **payload})

    def run() -> None:
        try:
            ans = _orchestrator().answer(req.question, top_k=req.top_k,
                                         doc_ids=req.doc_ids, emit=emit)
            events.put({"stage": "answer", "answer": ans.model_dump(mode="json")})
        except Exception as exc:  # noqa: BLE001
            events.put({"stage": "error", "message": str(exc)[:300]})
        finally:
            events.put(None)

    threading.Thread(target=run, daemon=True).start()

    def stream():
        yield ": open\n\n"
        while True:
            try:
                item = events.get(timeout=15)
            except queue.Empty:
                # a comment line keeps proxies from closing an idle stream
                # while the model is thinking
                yield ": still reading\n\n"
                continue
            if item is None:
                return
            yield f"data: {json.dumps(item)}\n\n"

    return StreamingResponse(stream(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.get("/tickers")
def tickers(q: str = "", limit: int = 12) -> dict:
    """Search the full EDGAR company list for the picker."""
    from ..ingestion import EdgarClient

    try:
        results = EdgarClient(get_settings()).search_companies(q, limit=limit)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"EDGAR company list unavailable: {exc}")
    indexed = {d.doc_id.split("-")[0] for d in load_registry().values()}
    for r in results:
        r["indexed"] = r["ticker"] in indexed
    return {"results": results}


@app.get("/company")
def company(ticker: str, forms: str = "", limit: int = 30) -> dict:
    """Company profile + recent filing history — browse all filings of any of
    the ~10k EDGAR companies. `forms` is an optional comma-separated filter."""
    from ..ingestion import EdgarClient

    flt = [f.strip().upper() for f in forms.split(",") if f.strip()] or None
    try:
        data = EdgarClient(get_settings()).company_filings(
            ticker.strip().upper(), forms=flt, limit=limit)
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"EDGAR lookup failed: {exc}")
    indexed = {d.doc_id for d in load_registry().values()}
    for f in data["filings"]:
        f["doc_id"] = f"{data['ticker']}-{f['form']}-{f['date']}"
        f["indexed"] = f["doc_id"] in indexed
    return data


@app.post("/ingest")
def ingest(req: IngestRequest) -> dict:
    """Fetch + index a live SEC EDGAR filing on demand — the latest of a form,
    or one specific filing when an accession is supplied (company browser)."""
    tk = req.ticker.strip().upper()
    from ..ingestion import EdgarClient
    from ..knowledge import reserve_fetch

    # Check the company exists before spending from the shared allowance (the
    # ticker list is cached, so this costs no request to EDGAR); otherwise a
    # few made-up tickers would lock everyone out of fetching.
    try:
        EdgarClient(get_settings()).resolve_cik(tk)
    except ValueError as exc:
        raise HTTPException(404, str(exc))
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(502, f"EDGAR company list unavailable: {exc}")
    refusal, _slot = reserve_fetch()
    if refusal:
        raise HTTPException(429, refusal)
    try:
        if req.accession and req.primary_doc and req.date:
            doc = ingest_filing(tk, req.accession, req.primary_doc, req.form,
                                req.date, retriever=_orchestrator().retriever)
        else:
            doc = ingest_ticker(tk, form=req.form, retriever=_orchestrator().retriever)
    except Exception as exc:  # noqa: BLE001 — it reached EDGAR, so the slot stays spent
        raise HTTPException(502, f"EDGAR ingest failed: {exc}")
    return {"doc_id": doc.doc_id, "title": doc.title, "pages": len(doc.pages),
            "source": doc.source, "sections": sorted(
                {p.section for p in doc.pages if p.section})}


@app.get("/documents")
def documents() -> dict:
    reg = load_registry()
    return {"documents": [
        {"doc_id": d.doc_id, "title": d.title, "doc_type": d.doc_type,
         "pages": len(d.pages), "source": d.source,
         "date": d.metadata.get("date", ""),
         "company": d.metadata.get("name", d.title),
         "sections": sorted({p.section for p in d.pages if p.section})}
        for d in reg.values()]}


@app.get("/documents/{doc_id}")
def document(doc_id: str) -> dict:
    reg = load_registry()
    if doc_id not in reg:
        raise HTTPException(404, "document not found")
    return reg[doc_id].model_dump(mode="json")


@app.get("/documents/{doc_id}/pages/{page_number}")
def document_page(doc_id: str, page_number: int) -> dict:
    """One page and where it sits in its filing — what the reader shows beside
    an answer. Fetching the whole document for this moved megabytes per view."""
    doc = load_registry().get(doc_id)
    if doc is None:
        raise HTTPException(404, "document not found")
    page = next((p for p in doc.pages if p.page_number == page_number), None)
    if page is None:
        raise HTTPException(404, "page not found")
    return {
        "doc_id": doc.doc_id, "title": doc.title, "doc_type": doc.doc_type,
        "company": doc.metadata.get("name", doc.title),
        "date": doc.metadata.get("date", ""), "source": doc.source,
        "page_number": page.page_number, "total_pages": len(doc.pages),
        "section": page.section or "", "text": page.text or "",
        "regions": [r.model_dump(mode="json") for r in page.regions],
    }


@app.get("/trace/{trace_id}")
def trace(trace_id: str) -> dict:
    path = get_settings().traces_dir / f"{trace_id}.jsonl"
    if not path.exists():
        raise HTTPException(404, "trace not found")
    events = [json.loads(line) for line in
              path.read_text(encoding="utf-8").splitlines() if line.strip()]
    return {"trace_id": trace_id, "events": events}


@app.post("/eval")
def run_eval_endpoint() -> dict:
    from ..eval.harness import check_gate, run_eval

    report = run_eval()
    gate = check_gate(report)
    return {"report": report.model_dump(exclude={"results"}),
            "gate": {"passed": gate.passed, "failures": gate.failures}}
