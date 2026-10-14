"""A filing is paginated whole: nothing but its table of contents is dropped.

Pagination used to keep only the longest stretch of text per item number. That
lost the cover page of every filing, all of Intel's and GE's reports (they
index their items at the back), and whatever followed a cross-reference:
JPMorgan's filing kept 56% of its text.
"""
import re

from insightledger.ingestion.edgar import EdgarClient

PARA = "Revenue for the year was $79.0 billion, up 15% from a year ago, driven by Data Center. "


def _squash(t):
    return re.sub(r"\s+", "", t)


def test_a_report_whose_items_are_indexed_at_the_back_is_kept_whole():
    body = "\n".join(f"Fundamentals of our business, part {i}. " + PARA * 6 for i in range(30))
    back = "\n".join(f"Item {n}. Cross-reference index entry" for n in ("1", "1A", "7", "8"))
    text = "INTEL CORPORATION\nAnnual report\n" + body + "\n" + back + "\nItem 16. Form 10-K Summary None\nSignatures\n" + "Signed. " * 50
    pages = EdgarClient._paginate(text)
    kept = _squash("".join(pages))
    assert _squash(body) in kept, "the report before the item index was dropped"


def test_a_cross_reference_in_running_prose_does_not_cut_a_section():
    mdna = ("Item 7. Management's Discussion and Analysis\n"
            + PARA * 10 + "as discussed in Item 8. Financial Statements, " + PARA * 30)
    statements = "Item 8. Financial Statements\n" + "Net income $ 58,471 $ 29,760\n" * 40
    pages = EdgarClient._paginate("Cover page of the annual report. " * 20 + "\n" + mdna + "\n" + statements)
    kept = _squash("".join(pages))
    assert _squash(mdna) in kept and _squash(statements) in kept


def test_the_table_of_contents_is_the_only_thing_dropped():
    toc = "\n".join(f"Item {n}. {t} {p}" for n, t, p in
                    [("1", "Business", 4), ("1A", "Risk Factors", 12), ("7", "MD&A", 30)])
    cover = "NVIDIA CORPORATION\nShares outstanding 24,300,000,000 as of February 2026. " * 8
    sections = "\n".join(f"Item {n}. {t}\n" + PARA * 8 for n, t in
                         [("1", "Business"), ("1A", "Risk Factors"), ("7", "MD&A")])
    pages = EdgarClient._paginate(cover + "\n" + toc + "\n" + sections)
    joined = "\n".join(pages)
    assert "Shares outstanding" in joined, "the cover page was dropped"
    assert "Risk Factors 12" not in joined, "the table of contents was kept"
    assert all(len(p) <= 4000 for p in pages)


def test_pages_break_between_lines_so_a_table_row_stays_whole():
    rows = "\n".join(f"Net income attributable to segment {i} $ {i},471 $ {i},760" for i in range(400))
    pages = EdgarClient._paginate("Item 8. Financial Statements\n" + rows)
    for p in pages:
        for line in p.split("\n")[1:]:
            assert re.fullmatch(r"Net income attributable to segment \d+ \$ \d+,471 \$ \d+,760", line)
