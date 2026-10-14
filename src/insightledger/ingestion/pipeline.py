"""Assemble a Document from raw page texts (+ optional rendered images)."""
from __future__ import annotations

from typing import Optional

from ..schemas import Document, Page


def build_document_from_pages(
    doc_id: str,
    title: str,
    page_texts: list[str],
    source: str = "",
    doc_type: str = "filing",
    image_paths: Optional[list[Optional[str]]] = None,
    metadata: Optional[dict] = None,
) -> Document:
    pages: list[Page] = []
    for i, text in enumerate(page_texts):
        img = image_paths[i] if image_paths and i < len(image_paths) else None
        pages.append(Page(
            doc_id=doc_id,
            page_number=i + 1,
            image_path=img,
            text=text,
        ))
    return Document(
        doc_id=doc_id, title=title, source=source, doc_type=doc_type,
        metadata=metadata or {}, pages=pages,
    )
