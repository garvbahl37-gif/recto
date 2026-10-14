"""Shared graph state. A TypedDict so it works identically under LangGraph and
the linear fallback runner."""
from __future__ import annotations

from typing import Any, Optional, TypedDict

from ..schemas import Citation, Claim, Difficulty, RetrievedPage


class ILState(TypedDict, total=False):
    # inputs
    question: str
    top_k: int
    doc_ids: Optional[list[str]]
    max_retries: int
    min_confidence: float

    # working state
    difficulty: Difficulty
    retrieved: list[RetrievedPage]
    claims: list[Claim]
    verified_confidence: float
    retries: int

    # outputs
    answer_text: str
    citations: list[Citation]
    model_used: str

    # bookkeeping (passed through the graph)
    meter: Any        # CostMeter
    tracer: Any       # Tracer
    log: list[str]
    # optional progress callback: emit(stage, payload). Declared here because
    # LangGraph only carries keys that are part of the state schema.
    emit: Any
