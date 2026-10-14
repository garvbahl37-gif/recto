"""A question that names a company should read that company's filing.

Every 10-K prints a "Net income" line item, so with all thirteen filings in
scope, "How much net income did JPMorgan report?" pulled pages from Visa,
Netflix and Alphabet before JPMorgan's own. The router now scopes to the
filings of any company the question names.
"""
from insightledger.agents.entities import companies_in
from insightledger.ingestion.pipeline import build_document_from_pages


def _doc(doc_id, name):
    d = build_document_from_pages(doc_id, f"{name} 10-K (2026)", ["text"], source="t://", doc_type="10-K")
    d.metadata["name"] = name
    return d


REG = {d.doc_id: d for d in [
    _doc("JPM-10-K-2026-02-13", "JPMORGAN CHASE & CO"),
    _doc("META-10-K-2026-01-29", "Meta Platforms, Inc."),
    _doc("LLY-10-K-2026-02-12", "ELI LILLY & Co"),
    _doc("V-10-K-2025-11-06", "VISA INC."),
    _doc("GOOGL-10-K-2026-02-05", "Alphabet Inc."),
    _doc("BRK-B-10-K-2026-02-20", "BERKSHIRE HATHAWAY INC"),
]}


def test_names_and_possessives_scope_to_the_company():
    assert companies_in("How much net income did JPMorgan report?", REG) == ["JPM-10-K-2026-02-13"]
    assert companies_in("What was Meta's advertising revenue?", REG) == ["META-10-K-2026-01-29"]


def test_common_aliases_resolve():
    assert companies_in("What did Google spend on capex?", REG) == ["GOOGL-10-K-2026-02-05"]
    assert companies_in("How did Lilly's revenue change?", REG) == ["LLY-10-K-2026-02-12"]


def test_tickers_match_only_when_written_as_tickers():
    assert companies_in("Compare BRK-B and JPM on net income", REG) == [
        "BRK-B-10-K-2026-02-20", "JPM-10-K-2026-02-13"]
    # a lone capital V inside ordinary prose is not Visa
    assert companies_in("What is the value of goodwill?", REG) == []


def test_two_companies_named_keeps_both():
    assert companies_in("Compare Visa and Meta revenue growth", REG) == [
        "META-10-K-2026-01-29", "V-10-K-2025-11-06"]


def test_no_company_named_leaves_scope_open():
    assert companies_in("Which company reported the most revenue?", REG) == []


def test_router_reads_only_the_named_company():
    from insightledger.agents import Orchestrator
    from insightledger.knowledge import ensure_sample_index
    ensure_sample_index(force=True)
    seen = []
    ans = Orchestrator().answer("What was ACME's total revenue in fiscal 2024?",
                                emit=lambda stage, p: seen.append((stage, p)))
    router = dict(seen)["router"]
    assert router["scoped"] == ["ACME-10K-2024"]
    assert {p.doc_id for p in ans.retrieved} == {"ACME-10K-2024"}
