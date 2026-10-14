"""Eval harness. Runs the full agentic pipeline over the golden set, scores
each item, aggregates, and (optionally) enforces regression thresholds — the
gate you wire into CI."""
from __future__ import annotations

import json
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Optional

from ..agents import Orchestrator
from ..config import Settings, get_settings
from ..knowledge import ensure_sample_index
from ..schemas import EvalItem, EvalReport
from .golden_set import load_golden
from .metrics import aggregate, score_item


def eval_settings(s: Optional[Settings] = None) -> Settings:
    """Isolate eval into its own index namespace so the labelled synthetic
    fixture never collides with the app's live EDGAR index."""
    s = s or get_settings()
    return replace(s, data_dir=s.data_dir / "_eval", bootstrap="sample")

# CI regression gate — tune to your baseline. The pipeline must clear these.
DEFAULT_THRESHOLDS = {
    "recall_at_k": 0.85,
    "faithfulness": 0.85,
    "answer_accuracy": 0.75,
    "hallucination_rate_max": 0.10,
    "mean_citation_iou": 0.50,
}


@dataclass
class GateOutcome:
    passed: bool
    failures: list[str]


def check_gate(report: EvalReport,
               thresholds: Optional[dict] = None) -> GateOutcome:
    t = {**DEFAULT_THRESHOLDS, **(thresholds or {})}
    failures: list[str] = []
    if report.recall_at_k < t["recall_at_k"]:
        failures.append(f"recall_at_k {report.recall_at_k:.2f} < {t['recall_at_k']}")
    if report.faithfulness < t["faithfulness"]:
        failures.append(f"faithfulness {report.faithfulness:.2f} < {t['faithfulness']}")
    if report.answer_accuracy < t["answer_accuracy"]:
        failures.append(f"answer_accuracy {report.answer_accuracy:.2f} < {t['answer_accuracy']}")
    if report.hallucination_rate > t["hallucination_rate_max"]:
        failures.append(
            f"hallucination_rate {report.hallucination_rate:.2f} > {t['hallucination_rate_max']}")
    if report.mean_citation_iou < t["mean_citation_iou"]:
        failures.append(
            f"mean_citation_iou {report.mean_citation_iou:.2f} < {t['mean_citation_iou']}")
    return GateOutcome(passed=not failures, failures=failures)


def run_eval(items: Optional[list[EvalItem]] = None,
             settings: Optional[Settings] = None,
             save: bool = True) -> EvalReport:
    s = eval_settings(settings)
    ensure_sample_index(settings=s, force=True)
    orch = Orchestrator(settings=s)
    items = items or load_golden(s=s)
    results = []
    for it in items:
        ans = orch.answer(it.question, doc_ids=it.doc_ids or None)
        results.append(score_item(it, ans))
    report = aggregate(results, backends=s.backend_report())
    if save:
        out = s.data_dir / "eval_report.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(report.model_dump_json(indent=2), encoding="utf-8")
    return report


def format_report(report: EvalReport, gate: Optional[GateOutcome] = None) -> str:
    lines = [
        "=" * 60,
        "Recto — Eval Report",
        "=" * 60,
        f"backends        : {json.dumps(report.backends)}",
        f"items           : {report.n}",
        f"recall@k        : {report.recall_at_k:.3f}",
        f"faithfulness    : {report.faithfulness:.3f}",
        f"answer_accuracy : {report.answer_accuracy:.3f}",
        f"citation_iou    : {report.mean_citation_iou:.3f}",
        f"hallucination   : {report.hallucination_rate:.3f}",
        f"latency p50/p95 : {report.p50_latency_ms:.1f} / {report.p95_latency_ms:.1f} ms",
        f"cost mean/total : ${report.mean_cost_usd:.6f} / ${report.total_cost_usd:.6f}",
    ]
    if gate is not None:
        lines.append("-" * 60)
        lines.append("GATE: " + ("PASS [OK]" if gate.passed else "FAIL [X]"))
        for f in gate.failures:
            lines.append(f"  - {f}")
    lines.append("=" * 60)
    return "\n".join(lines)
