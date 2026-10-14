"""In-memory store: dimension changes, concurrent ingest, eviction."""
import threading
from dataclasses import replace

import numpy as np

from insightledger.config import get_settings
from insightledger.retrieval.store import MemoryStore


def _store(tmp_path, dim):
    return MemoryStore(settings=replace(get_settings(), data_dir=tmp_path), dim=dim)


def test_an_index_built_at_another_dimension_is_discarded_not_loaded(tmp_path):
    old = _store(tmp_path, 128)
    old.upsert("A::1", np.ones((3, 128), np.float32), {"doc_id": "A", "page_number": 1})
    old.save()
    new = _store(tmp_path, 64)
    new.load()
    assert new.count() == 0, "128-d vectors would crash against 64-d queries"


def test_delete_removes_every_page_of_one_filing(tmp_path):
    s = _store(tmp_path, 4)
    for doc in ("A", "B"):
        for n in (1, 2):
            s.upsert(f"{doc}::{n}", np.ones((2, 4), np.float32), {"doc_id": doc, "page_number": n})
    assert s.delete_doc("A") == 2
    assert s.count() == 2 and all(p["doc_id"] == "B" for p in s._payloads.values())


def test_search_survives_an_ingest_running_alongside(tmp_path):
    s = _store(tmp_path, 8)
    for n in range(300):
        s.upsert(f"A::{n}", np.random.rand(5, 8).astype(np.float32), {"doc_id": "A", "page_number": n})
    stop, errors = threading.Event(), []

    def ingest():
        # a burst like a real filing landing mid-search, not an unbounded flood
        for n in range(1000, 3000):
            if stop.is_set():
                return
            s.upsert(f"B::{n}", np.random.rand(5, 8).astype(np.float32), {"doc_id": "B", "page_number": n})

    t = threading.Thread(target=ingest); t.start()
    try:
        for _ in range(60):
            s.search(np.random.rand(3, 8).astype(np.float32), top_k=5, weighting="idf")
    except RuntimeError as e:          # "dictionary changed size during iteration"
        errors.append(e)
    finally:
        stop.set(); t.join()
    assert not errors, errors


# ---------------------------------------------- shared rows (deduplication)

def _rand_pages(n_pages=40, vocab=60, dim=16, seed=0):
    rng = np.random.default_rng(seed)
    words = rng.normal(size=(vocab, dim)).astype(np.float32)
    words /= np.linalg.norm(words, axis=1, keepdims=True)
    return [words[rng.choice(vocab, size=rng.integers(5, 30), replace=False)] for _ in range(n_pages)]


def test_identical_rows_are_stored_once_and_scores_do_not_change(tmp_path):
    """Hashed stub vectors repeat every common word on every page; only 5.7% of
    the live index's rows were distinct. Storing each row once must not change
    a single score."""
    from insightledger.retrieval.embedder import maxsim
    s = _store(tmp_path, 16)
    pages = _rand_pages()
    for i, m in enumerate(pages):
        s.upsert(f"D::{i}", m, {"doc_id": "D", "page_number": i})
    assert s.vocab_rows <= 60, "a repeated row was stored twice"
    q = pages[3][:4]
    got = {h.key: h.score for h in s.search(q, top_k=40)}
    for i, m in enumerate(pages):
        assert abs(got[f"D::{i}"] - maxsim(q, m)) < 1e-4


def test_shared_rows_survive_save_and_load(tmp_path):
    s = _store(tmp_path, 16)
    pages = _rand_pages(seed=1)
    for i, m in enumerate(pages):
        s.upsert(f"D::{i}", m, {"doc_id": "D", "page_number": i})
    s.save()
    t = _store(tmp_path, 16); t.load()
    q = pages[5][:3]
    assert [h.key for h in t.search(q, top_k=5)] == [h.key for h in s.search(q, top_k=5)]


def test_an_index_saved_in_the_old_per_page_format_still_loads(tmp_path):
    import json
    pages = _rand_pages(n_pages=5, seed=2)
    (tmp_path / "index").mkdir(parents=True, exist_ok=True)
    np.savez_compressed(str(tmp_path / "index" / "memory_index.npz"),
                        **{f"D::{i}": m for i, m in enumerate(pages)})
    (tmp_path / "index" / "memory_index.json").write_text(json.dumps(
        {f"D::{i}": {"doc_id": "D", "page_number": i} for i in range(5)}))
    s = _store(tmp_path, 16); s.load()
    assert s.count() == 5 and s.search(pages[0][:2], top_k=1)[0].key == "D::0"


def test_scoring_in_blocks_gives_the_same_ranking(tmp_path, monkeypatch):
    from insightledger.retrieval import store as store_mod
    rng = np.random.default_rng(7)
    s = _store(tmp_path, 8)
    for n in range(40):
        s.upsert(f"A::{n}", rng.random((6, 8), dtype=np.float32), {"doc_id": "A", "page_number": n})
    q = rng.random((9, 8), dtype=np.float32)
    whole = [(h.key, round(h.score, 5)) for h in s.search(q, top_k=40, weighting="idf")]
    monkeypatch.setattr(store_mod, "_GATHER_CELLS", 7)      # one token per block
    blocked = [(h.key, round(h.score, 5)) for h in s.search(q, top_k=40, weighting="idf")]
    assert blocked == whole
