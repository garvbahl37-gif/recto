"""Domain schemas. Pydantic models shared across ingestion, retrieval, agents,
API and eval. These are the contracts — keep them stable."""
from __future__ import annotations

from enum import Enum
from typing import Any, Optional

from pydantic import BaseModel, Field, computed_field


class BBox(BaseModel):
    """Normalized bounding box (0..1) in page coordinates, origin top-left."""
    x0: float
    y0: float
    x1: float
    y1: float

    def iou(self, other: "BBox") -> float:
        ix0, iy0 = max(self.x0, other.x0), max(self.y0, other.y0)
        ix1, iy1 = min(self.x1, other.x1), min(self.y1, other.y1)
        iw, ih = max(0.0, ix1 - ix0), max(0.0, iy1 - iy0)
        inter = iw * ih
        a = max(0.0, self.x1 - self.x0) * max(0.0, self.y1 - self.y0)
        b = max(0.0, other.x1 - other.x0) * max(0.0, other.y1 - other.y0)
        union = a + b - inter
        return inter / union if union > 0 else 0.0


class RegionType(str, Enum):
    text = "text"
    table = "table"
    chart = "chart"
    figure = "figure"
    signature = "signature"
    stamp = "stamp"
    header = "header"


class Region(BaseModel):
    """A layout region on a page (from Object Detection / Segmentation task)."""
    region_id: str
    type: RegionType
    bbox: BBox
    text: str = ""          # OCR / extracted text if any
    confidence: float = 1.0


class Page(BaseModel):
    """One rendered page of a document."""
    doc_id: str
    page_number: int        # 1-indexed
    image_path: Optional[str] = None   # rendered page image (real backend)
    width: int = 1000
    height: int = 1400
    text: str = ""          # full-page extracted text (fallback / hybrid)
    section: str = ""       # e.g. "Item 1A. Risk Factors" (filing section)

    @computed_field
    @property
    def regions(self) -> list[Region]:
        """Layout blocks, worked out from the text whenever they are asked for.

        Every region is a slice of the page text, so storing them kept each
        page in memory several times over. They are still serialised for the
        API; a stored copy is ignored on load.
        """
        from .ingestion.layout import detect_regions
        return detect_regions(self.text)


class Document(BaseModel):
    doc_id: str
    title: str
    source: str = ""        # e.g. EDGAR URL
    doc_type: str = "filing"
    metadata: dict[str, Any] = Field(default_factory=dict)
    pages: list[Page] = Field(default_factory=list)


class RetrievedPage(BaseModel):
    doc_id: str
    page_number: int
    score: float
    image_path: Optional[str] = None
    text: str = ""
    regions: list[Region] = Field(default_factory=list)


class Citation(BaseModel):
    """Region-level provenance for an answer claim."""
    doc_id: str
    page_number: int
    bbox: Optional[BBox] = None
    region_id: Optional[str] = None
    snippet: str = ""


class Difficulty(str, Enum):
    simple = "simple"       # single-fact lookup -> cheap model
    complex = "complex"     # cross-page / chart+table reasoning -> strong model


class Query(BaseModel):
    text: str
    top_k: int = 5
    doc_ids: Optional[list[str]] = None   # restrict to specific documents


class Claim(BaseModel):
    """An atomic factual statement extracted from the draft answer, checked by
    the verifier against its citation."""
    text: str
    citations: list[Citation] = Field(default_factory=list)
    verified: Optional[bool] = None
    verifier_note: str = ""
    confidence: float = 0.0


class Answer(BaseModel):
    query: str
    text: str
    citations: list[Citation] = Field(default_factory=list)
    claims: list[Claim] = Field(default_factory=list)
    confidence: float = 0.0
    difficulty: Difficulty = Difficulty.simple
    retrieved: list[RetrievedPage] = Field(default_factory=list)
    # bookkeeping
    model_used: str = ""
    retries: int = 0
    trace_id: str = ""
    cost_usd: float = 0.0
    latency_ms: float = 0.0


class EvalItem(BaseModel):
    """One golden-set row."""
    id: str
    question: str
    doc_ids: list[str] = Field(default_factory=list)
    answer_contains: list[str] = Field(default_factory=list)   # substrings expected
    gold_pages: list[int] = Field(default_factory=list)        # expected page numbers
    gold_bbox: Optional[BBox] = None                           # expected citation region
    tags: list[str] = Field(default_factory=list)


class EvalResult(BaseModel):
    item_id: str
    recall_hit: bool                # gold page in retrieved top-k
    faithful: bool                  # answer grounded in a real citation
    answer_match: bool              # expected substrings present
    citation_iou: float             # best IoU vs gold_bbox (0 if n/a)
    hallucinated: bool
    latency_ms: float
    cost_usd: float
    model_used: str


class EvalReport(BaseModel):
    n: int
    recall_at_k: float
    faithfulness: float
    answer_accuracy: float
    mean_citation_iou: float
    hallucination_rate: float
    p50_latency_ms: float
    p95_latency_ms: float
    mean_cost_usd: float
    total_cost_usd: float
    results: list[EvalResult] = Field(default_factory=list)
    backends: dict[str, str] = Field(default_factory=dict)
