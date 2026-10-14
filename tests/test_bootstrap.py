"""Seed tickers added after the first boot must still be ingested.

ensure_live_index used to return as soon as any index existed, so adding a
company to IL_SEED_TICKERS did nothing on an instance that already had data.
"""
from dataclasses import replace

from insightledger import knowledge
from insightledger.config import get_settings
from insightledger.ingestion.pipeline import build_document_from_pages


def _fake_ingest(calls):
    def ingest(ticker, form="10-K", settings=None, retriever=None):
        calls.append(ticker)
        doc = build_document_from_pages(
            f"{ticker}-10-K-2026-01-01", f"{ticker} CORP 10-K (2026-01-01)",
            [f"{ticker} revenue was $1,000 million."], source="test://", doc_type="10-K")
        reg = knowledge.load_registry(settings)
        reg[doc.doc_id] = doc
        knowledge.save_registry(list(reg.values()), settings)
        # put something in the store so later calls see a non-empty index
        from insightledger.retrieval import Indexer
        Indexer(embedder=retriever.embedder, store=retriever.store,
                settings=settings).index_documents([doc])
        return doc
    return ingest


def test_new_seed_ticker_is_ingested_into_existing_index(tmp_path, monkeypatch):
    s = replace(get_settings(), data_dir=tmp_path, seed_tickers="AAA")
    calls = []
    monkeypatch.setattr(knowledge, "ingest_ticker", _fake_ingest(calls))

    knowledge.ensure_live_index(s)
    assert calls == ["AAA"]

    s2 = replace(s, seed_tickers="AAA,BBB")
    knowledge.ensure_live_index(s2)
    assert calls == ["AAA", "BBB"], "BBB was added to the seeds but never ingested"


def test_already_ingested_seeds_are_not_refetched(tmp_path, monkeypatch):
    s = replace(get_settings(), data_dir=tmp_path, seed_tickers="AAA,BBB")
    calls = []
    monkeypatch.setattr(knowledge, "ingest_ticker", _fake_ingest(calls))
    knowledge.ensure_live_index(s)
    knowledge.ensure_live_index(s)
    assert calls == ["AAA", "BBB"]
