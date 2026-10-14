from insightledger.agents import Orchestrator
from insightledger.knowledge import ensure_sample_index
from insightledger.schemas import Difficulty


def _orch():
    ensure_sample_index(force=True)
    return Orchestrator()


def test_grounded_answer_has_region_citation():
    ans = _orch().answer(
        "Which segment drove ACME's operating margin improvement and by how much did it grow?",
        doc_ids=["ACME-10K-2024"])
    assert "Cloud" in ans.text and "42%" in ans.text
    assert ans.citations and any(c.bbox is not None for c in ans.citations)
    assert any(c.verified for c in ans.claims)


def test_router_flags_complex_reasoning():
    o = _orch()
    hard = o.provider.assess_difficulty(
        "How did operating margin change year over year and which segment drove it?")
    easy = o.provider.assess_difficulty("What was total revenue in 2024?")
    assert hard == Difficulty.complex
    assert easy == Difficulty.simple


def test_abstains_when_answer_absent():
    ans = _orch().answer("What was ACME's CEO total compensation in 2024?",
                         doc_ids=["ACME-10K-2024"])
    assert ans.citations == []
    assert "could not find" in ans.text.lower()


def test_lookup_uses_cheap_routing_signal():
    ans = _orch().answer("What was ACME's total revenue in fiscal 2024?",
                         doc_ids=["ACME-10K-2024"])
    assert "4,820" in ans.text
    assert ans.difficulty == Difficulty.simple


def test_a_retry_reads_only_pages_it_has_not_read():
    """Widening retrieval used to re-read every page, including the ones the
    first pass had just read, roughly doubling the time an abstention took."""
    from insightledger.observability.cost import CostMeter
    from insightledger.llm import StubProvider

    seen = []

    class Spy(StubProvider):
        def extract_claims(self, question, pages, meter):
            seen.append([(p.doc_id, p.page_number) for p in pages])
            return []                      # nothing found: forces the retry

    ensure_sample_index(force=True)
    Orchestrator(provider=Spy()).answer("What was ACME's CEO total compensation in 2024?",
                                        doc_ids=["ACME-10K-2024"])
    assert len(seen) == 2, seen
    assert not set(seen[0]) & set(seen[1]), "the retry re-read pages it had already read"


def test_langgraph_retries_once_without_hitting_the_recursion_limit():
    """decide() used to bump the retry counter inside a conditional edge.
    LangGraph does not persist state changes made in an edge, so the counter
    never moved: every low-confidence question looped until the recursion
    limit, then crashed into the linear fallback and started over."""
    from insightledger.observability.cost import CostMeter
    from insightledger.observability.tracing import get_tracer
    ensure_sample_index(force=True)
    o = _orch()
    st = o._initial_state("What was ACME's CEO total compensation in 2024?", None,
                          ["ACME-10K-2024"], CostMeter(), get_tracer())
    out = o._run_langgraph(st)          # must not raise GraphRecursionError
    assert out["retries"] == 1
