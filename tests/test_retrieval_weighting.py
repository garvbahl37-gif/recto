"""Rare query terms must outweigh common ones in retrieval.

Plain MaxSim sums every query token's best match equally, so "how", "much",
"did" and "report" counted as much as "net" and "income". Asking JPMorgan's
172-page 10-K for net income retrieved five pages that never print it, and the
pipeline abstained on a question its own filing answers.
"""
from dataclasses import replace

from insightledger.config import get_settings
from insightledger.retrieval import Retriever
from insightledger.retrieval.embedder import StubEmbedder
from insightledger.retrieval.store import MemoryStore
from insightledger.schemas import Page, Query


def _retriever(pages):
    s = replace(get_settings(), vector_store="memory")
    emb, store = StubEmbedder(), MemoryStore(settings=s)
    for p in pages:
        store.upsert(f"{p.doc_id}::{p.page_number}", emb.embed_page(p),
                     {"doc_id": p.doc_id, "page_number": p.page_number, "text": p.text})
    return Retriever(embedder=emb, store=store, settings=s)


def test_rare_terms_beat_common_ones():
    filler = "how much did the bank report this year and how did it report"
    pages = [Page(doc_id="JPM-10-K", page_number=i, text=filler, regions=[]) for i in range(1, 9)]
    pages.append(Page(doc_id="JPM-10-K", page_number=99, regions=[],
                      text="Net income was $58,471 million for the year."))
    top = _retriever(pages).retrieve(Query(text="How much net income did JPMorgan report?", top_k=3))
    assert top[0].page_number == 99


def test_scope_filter_still_applies():
    pages = [Page(doc_id="A-10-K", page_number=1, regions=[], text="net income was high"),
             Page(doc_id="B-10-K", page_number=1, regions=[], text="net income was low")]
    top = _retriever(pages).retrieve(Query(text="net income", top_k=5, doc_ids=["B-10-K"]))
    assert {p.doc_id for p in top} == {"B-10-K"}


def test_a_line_item_with_its_figure_outranks_scattered_mentions():
    """Financial statements are line items: a phrase followed by its figure.
    A page that prints 'Net income $ 58,471' answers 'how much net income'; a
    page that merely discusses net income several times does not."""
    talk = ("Net income is discussed throughout. Our net interest income and the "
            "income from net investments rose, and net income trends are reviewed.")
    pages = [Page(doc_id="JPM-10-K", page_number=i, text=talk, regions=[]) for i in range(1, 12)]
    pages.append(Page(doc_id="JPM-10-K", page_number=139, regions=[],
                      text="Consolidated statements of income. Net income $ 58,471 $ 49,552."))
    top = _retriever(pages).retrieve(Query(text="How much net income did JPMorgan report?", top_k=3))
    assert top[0].page_number == 139


def test_line_items_spanning_connectives_are_recognised():
    talk = "Research spending and development plans are discussed in general terms."
    pages = [Page(doc_id="TSLA-10-K", page_number=i, text=talk, regions=[]) for i in range(1, 10)]
    pages.append(Page(doc_id="TSLA-10-K", page_number=57, regions=[],
                      text="Research and development 6,411 4,540 3,969"))
    top = _retriever(pages).retrieve(
        Query(text="How much did Tesla spend on research and development?", top_k=3))
    assert top[0].page_number == 57


def test_company_names_do_not_glue_onto_labels():
    from insightledger.retrieval.lineitems import question_phrases
    assert "advertising" in question_phrases("What was Meta's advertising revenue?")
    assert question_phrases("What was Apple's total net sales?") == ["net sales"]
    assert question_phrases("How much did Tesla spend on research and development?")[0] \
        == "research and development"


def test_a_line_item_matches_whether_the_question_says_singular_or_plural():
    from insightledger.retrieval.lineitems import line_item_hits
    assert line_item_hits("Total revenue $ 45,855 $ 38,702", ["revenues"]) == 1
    assert line_item_hits("Total revenues $ 45,855", ["revenue"]) == 1
    assert line_item_hits("Total liabilities 4,000,123", ["liability"]) == 1
    assert line_item_hits("Net losses (1,234)", ["net loss"]) == 1
    assert line_item_hits("Research and development 12,345", ["research and development"]) == 1
    assert line_item_hits("Revenues grew in 2025", ["revenue"]) == 0, "a label still needs its figure"
