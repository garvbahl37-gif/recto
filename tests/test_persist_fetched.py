"""In production, filings fetched for a question live in memory only.

Render's disk resets to the image on every restart, so writing a fetched
filing out cost seconds on a slow CPU and kept nothing. Seeds are always
written, because the Docker build bakes them into the image.
"""
from dataclasses import replace

from insightledger import knowledge
from insightledger.config import get_settings
from insightledger.ingestion.pipeline import build_document_from_pages
from insightledger.retrieval import Retriever


def _doc(ticker):
    d = build_document_from_pages(f"{ticker}-10-K-2026-01-01", f"{ticker} 10-K (2026-01-01)",
                                  [f"{ticker} net revenue was $47,061 million in 2025."],
                                  source="t://", doc_type="10-K")
    d.metadata["name"] = ticker
    return d


def test_a_fetched_filing_is_kept_in_memory_and_a_seed_on_disk(tmp_path):
    s = replace(get_settings(), data_dir=tmp_path, seed_tickers="AAA", persist_fetched=False,
                max_fetched=5)
    r = Retriever(settings=s)
    knowledge._shelve(_doc("AAA"), s, r)                     # a seed: written
    on_disk = (s.index_dir / "doc_registry.json").read_text()
    index_bytes = (s.index_dir / "memory_index.json").read_bytes()
    knowledge._shelve(_doc("KO"), s, r)                      # fetched: memory only
    assert (s.index_dir / "doc_registry.json").read_text() == on_disk
    assert (s.index_dir / "memory_index.json").read_bytes() == index_bytes
    assert set(knowledge.load_registry(s)) == {"AAA-10-K-2026-01-01", "KO-10-K-2026-01-01"}
    assert {h.payload["doc_id"] for h in r.store.search(r.embedder.embed_query("KO net revenue"), 5)} \
        >= {"KO-10-K-2026-01-01"}


def test_eviction_in_memory_leaves_the_image_files_alone(tmp_path):
    s = replace(get_settings(), data_dir=tmp_path, seed_tickers="AAA", persist_fetched=False,
                max_fetched=1)
    r = Retriever(settings=s)
    knowledge._shelve(_doc("AAA"), s, r)
    on_disk = (s.index_dir / "doc_registry.json").read_text()
    knowledge._shelve(_doc("KO"), s, r)
    knowledge.touch(["KO-10-K-2026-01-01"])
    knowledge._shelve(_doc("PEP"), s, r)                     # over budget: KO goes
    assert set(knowledge.load_registry(s)) == {"AAA-10-K-2026-01-01", "PEP-10-K-2026-01-01"}
    assert (s.index_dir / "doc_registry.json").read_text() == on_disk
