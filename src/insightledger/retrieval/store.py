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


@dataclass
class MemoryStore:
    settings: Settings = field(default_factory=get_settings)
    _vectors: dict[str, np.ndarray] = field(default_factory=dict)
    _payloads: dict[str, dict[str, Any]] = field(default_factory=dict)

    def upsert(self, key: str, vectors: np.ndarray, payload: dict[str, Any]) -> None:
        self._vectors[key] = vectors.astype(np.float32)
        self._payloads[key] = payload

    def search(self, query_vectors: np.ndarray, top_k: int,
               doc_ids: Optional[list[str]] = None,
               weighting: str = "none") -> list[Hit]:
        keys = [k for k, _ in self._vectors.items()
                if not doc_ids or self._payloads[k].get("doc_id") in doc_ids]
        if not keys:
            return []
        if weighting == "idf" and query_vectors.size:
            # (pages, query tokens): weight each token by how rare it is among
            # the pages in scope, so scoping to one filing re-weights correctly
            best = np.stack([best_per_token(query_vectors, self._vectors[k]) for k in keys])
            with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
                scores = best @ idf_weights(best)   # same spurious macOS flag
            scores = np.nan_to_num(scores, nan=0.0, posinf=0.0, neginf=0.0)
        else:
            scores = np.array([maxsim(query_vectors, self._vectors[k]) for k in keys])
        order = np.argsort(-scores, kind="stable")[:top_k]
        return [Hit(key=keys[i], score=float(scores[i]), payload=self._payloads[keys[i]])
                for i in order]

    def count(self) -> int:
        return len(self._vectors)

    @property
    def _path(self) -> Path:
        return self.settings.index_dir / "memory_index"

    def save(self) -> None:
        self.settings.index_dir.mkdir(parents=True, exist_ok=True)
        np.savez_compressed(str(self._path) + ".npz", **self._vectors)
        (self._path.with_suffix(".json")).write_text(
            json.dumps(self._payloads), encoding="utf-8")

    def load(self) -> None:
        npz = Path(str(self._path) + ".npz")
        js = self._path.with_suffix(".json")
        if npz.exists() and js.exists():
            data = np.load(npz)
            self._vectors = {k: data[k] for k in data.files}
            self._payloads = json.loads(js.read_text(encoding="utf-8"))


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
    store = MemoryStore(settings=s)
    store.load()
    return store
