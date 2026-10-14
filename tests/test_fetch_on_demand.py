"""Asking about a company Recto has not read fetches its filing first."""
from dataclasses import replace

import pytest

from insightledger import knowledge
from insightledger.config import get_settings
from insightledger.ingestion.pipeline import build_document_from_pages

COMPANIES = [{"ticker": "KO", "title": "COCA COLA CO", "cik": 21344},
             {"ticker": "PEP", "title": "PEPSICO INC", "cik": 77476},
             {"ticker": "SBUX", "title": "STARBUCKS CORP", "cik": 829224}]


@pytest.fixture
def env(tmp_path, monkeypatch):
    s = replace(get_settings(), data_dir=tmp_path, seed_tickers="AAA", max_fetched=1, auto_fetch=True)
    fetched = []

    def fake_ingest(ticker, form="10-K", settings=None, retriever=None):
        fetched.append(ticker)
        doc = build_document_from_pages(
            f"{ticker}-10-K-2026-01-01", f"{ticker} 10-K (2026-01-01)",
            [f"{ticker} net revenues were $47,061 million in 2025."], source="t://", doc_type="10-K")
        doc.metadata["name"] = next(c["title"] for c in COMPANIES if c["ticker"] == ticker)
        from insightledger.retrieval import Indexer
        Indexer(embedder=retriever.embedder, store=retriever.store, settings=settings).index_documents([doc])
        reg = dict(knowledge.load_registry(settings)); reg[doc.doc_id] = doc
        knowledge.save_registry(list(reg.values()), settings)
        knowledge.touch([doc.doc_id])
        knowledge.evict_over_budget(settings, retriever)
        return doc

    class FakeEdgar:
        def __init__(self, *a, **k): pass
        def _load_companies(self): return COMPANIES

    monkeypatch.setattr(knowledge, "ingest_ticker", fake_ingest)
    monkeypatch.setattr(knowledge, "EdgarClient", FakeEdgar)
    from insightledger.retrieval import Retriever
    return s, Retriever(settings=s), fetched


def test_a_named_company_is_fetched_and_the_question_scoped_to_it(env):
    s, r, fetched = env
    events = []
    ids = knowledge.fetch_for_question("What was Coca-Cola's revenue?", r, s,
                                       emit=lambda st, **p: events.append((st, p)))
    assert fetched == ["KO"] and ids == ["KO-10-K-2026-01-01"]
    stages = [e[0] for e in events]
    assert stages == ["fetch", "fetched"] and events[1][1]["pages"] == 1


def test_a_company_already_on_the_shelf_is_not_fetched_again(env):
    s, r, fetched = env
    knowledge.fetch_for_question("What was Coca-Cola's revenue?", r, s)
    knowledge.fetch_for_question("How did Coca-Cola's margins move?", r, s)
    assert fetched == ["KO"]


def test_least_recently_used_fetch_is_evicted_past_the_budget(env):
    s, r, fetched = env                      # max_fetched=1
    knowledge.fetch_for_question("What was Coca-Cola's revenue?", r, s)
    knowledge.fetch_for_question("What was PepsiCo's revenue?", r, s)
    reg = knowledge.load_registry(s)
    assert "PEP-10-K-2026-01-01" in reg and "KO-10-K-2026-01-01" not in reg
    assert all(p.get("doc_id") != "KO-10-K-2026-01-01" for p in r.store._payloads.values())


def test_nothing_is_fetched_for_a_question_that_names_no_company(env):
    s, r, fetched = env
    assert knowledge.fetch_for_question("What are the most significant risk factors?", r, s) == []
    assert fetched == []
