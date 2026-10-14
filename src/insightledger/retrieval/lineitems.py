"""Line-item re-ranking for financial filings.

Most questions about a 10-K are about a line item: a label followed by its
figures, as in "Net income $ 58,471 $ 49,552". Bag-of-words retrieval cannot
see that structure. Inside JPMorgan's 172-page filing, "net" and "income"
appear on most pages, so the income statement ranked 38th for "how much net
income did JPMorgan report?" and the reader never saw it.

This pass looks for the question's own phrases printed as line items and lifts
the pages that carry them. It is deterministic and costs a few milliseconds
over the candidates the lexical pass already found.
"""
from __future__ import annotations

import re
from functools import lru_cache

# Words that end a phrase. Connectives that live *inside* line-item labels
# ("research and development", "cost of revenue") are deliberately absent.
_BREAK = {
    "how", "much", "many", "what", "which", "was", "were", "is", "are", "did", "does",
    "do", "the", "a", "an", "in", "on", "for", "to", "by", "at", "from", "its", "it",
    "as", "be", "this", "that", "with", "report", "reported", "spend", "spent",
    "fiscal", "year", "years", "total", "company", "company's", "s", "during",
}
_INNER = {"and", "of"}
_CATEGORY = {"revenue", "revenues", "sales", "income", "expense", "expenses", "cost", "costs"}
_MODIFIER = {"net", "total", "gross", "operating", "other", "cost", "adjusted",
             "diluted", "basic", "comprehensive"}
_WORD = re.compile(r"[a-z][a-z0-9&'\-]*")
# a comma-grouped figure, optionally preceded by $ or an opening parenthesis
_FIGURE = r"[\s$(]*\d{1,3}(?:,\d{3})+"


def question_phrases(question: str, entity_terms: frozenset = frozenset()) -> list[str]:
    """Runs of content words from the question, longest first.

    "How much did Tesla spend on research and development?" yields
    ["research and development"]; company names are dropped so they do not
    glue themselves onto a label.
    """
    words = _WORD.findall(question.lower().replace("\u2019", "'"))
    phrases, run = [], []
    for w in words + ["."]:
        # A possessive names the subject, not the label: "Meta's advertising
        # revenue" asks about Meta, and the line item is "Advertising".
        possessive = w.endswith("'s")
        if w in _BREAK or w in entity_terms or w == "." or possessive:
            while run and run[-1] in _INNER:
                run.pop()
            if run:
                phrases.append(" ".join(run))
            run = []
        elif w in _INNER:
            if run:
                run.append(w)
        else:
            run.append(w)
    # a lone word is only a label when nothing longer was asked
    multi = [p for p in phrases if " " in p]
    out = list(multi or phrases)
    # Statements often print the label without its category noun: Meta's
    # "advertising revenue" is the line "Advertising $ 162,345" under a
    # Revenue heading. Try the phrase with that noun dropped as well.
    for p in list(out):
        head, _, last = p.rpartition(" ")
        head_words = head.split()
        while head_words and head_words[-1] in _INNER:
            head_words.pop()          # "cost of revenue" -> "cost", not "cost of"
        # what remains must be a label, not a bare modifier: "net" from "net
        # sales" would match prose like "net of tax 1,234"
        if last in _CATEGORY and head_words and " ".join(head_words) not in _MODIFIER:
            out.append(" ".join(head_words))
    return sorted(set(out), key=lambda p: (-len(p.split()), p))


@lru_cache(maxsize=256)
def _pattern(phrase: str) -> re.Pattern:
    words = [re.escape(w) for w in phrase.split()]
    # the label, flexible about spacing, then up to 30 non-figure characters
    # (a footnote marker, "(1)", a currency word) before the first figure
    label = r"\s+".join(words)
    return re.compile(rf"\b{label}\b[^\d.\n]{{0,30}}?{_FIGURE}", re.IGNORECASE)


def line_item_hits(text: str, phrases: list[str]) -> int:
    """How many of the phrases this page prints as a line item."""
    if not text or not phrases:
        return 0
    return sum(1 for p in phrases if _pattern(p).search(text))
