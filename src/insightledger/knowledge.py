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
import re
import threading
import time
from typing import Callable, Optional

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


# ------------------------------------------------- fetched-filing budget ----

_LAST_USED: dict[str, float] = {}
_FETCH_LOCK = threading.RLock()
_FETCH_TIMES: list[float] = []          # when each recent EDGAR fetch started
_FETCH_WINDOW = 600.0                    # seconds


def reserve_fetch(settings: Optional[Settings] = None) -> Optional[str]:
    """Claim one EDGAR fetch from the service-wide allowance.

    Returns None when the fetch may go ahead, or the reason it may not. Every
    path that pulls a filing from EDGAR on a visitor's behalf goes through
    here, so no visitor can make the server hammer the SEC or fill its memory.
    """
    s = settings or get_settings()
    now = time.time()
    with _FETCH_LOCK:
        _FETCH_TIMES[:] = [t for t in _FETCH_TIMES if now - t < _FETCH_WINDOW]
        if len(_FETCH_TIMES) >= max(0, s.fetch_rate):
            wait = int(_FETCH_WINDOW - (now - _FETCH_TIMES[0])) + 1
            return (f"Fetch limit reached: {s.fetch_rate} filings per ten minutes "
                    f"across the service. Try again in about {max(1, wait // 60)} min.")
        _FETCH_TIMES.append(now)
    return None
_FORM = re.compile(r"-(10-K|10-Q|8-K|20-F|40-F|DEF 14A)-\d{4}-\d{2}-\d{2}$")


def _ticker(doc_id: str) -> str:
    m = _FORM.search(doc_id)
    return doc_id[: m.start()] if m else doc_id.split("-")[0]


def touch(doc_ids) -> None:
    """Record that these filings were just read (for least-recently-used eviction)."""
    now = time.time()
    for d in doc_ids:
        _LAST_USED[d] = now


def evict_over_budget(settings: Optional[Settings] = None,
                      retriever: Optional[Retriever] = None) -> list[str]:
    """Drop the least recently read fetched filings once there are more than
    `max_fetched` of them. Seeds are never evicted. On a 512 MB server this is
    what lets anyone ask about any company without the index outgrowing memory."""
    s = settings or get_settings()
    reg = load_registry(s)
    seeds = {t.strip().upper() for t in s.seed_tickers.split(",") if t.strip()}
    fetched = [d for d in reg if _ticker(d).upper() not in seeds]
    over = len(fetched) - max(0, s.max_fetched)
    if over <= 0:
        return []
    victims = sorted(fetched, key=lambda d: _LAST_USED.get(d, 0.0))[:over]
    retriever = retriever or Retriever(settings=s)
    for d in victims:
        retriever.store.delete_doc(d)
        _LAST_USED.pop(d, None)
    retriever.store.save()
    save_registry([v for k, v in reg.items() if k not in victims], s)
    return victims


def fetch_for_question(question: str, retriever: Retriever,
                       settings: Optional[Settings] = None,
                       emit: Optional[Callable[..., None]] = None) -> list[str]:
    """Fetch the filings of EDGAR companies a question names but Recto has not
    read, and return the doc_ids to scope the question to. Reports progress
    through `emit(stage, **payload)`: fetch, then fetched or fetch_failed."""
    from .agents.entities import edgar_companies_in

    s = settings or get_settings()
    say = emit or (lambda *a, **k: None)
    try:
        companies = EdgarClient(s)._load_companies()
    except Exception:  # noqa: BLE001 — no EDGAR list, no fetch; answer from the shelf
        return []
    found = edgar_companies_in(question, companies)
    out: list[str] = []
    for c in found:
        tk, name = c["ticker"], c["title"]
        with _FETCH_LOCK:          # two questions about the same new company fetch once
            have = [d for d in load_registry(s) if _ticker(d).upper() == tk.upper()]
            if have:
                out += have
                continue
            refusal = reserve_fetch(s)
            if refusal:
                say("fetch_failed", ticker=tk, company=name, message=refusal)
                continue
            say("fetch", ticker=tk, company=name)
            t0 = time.time()
            try:
                doc = ingest_ticker(tk, settings=s, retriever=retriever)
            except Exception as exc:  # noqa: BLE001
                say("fetch_failed", ticker=tk, company=name, message=str(exc)[:240])
                continue
            say("fetched", ticker=tk, company=name, doc_id=doc.doc_id, form=doc.doc_type,
                pages=len(doc.pages), seconds=round(time.time() - t0, 1))
            out.append(doc.doc_id)
    touch(out)
    return out


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
    touch([doc.doc_id])
    evict_over_budget(s, retriever)
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
    touch([doc.doc_id])
    # The catalogue's Index button is the same kind of fetch as asking about a
    # new company; without this the budget could be bypassed through /ingest.
    evict_over_budget(s, retriever)
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
