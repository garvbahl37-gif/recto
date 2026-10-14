"""Corpus + index bootstrap shared by the API, CLI and eval harness.

The application indexes **live SEC EDGAR filings** — no mock data. On first boot
it ingests the seed tickers (IL_SEED_TICKERS, default AAPL,MSFT,NVDA); more can
be added at runtime via `ingest_ticker` / the `POST /ingest` endpoint. A doc
registry is persisted so the UI can render page text / regions for citations.

(The deterministic synthetic corpus still exists, but only as a labelled test
fixture for the eval harness — see `eval/golden_set.py`. It is never served by
the app.)
"""
from __future__ import annotations

import json
from typing import Optional

from .config import Settings, get_settings
from .ingestion import EdgarClient
from .retrieval import Indexer, Retriever
from .schemas import Document


def _registry_path(s: Settings):
    return s.index_dir / "doc_registry.json"


# path -> (mtime_ns, size, parsed registry). Thirteen filings make a 9 MB
# registry; parsing and validating it on every request (and once per claim in
# the verifier) cost far more than the request itself.
_REGISTRY_CACHE: dict[str, tuple[int, int, dict[str, Document]]] = {}


def save_registry(docs: list[Document], s: Optional[Settings] = None) -> None:
    s = s or get_settings()
    s.index_dir.mkdir(parents=True, exist_ok=True)
    _registry_path(s).write_text(
        json.dumps({d.doc_id: d.model_dump(mode="json") for d in docs}),
        encoding="utf-8")
    _REGISTRY_CACHE.pop(str(_registry_path(s)), None)


def load_registry(s: Optional[Settings] = None) -> dict[str, Document]:
    """The document registry, parsed once and reused until the file changes.

    Callers get a shared dict: copy it before mutating (ingest does).
    """
    s = s or get_settings()
    p = _registry_path(s)
    if not p.exists():
        return {}
    st = p.stat()
    key = str(p)
    hit = _REGISTRY_CACHE.get(key)
    if hit and hit[0] == st.st_mtime_ns and hit[1] == st.st_size:
        return hit[2]
    raw = json.loads(p.read_text(encoding="utf-8"))
    reg = {k: Document.model_validate(v) for k, v in raw.items()}
    _REGISTRY_CACHE[key] = (st.st_mtime_ns, st.st_size, reg)
    return reg


def ingest_ticker(ticker: str, form: str = "10-K",
                  settings: Optional[Settings] = None,
                  retriever: Optional[Retriever] = None) -> Document:
    """Fetch a live filing from EDGAR, index it, and register it."""
    s = settings or get_settings()
    s.ensure_dirs()
    retriever = retriever or Retriever(settings=s)
    doc = EdgarClient(s).fetch(ticker, form=form)
    Indexer(embedder=retriever.embedder, store=retriever.store,
            settings=s).index_documents([doc])
    reg = dict(load_registry(s))
    reg[doc.doc_id] = doc
    save_registry(list(reg.values()), s)
    return doc


def ingest_filing(ticker: str, accession: str, primary_doc: str, form: str,
                  date: str, settings: Optional[Settings] = None,
                  retriever: Optional[Retriever] = None) -> Document:
    """Fetch + index one specific EDGAR filing (by accession) and register it."""
    s = settings or get_settings()
    s.ensure_dirs()
    retriever = retriever or Retriever(settings=s)
    doc = EdgarClient(s).fetch_specific(ticker, accession, primary_doc, form, date)
    Indexer(embedder=retriever.embedder, store=retriever.store,
            settings=s).index_documents([doc])
    reg = dict(load_registry(s))
    reg[doc.doc_id] = doc
    save_registry(list(reg.values()), s)
    return doc


def ensure_live_index(settings: Optional[Settings] = None,
                      force: bool = False) -> Retriever:
    """Ensure the seed filings are ingested; return a ready Retriever.

    Fails soft: if EDGAR is unreachable for a ticker, it is skipped so the app
    still starts with whatever was ingested.
    """
    s = settings or get_settings()
    s.ensure_dirs()
    retriever = Retriever(settings=s)
    reg = load_registry(s)
    if retriever.store.count() == 0:
        reg = {}            # registry without vectors is stale; rebuild both
    seeds = [t.strip().upper() for t in s.seed_tickers.split(",") if t.strip()]

    def indexed(ticker: str) -> bool:
        # doc_ids are "<TICKER>-<FORM>-<DATE>"; match the whole ticker so
        # "BRK-B" is not mistaken for "BRK" and refetched on every boot.
        return any(doc_id.upper().startswith(ticker + "-") for doc_id in reg)

    # Only seeds that are missing cost anything, so a warm instance with an
    # up-to-date index starts without touching the network. (This used to
    # return early whenever any index existed, so seeds added later were
    # never ingested.)
    for ticker in seeds:
        if not force and indexed(ticker):
            continue
        try:
            doc = ingest_ticker(ticker, form=s.seed_form, settings=s, retriever=retriever)
            print(f"[ingest] {ticker}: {doc.doc_id} ({len(doc.pages)} pages)")
        except Exception as exc:  # noqa: BLE001
            print(f"[ingest] {ticker} skipped: {exc}")
    return retriever


# Backwards-compatible alias used by older call sites / eval.
def ensure_sample_index(force: bool = False,
                        settings: Optional[Settings] = None) -> Retriever:
    """Eval-only: index the synthetic golden-set corpus (labelled fixture)."""
    from .ingestion import build_sample_corpus

    s = settings or get_settings()
    s.ensure_dirs()
    retriever = Retriever(settings=s)
    if not force and retriever.store.count() > 0 and _registry_path(s).exists():
        return retriever
    docs = build_sample_corpus()
    Indexer(embedder=retriever.embedder, store=retriever.store,
            settings=s).index_documents(docs)
    save_registry(docs, s)
    return retriever
