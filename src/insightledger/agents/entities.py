"""Which indexed companies a question is about.

Every annual report prints the same line items — revenue, net income, cash —
so a question that names a company has to read that company's filing, or the
retriever happily returns the right line item from the wrong company. Names
are matched on word boundaries; tickers only when written as tickers (in
capitals), so a stray capital "V" in prose never means Visa.
"""
from __future__ import annotations

import re

# What people call a company, when it is not its legal name. ("Coke" is left
# out on purpose: steelmakers' filings are full of coal coke.)
ALIASES = {
    "google": "GOOGL", "facebook": "META", "instagram": "META", "lilly": "LLY",
    "j&j": "JNJ", "jp morgan": "JPM", "chase": "JPM", "berkshire": "BRK-B",
    "bank of america": "BAC", "bofa": "BAC", "disney": "DIS", "mcdonald": "MCD",
    "pepsi": "PEP", "p&g": "PG", "general electric": "GE",
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


# ------------------------------------------------- companies not yet read --

# Finance acronyms that are also tickers (C3.ai is "AI", Gartner is "IT"):
# in a question about a filing they almost always mean the acronym.
_ACRONYMS = {
    "AI", "IT", "HR", "CEO", "CFO", "COO", "CTO", "CIO", "EPS", "GAAP", "SEC", "USA", "US",
    "UK", "EU", "IPO", "ESG", "EBIT", "EBITDA", "ROE", "ROI", "ROA", "FY", "YOY", "LLC",
    "INC", "ETF", "NYSE", "IRS", "FDA", "DOJ", "FTC", "API", "Q1", "Q2", "Q3", "Q4", "TV",
    "PC", "OK", "II", "III", "IV", "AND", "OR", "THE", "NA", "MD", "AGM", "CEOS", "R&D",
}
# Capitalised words that start a question or name a filing part, not a company.
_NOT_NAMES = {
    "what", "how", "which", "who", "why", "when", "where", "did", "does", "do", "is",
    "are", "was", "were", "can", "could", "should", "would", "will", "has", "have",
    "the", "a", "an", "in", "on", "for", "of", "and", "or", "to", "by", "with",
    "compare", "list", "show", "tell", "give", "describe", "summarize", "summarise",
    "explain", "find", "item", "items", "form", "part", "note", "notes", "risk", "risks",
    "factors", "total", "net", "revenue", "revenues", "income", "sales", "fiscal", "year",
    "quarter", "annual", "report", "q1", "q2", "q3", "q4", "january", "february", "march",
    "april", "may", "june", "july", "august", "september", "october", "november",
    "december", "management", "board", "company", "companies", "ceo", "cfo",
}
_SUFFIXES = {"inc", "incorporated", "corp", "corporation", "co", "company", "ltd", "limited",
             "plc", "holdings", "holding", "group", "the", "new", "del", "de", "sa", "nv",
             "ag", "se", "lp", "llc", "reit", "trust", "cl", "class", "a", "b"}
_PLACES = {
    "north", "south", "east", "west", "northern", "southern", "eastern", "western",
    "america", "americas", "american", "europe", "european", "asia", "asian", "africa",
    "china", "chinese", "japan", "india", "canada", "mexico", "brazil", "germany",
    "france", "british", "united", "latin", "pacific", "atlantic", "middle", "global",
    "world", "international", "national", "new", "great",
}
# Capitalised runs, allowed one lowercase connective inside ("Bank of
# America", "Procter & Gamble"); leading and trailing filler is trimmed later.
_RUN = re.compile(r"[A-Z][\w&'\u2019.\-]*(?:\s+(?:(?:of|and|&|the|de)\s+)?[A-Z][\w&'\u2019.\-]*)*")
_TICKER = re.compile(r"(?<![\w-])[A-Z][A-Z.\-]{0,5}[A-Z](?![\w-])")


def _words(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


def _core(title: str) -> list[str]:
    """'HOME DEPOT, INC.' -> ['home', 'depot']; 'COSTCO WHOLESALE CORP /NEW' ->
    ['costco', 'wholesale']."""
    ws = _words(title.split("/")[0])
    while ws and ws[-1] in _SUFFIXES:
        ws.pop()
    return ws


def edgar_companies_in(question: str, companies: list[dict], limit: int = 2) -> list[dict]:
    """EDGAR companies a question names, most explicit first.

    `companies` is EDGAR's ticker list ([{ticker, title, cik}]). Matches are a
    ticker written in capitals, or a capitalised name (possessive or not) equal
    to a company's name, or the distinctive first word of one. When several
    companies share a first word, the plainest name wins: "Apple" is Apple Inc.,
    not Apple Hospitality REIT.
    """
    found: list[dict] = []
    seen: set[str] = set()

    def add(c: dict) -> None:
        if c["ticker"] not in seen:
            seen.add(c["ticker"])
            found.append(c)

    by_ticker = {c["ticker"]: c for c in companies}
    for tok in _TICKER.findall(question):
        if tok in by_ticker and tok not in _ACRONYMS:
            add(by_ticker[tok])

    for run in _RUN.findall(question.replace("’", "'")):
        words = _words(re.sub(r"'s\b", "", run))
        while words and words[0] in _NOT_NAMES:
            words = words[1:]
        while words and words[-1] in _NOT_NAMES:
            words.pop()
        if not words or (len(words) == 1 and (len(words[0]) < 4 or words[0] in _GENERIC)):
            continue
        exact, prefix = [], []
        for c in companies:
            core = _core(c["title"])
            if core == words:
                exact.append(c)
            elif len(core) > len(words) and core[: len(words)] == words:
                prefix.append(c)
        if not exact:
            # Places name markets, not companies: "North America" must not
            # fetch North America Lithium & Gold. Only an exact name may start
            # with one ("American Express", "United Airlines Holdings").
            if words[0] in _PLACES:
                continue
            # A lone word must be distinctive. "Ford" starts Ford Motor and
            # Ford Motor Credit, one family; "China" starts dozens of unrelated
            # companies, and fetching one at random answers nobody's question.
            if len(words) == 1 and len({tuple(_core(c["title"])[:2]) for c in prefix}) != 1:
                continue
        pool = exact or prefix
        if pool:
            add(min(pool, key=lambda c: (len(_core(c["title"])), len(c["title"]))))
    return found[:limit]
