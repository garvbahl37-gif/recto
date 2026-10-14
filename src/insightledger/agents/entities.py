"""Which indexed companies a question is about.

Every annual report prints the same line items — revenue, net income, cash —
so a question that names a company has to read that company's filing, or the
retriever happily returns the right line item from the wrong company. Names
are matched on word boundaries; tickers only when written as tickers (in
capitals), so a stray capital "V" in prose never means Visa.
"""
from __future__ import annotations

import re

# What people call a company, when it is not its legal name.
ALIASES = {
    "google": "GOOGL", "facebook": "META", "instagram": "META", "lilly": "LLY",
    "j&j": "JNJ", "jp morgan": "JPM", "chase": "JPM", "berkshire": "BRK-B",
}
# Legal-name words too common to identify anyone.
_GENERIC = {"the", "first", "american", "international", "general", "united", "national",
            "global", "new", "group", "holdings", "corp", "corporation", "inc", "company",
            "co", "bank", "financial", "capital", "energy", "health", "systems", "technologies"}
_FORM = re.compile(r"-(10-K|10-Q|8-K|20-F|40-F|DEF 14A)-\d{4}-\d{2}-\d{2}$")


def ticker_of(doc_id: str) -> str:
    m = _FORM.search(doc_id)
    return doc_id[: m.start()] if m else doc_id.split("-")[0]


def _first_word(name: str) -> str:
    words = re.findall(r"[a-z0-9&]+", name.lower())
    return words[0] if words else ""


def companies_in(question: str, registry: dict) -> list[str]:
    """doc_ids of every indexed filing whose company the question names."""
    q = question.lower().replace("’", "'")
    hits: set[str] = set()
    for doc_id, doc in registry.items():
        tk = ticker_of(doc_id)
        name = (getattr(doc, "metadata", {}) or {}).get("name") or getattr(doc, "title", "")
        keys = {a for a, t in ALIASES.items() if t == tk}
        first = _first_word(name)
        if len(first) >= 4 and first not in _GENERIC:
            keys.add(first)
        if any(re.search(rf"(?<![\w&]){re.escape(k)}(?![\w&])", q) for k in keys):
            hits.add(doc_id)
        elif len(tk) >= 2 and re.search(rf"(?<![\w-]){re.escape(tk)}(?![\w-])", question):
            hits.add(doc_id)
    return sorted(hits)
