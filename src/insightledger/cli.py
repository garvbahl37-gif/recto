"""Recto CLI.

    insightledger backends                 show resolved backends
    insightledger bootstrap                build + index the sample corpus
    insightledger ingest --ticker AAPL     ingest a live SEC filing (needs network)
    insightledger query "..."              ask a question, print grounded answer
    insightledger eval [--gate]            run the eval harness (optional CI gate)
    insightledger serve [--port 8000]      run the FastAPI service
"""
from __future__ import annotations

import argparse
import json
import sys

from .config import get_settings


def _cmd_backends(_args) -> int:
    print(json.dumps(get_settings().backend_report(), indent=2))
    return 0


def _bootstrap_index(force: bool = False):
    from .knowledge import ensure_live_index, ensure_sample_index

    s = get_settings()
    return (ensure_sample_index(force=force) if s.bootstrap == "sample"
            else ensure_live_index(force=force))


def _cmd_bootstrap(_args) -> int:
    s = get_settings()
    print(f"bootstrap mode: {s.bootstrap} "
          f"(tickers: {s.seed_tickers if s.bootstrap != 'sample' else 'synthetic fixture'})")
    r = _bootstrap_index(force=True)
    print(f"indexed {r.store.count()} pages -> {s.index_dir}")
    return 0


def _cmd_ingest(args) -> int:
    from .ingestion import EdgarClient
    from .knowledge import load_registry, save_registry
    from .retrieval import Indexer

    doc = EdgarClient().fetch(args.ticker, form=args.form)
    idx = Indexer()
    n = idx.index_documents([doc])
    reg = load_registry()
    reg[doc.doc_id] = doc
    save_registry(list(reg.values()))
    print(f"ingested {doc.doc_id}: {len(doc.pages)} pages, indexed {n}")
    return 0


def _cmd_query(args) -> int:
    from .agents import answer_query

    _bootstrap_index()
    ans = answer_query(args.question, doc_ids=args.doc_ids)
    print("\nANSWER:\n" + ans.text)
    print("\nCITATIONS:")
    for c in ans.citations:
        loc = f" bbox={c.bbox.model_dump()}" if c.bbox else ""
        print(f"  - {c.doc_id} p.{c.page_number}{loc}")
    print(f"\ndifficulty={ans.difficulty.value} confidence={ans.confidence:.2f} "
          f"model={ans.model_used} retries={ans.retries} "
          f"cost=${ans.cost_usd:.6f} latency={ans.latency_ms:.0f}ms trace={ans.trace_id}")
    return 0


def _cmd_eval(args) -> int:
    from .eval.harness import check_gate, format_report, run_eval

    report = run_eval()
    gate = check_gate(report) if args.gate else None
    print(format_report(report, gate))
    if gate is not None and not gate.passed:
        return 1
    return 0


def _cmd_qagen(args) -> int:
    from .eval.qagen import generate_for_document, write_generated
    from .knowledge import ingest_ticker, load_registry

    s = get_settings()
    reg = load_registry()
    doc = next((d for d in reg.values()
                if d.doc_id.split("-")[0] == args.ticker.upper()), None)
    if doc is None:
        print(f"ingesting {args.ticker} from EDGAR…")
        doc = ingest_ticker(args.ticker.upper(), form=args.form)
    print(f"generating QA from {doc.doc_id} using LLM '{s.resolve_llm()}'…")
    items = generate_for_document(doc, per_page=args.per_page, max_pages=args.max_pages)
    path = write_generated(items)
    print(f"generated {len(items)} grounded QA pairs -> {path}")
    for it in items[:5]:
        print(f"  Q: {it.question}\n     a~ '{it.answer_contains[0]}' (p.{it.gold_pages})")
    return 0


def _cmd_compare(_args) -> int:
    from .eval.baseline import compare, format_compare

    cmp = compare()
    print(format_compare(cmp))
    return 0


def _cmd_serve(args) -> int:
    import uvicorn

    uvicorn.run("insightledger.api.main:app", host=args.host, port=args.port,
                reload=args.reload)
    return 0


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="insightledger", description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    sub.add_parser("backends").set_defaults(func=_cmd_backends)
    sub.add_parser("bootstrap").set_defaults(func=_cmd_bootstrap)

    pi = sub.add_parser("ingest")
    pi.add_argument("--ticker", required=True)
    pi.add_argument("--form", default="10-K")
    pi.set_defaults(func=_cmd_ingest)

    pq = sub.add_parser("query")
    pq.add_argument("question")
    pq.add_argument("--doc-ids", dest="doc_ids", nargs="*", default=None)
    pq.set_defaults(func=_cmd_query)

    pe = sub.add_parser("eval")
    pe.add_argument("--gate", action="store_true", help="fail (exit 1) if below thresholds")
    pe.set_defaults(func=_cmd_eval)

    pg = sub.add_parser("qagen", help="LLM-generate an eval QA set from a live filing")
    pg.add_argument("--ticker", required=True)
    pg.add_argument("--form", default="10-K")
    pg.add_argument("--per-page", dest="per_page", type=int, default=1)
    pg.add_argument("--max-pages", dest="max_pages", type=int, default=3,
                    help="pages to generate from (1 hosted call each)")
    pg.set_defaults(func=_cmd_qagen)

    sub.add_parser("compare").set_defaults(func=_cmd_compare)

    ps = sub.add_parser("serve")
    ps.add_argument("--host", default="127.0.0.1")
    ps.add_argument("--port", type=int, default=8000)
    ps.add_argument("--reload", action="store_true")
    ps.set_defaults(func=_cmd_serve)

    args = p.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
