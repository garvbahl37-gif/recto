"""Vector stores with a multi-vector (late-interaction) contract.

MemoryStore: numpy MaxSim over an in-process list. Persists to a .npz + json
sidecar so an index survives across processes (API, eval, CLI all share it).

QdrantStore: production multi-vector collection using Qdrant's native
MultiVectorConfig + MAX_SIM comparator.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Protocol

import numpy as np

from ..config import Settings, get_settings
from .embedder import best_per_token, idf_weights, maxsim


@dataclass
class Hit:
    key: str
    score: float
    payload: dict[str, Any]


class VectorStore(Protocol):
    def upsert(self, key: str, vectors: np.ndarray, payload: dict[str, Any]) -> None: ...
    def search(self, query_vectors: np.ndarray, top_k: int,
               doc_ids: Optional[list[str]] = None,
               weighting: str = "none") -> list[Hit]: ...
    def count(self) -> int: ...
    def save(self) -> None: ...
    def load(self) -> None: ...


# Cells in one block of the search gather (float32): 16 MB.
_GATHER_CELLS = 1 << 22


@dataclass
class MemoryStore:
    """In-process multi-vector store that keeps each distinct row once.

    A page's matrix holds one row per word, and the hashed stub vectors repeat
    every common word on every page: in the live index only 5.7% of 366,853
    rows were distinct. So rows live once in a shared table and each page
    keeps the numbers of its rows. Scores are unchanged, the vectors shrink
    from ~94 MB to ~7 MB, and a query is scored against the table once rather
    than against every page.
    """
    settings: Settings = field(default_factory=get_settings)
    dim: Optional[int] = None                      # expected width; None accepts any
    _ids: dict[str, np.ndarray] = field(default_factory=dict)       # page -> row ids
    _payloads: dict[str, dict[str, Any]] = field(default_factory=dict)
    _table: Optional[np.ndarray] = None            # capacity-doubling row table
    _n: int = 0                                    # rows in use
    _row_of: dict[int, int] = field(default_factory=dict)            # hash(row) -> id

    # -------------------------------------------------------------- rows
    @property
    def vocab_rows(self) -> int:
        return self._n

    def _vocab(self) -> np.ndarray:
        if self._table is None:
            return np.zeros((0, self.dim or 0), dtype=np.float32)
        return self._table[: self._n]

    def _row_ids(self, vectors: np.ndarray) -> np.ndarray:
        v = np.ascontiguousarray(vectors, dtype=np.float32)
        if v.ndim != 2:
            raise ValueError(f"expected a (rows, dim) matrix, got shape {v.shape}")
        if self.dim and v.shape[1] != self.dim:
            raise ValueError(f"expected width {self.dim}, got {v.shape[1]}")
        ids = np.empty(len(v), dtype=np.int32)
        fresh: list[np.ndarray] = []
        for i, row in enumerate(v):
            h = hash(row.tobytes())
            j = self._row_of.get(h)
            # compare on a hit, so a hash collision costs a row, never a score
            if j is not None and j < self._n and np.array_equal(self._table[j], row):
                ids[i] = j
                continue
            j = self._n + len(fresh)
            fresh.append(row)
            self._row_of.setdefault(h, j)
            ids[i] = j
        if fresh:
            self._append(np.vstack(fresh))
        return ids

    def _append(self, rows: np.ndarray) -> None:
        need = self._n + len(rows)
        if self._table is None or need > len(self._table):
            cap = max(need, 2 * (0 if self._table is None else len(self._table)), 1024)
            grown = np.empty((cap, rows.shape[1]), dtype=np.float32)
            if self._table is not None:
                grown[: self._n] = self._table[: self._n]
            self._table = grown
        self._table[self._n: need] = rows
        self._n = need                              # publish only once written

    @property
    def _vectors(self) -> dict[str, np.ndarray]:
        """Per-page matrices, rebuilt on demand (diagnostics and old callers)."""
        vocab = self._vocab()
        return {k: vocab[ids] for k, ids in list(self._ids.items())}

    # ------------------------------------------------------------- write
    def upsert(self, key: str, vectors: np.ndarray, payload: dict[str, Any]) -> None:
        ids = self._row_ids(vectors)               # rows land before the page points at them
        self._payloads[key] = payload
        self._ids[key] = ids

    def delete_doc(self, doc_id: str) -> int:
        """Drop every page of one filing; returns how many were removed. Rows
        it shared stay in the table for the pages still using them."""
        keys = [k for k, p in list(self._payloads.items()) if p.get("doc_id") == doc_id]
        for k in keys:
            self._ids.pop(k, None)
            self._payloads.pop(k, None)
        return len(keys)

    # ------------------------------------------------------------- read
    def search(self, query_vectors: np.ndarray, top_k: int,
               doc_ids: Optional[list[str]] = None,
               weighting: str = "none") -> list[Hit]:
        # Snapshot the page list before the table: an ingest on another thread
        # appends rows first and pages second, so every page seen here already
        # has its rows, and nothing iterates a dict that is changing size.
        ids_of = dict(self._ids)
        payloads = self._payloads
        vocab = self._vocab()
        keys = [k for k in ids_of
                if k in payloads and (not doc_ids or payloads[k].get("doc_id") in doc_ids)]
        if not keys:
            return []
        q = np.asarray(query_vectors, dtype=np.float32)
        if q.size == 0 or len(vocab) == 0:
            scores = np.zeros(len(keys))
        else:
            # Every page's rows in one gather, then each page's best match per
            # token with one reduceat: no Python-level work per page, which is
            # what keeps search flat as the index grows.
            lens = np.fromiter((len(ids_of[k]) for k in keys), dtype=np.int64, count=len(keys))
            best = np.zeros((len(keys), len(q)), dtype=np.float32)
            filled = lens > 0
            if filled.any():
                flat = np.concatenate([ids_of[k] for k in keys])
                starts = np.concatenate([[0], np.cumsum(lens)[:-1]])[filled]
                # The gather is (query tokens x every stored row), so take the
                # tokens a block at a time: a long question over a full shelf
                # would otherwise want gigabytes for it at once.
                step = max(1, _GATHER_CELLS // len(flat))
                for i in range(0, len(q), step):
                    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
                        sims = q[i:i + step] @ vocab.T          # (tokens, distinct rows)
                    sims = np.nan_to_num(sims, nan=0.0, posinf=1.0, neginf=-1.0)
                    best[filled, i:i + step] = np.maximum.reduceat(
                        sims[:, flat], starts, axis=1).T
            if weighting == "idf":
                # weight each token by how rare it is among the pages in scope
                with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
                    scores = best @ idf_weights(best)
            else:
                scores = best.sum(axis=1)
            scores = np.nan_to_num(scores, nan=0.0, posinf=0.0, neginf=0.0)
        order = np.argsort(-scores, kind="stable")[:top_k]
        return [Hit(key=keys[i], score=float(scores[i]), payload=payloads[keys[i]])
                for i in order]

    def count(self) -> int:
        return len(self._ids)

    # ------------------------------------------------------------- disk
    @property
    def _path(self) -> Path:
        return self.settings.index_dir / "memory_index"

    def save(self) -> None:
        self.settings.index_dir.mkdir(parents=True, exist_ok=True)
        keys = list(self._ids)
        lens = np.array([len(self._ids[k]) for k in keys], dtype=np.int64)
        flat = (np.concatenate([self._ids[k] for k in keys]) if keys
                else np.zeros(0, dtype=np.int32))
        np.savez_compressed(str(self._path) + ".npz", __vocab__=self._vocab(),
                            __ids__=flat, __lens__=lens)
        (self._path.with_suffix(".json")).write_text(
            json.dumps({"__keys__": keys, "payloads": self._payloads}), encoding="utf-8")

    def load(self) -> None:
        npz = Path(str(self._path) + ".npz")
        js = self._path.with_suffix(".json")
        if not (npz.exists() and js.exists()):
            return
        data = np.load(npz)
        meta = json.loads(js.read_text(encoding="utf-8"))
        self._ids, self._payloads, self._table, self._n, self._row_of = {}, {}, None, 0, {}
        if "__vocab__" in data.files:                       # shared-row format
            vocab = data["__vocab__"]
            if self.dim and vocab.size and vocab.shape[1] != self.dim:
                return                                      # another width: rebuild
            if len(vocab):
                self._append(vocab.astype(np.float32))
                self._row_of = {hash(r.tobytes()): i for i, r in enumerate(self._vocab())}
            flat, lens = data["__ids__"], data["__lens__"]
            offs = np.concatenate([[0], np.cumsum(lens)])
            for i, k in enumerate(meta["__keys__"]):
                self._ids[k] = flat[offs[i]: offs[i + 1]].astype(np.int32)
            self._payloads = meta["payloads"]
            return
        # The earlier one-matrix-per-page format: fold it into shared rows.
        mats = {k: data[k] for k in data.files}
        if self.dim and any(m.ndim != 2 or m.shape[1] != self.dim for m in mats.values()):
            return                                          # another width: rebuild
        payloads = meta if "payloads" not in meta else meta["payloads"]
        for k, m in mats.items():
            self.upsert(k, m, payloads.get(k, {}))


class QdrantStore:
    """Production multi-vector store (native MaxSim)."""

    def __init__(self, settings: Optional[Settings] = None, dim: int = 128):
        from qdrant_client import QdrantClient  # lazy
        from qdrant_client import models as qm

        self.s = settings or get_settings()
        self.qm = qm
        self.collection = "insightledger_pages"
        self.client = QdrantClient(url=self.s.qdrant_url,
                                   api_key=self.s.qdrant_api_key or None)
        if not self.client.collection_exists(self.collection):
            self.client.create_collection(
                self.collection,
                vectors_config=qm.VectorParams(
                    size=dim,
                    distance=qm.Distance.COSINE,
                    multivector_config=qm.MultiVectorConfig(
                        comparator=qm.MultiVectorComparator.MAX_SIM),
                ),
            )
        self._id = 0

    def upsert(self, key: str, vectors: np.ndarray, payload: dict[str, Any]) -> None:
        self._id += 1
        self.client.upsert(self.collection, points=[self.qm.PointStruct(
            id=self._id, vector=vectors.tolist(), payload={**payload, "key": key})])

    def search(self, query_vectors: np.ndarray, top_k: int,
               doc_ids: Optional[list[str]] = None,
               weighting: str = "none") -> list[Hit]:
        # Qdrant runs MaxSim natively; `weighting` applies to the in-memory
        # store's lexical stub vectors only.
        flt = None
        if doc_ids:
            flt = self.qm.Filter(must=[self.qm.FieldCondition(
                key="doc_id", match=self.qm.MatchAny(any=doc_ids))])
        res = self.client.query_points(
            self.collection, query=query_vectors.tolist(),
            limit=top_k, query_filter=flt, with_payload=True).points
        return [Hit(key=p.payload.get("key", str(p.id)), score=p.score,
                    payload=p.payload) for p in res]

    def count(self) -> int:
        return self.client.count(self.collection).count

    def save(self) -> None:  # qdrant persists itself
        return None

    def load(self) -> None:
        return None


def get_store(settings: Optional[Settings] = None, dim: int = 128) -> VectorStore:
    s = settings or get_settings()
    if s.resolve_vector_store() == "qdrant":
        try:
            return QdrantStore(s, dim=dim)
        except Exception:
            pass
    store = MemoryStore(settings=s, dim=dim)
    store.load()
    return store
