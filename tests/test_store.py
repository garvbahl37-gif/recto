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
        n = 1000
        while not stop.is_set():
            s.upsert(f"B::{n}", np.random.rand(5, 8).astype(np.float32), {"doc_id": "B", "page_number": n})
            n += 1

    t = threading.Thread(target=ingest); t.start()
    try:
        for _ in range(60):
            s.search(np.random.rand(3, 8).astype(np.float32), top_k=5, weighting="idf")
    except RuntimeError as e:          # "dictionary changed size during iteration"
        errors.append(e)
    finally:
        stop.set(); t.join()
    assert not errors, errors
