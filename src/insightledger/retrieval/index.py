"""Index builder: Document -> per-page multi-vector embeddings -> vector store."""
from __future__ import annotations

from typing import Iterable, Optional

from ..config import Settings, get_settings
from ..observability.tracing import Tracer
from ..schemas import Document
from .embedder import Embedder, get_embedder
from .store import VectorStore, get_store


class Indexer:
    def __init__(self, embedder: Optional[Embedder] = None,
                 store: Optional[VectorStore] = None,
                 settings: Optional[Settings] = None):
        self.s = settings or get_settings()
        self.embedder = embedder or get_embedder(self.s)
        self.store = store or get_store(self.s, dim=getattr(self.embedder, "dim", 128),
                                         scheme=getattr(self.embedder, "scheme", ""))

    @staticmethod
    def _key(doc_id: str, page_number: int) -> str:
        return f"{doc_id}::p{page_number}"

    def index_document(self, doc: Document, tracer: Optional[Tracer] = None) -> int:
        n = 0
        for page in doc.pages:
            vecs = self.embedder.embed_page(page)
            payload = {
                "doc_id": doc.doc_id,
                "page_number": page.page_number,
                "image_path": page.image_path,
                "text": page.text,
                "title": doc.title,
            }
            self.store.upsert(self._key(doc.doc_id, page.page_number), vecs, payload)
            n += 1
        if tracer:
            tracer.event("indexed", doc_id=doc.doc_id, pages=n)
        return n

    def index_documents(self, docs: Iterable[Document]) -> int:
        total = sum(self.index_document(d) for d in docs)
        self.store.save()
        return total
