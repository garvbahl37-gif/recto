"""SEC EDGAR client — real filings ingestion (live data, no mock).

Uses the public EDGAR REST APIs (no key). SEC fair-access policy REQUIRES a
descriptive User-Agent with contact info; set IL_EDGAR_USER_AGENT. Rate limit
is 10 req/s — we stay well under.

Modern 10-K/10-Q primary documents are inline-XBRL HTML: the top of the file is
a large `<ix:header>` / hidden context block full of machine tags
(`aapl:IPhoneMember`, dozens of dates). A naive tag-strip yields garbage, so we
parse with BeautifulSoup+lxml, drop the XBRL header, hidden nodes, scripts and
styles, then split the readable prose into 10-K "Item" sections (Business, Risk
Factors, MD&A, financials, …) that map cleanly to pages.
"""
from __future__ import annotations

import re
import time
from typing import Optional

import requests

from ..config import Settings, get_settings
from ..schemas import Document
from .pipeline import build_document_from_pages

_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
# full EDGAR universe (every entity that ever filed) — "NAME:CIK:" per line
_CIK_LOOKUP_URL = "https://www.sec.gov/Archives/edgar/cik-lookup-data.txt"
_SUBMISSIONS = "https://data.sec.gov/submissions/CIK{cik:010d}.json"
_ARCHIVE = "https://www.sec.gov/Archives/edgar/data/{cik}/{acc}/{doc}"
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[ \t]+")
# "Item 1.", "Item 1A.", "Item 7A." — the canonical 10-K/10-Q section anchors.
_ITEM = re.compile(r"\bItem\s+(\d{1,2}[A-C]?)\s*[\.\:—-]", re.IGNORECASE)
# a token that is XBRL noise: namespaced tags, CIKs, bare ISO dates
_XBRL_TOK = re.compile(r"^([a-z]+:[A-Za-z]|\d{10}$|\d{4}-\d{2}-\d{2}$)")


class EdgarClient:
    _companies: Optional[list[dict]] = None   # ticker'd companies (~10k)
    _entities: Optional[list[tuple[str, int]]] = None  # ALL EDGAR filers (name, cik)

    def __init__(self, settings: Optional[Settings] = None):
        self.s = settings or get_settings()
        self.session = requests.Session()
        self.session.headers.update({"User-Agent": self.s.edgar_user_agent})
        self._tickers: Optional[dict[str, int]] = None

    def _get(self, url: str, **kw):
        r = self.session.get(url, timeout=30, **kw)
        r.raise_for_status()
        time.sleep(0.15)  # be polite (<10 req/s)
        return r

    def _load_companies(self) -> list[dict]:
        """Full EDGAR company list (ticker, title, cik), cached on the class."""
        if EdgarClient._companies is None:
            data = self._get(_TICKERS_URL).json()
            EdgarClient._companies = [
                {"ticker": v["ticker"].upper(), "title": v["title"],
                 "cik": int(v["cik_str"])}
                for v in data.values()
            ]
        # Always (re)build this instance's ticker->CIK lookup from the cache.
        # (Bug fix: if another instance already populated the class cache, a new
        # instance would otherwise skip this and cik_for_ticker would 'not find'
        # every ticker.)
        if self._tickers is None:
            self._tickers = {c["ticker"]: c["cik"] for c in EdgarClient._companies}
        return EdgarClient._companies

    def _load_entities(self) -> list[tuple[str, int]]:
        """The FULL EDGAR universe (every filer, incl. those without a ticker),
        parsed from cik-lookup-data.txt and cached to disk + class. ~800k rows."""
        if EdgarClient._entities is not None:
            return EdgarClient._entities
        cache = self.s.cache_dir / "cik-lookup-data.txt"
        try:
            text = cache.read_text(encoding="latin-1") if cache.exists() else ""
        except Exception:
            text = ""
        if not text:
            text = self._get(_CIK_LOOKUP_URL).content.decode("latin-1")
            try:
                cache.parent.mkdir(parents=True, exist_ok=True)
                cache.write_text(text, encoding="latin-1")
            except Exception:
                pass
        ents: list[tuple[str, int]] = []
        for line in text.splitlines():
            # format: "COMPANY NAME:0000320193:"
            i = line.rfind(":", 0, len(line) - 1)
            if i <= 0:
                continue
            name, cik = line[:i], line[i + 1:].rstrip(":")
            if cik.isdigit():
                ents.append((name, int(cik)))
        EdgarClient._entities = ents
        return ents

    def cik_for_ticker(self, ticker: str) -> int:
        self._load_companies()
        try:
            return self._tickers[ticker.upper()]  # type: ignore[index]
        except (KeyError, TypeError) as exc:
            raise ValueError(f"ticker not found: {ticker}") from exc

    def resolve_cik(self, identifier: str) -> tuple[int, str]:
        """Resolve a ticker OR a raw CIK to (cik, label). `label` is the ticker
        when known, else 'CIK{n}' — used as the document id prefix."""
        ident = identifier.strip()
        self._load_companies()
        if ident.upper() in self._tickers:  # type: ignore[operator]
            return self._tickers[ident.upper()], ident.upper()  # type: ignore[index]
        digits = ident.upper().removeprefix("CIK")
        if digits.isdigit():
            return int(digits), f"CIK{int(digits)}"
        raise ValueError(f"unknown company: {identifier}")

    def search_companies(self, query: str, limit: int = 12) -> list[dict]:
        """Search the FULL EDGAR universe (ticker'd companies ranked first, then
        every other filer by name). Non-ticker'd entities use their CIK as the
        identifier so they can still be ingested. Powers the company picker."""
        companies = self._load_companies()
        q = query.strip().lower()
        if not q:
            popular = ["AAPL", "MSFT", "NVDA", "GOOGL", "AMZN", "META", "TSLA",
                       "JPM", "V", "WMT", "XOM", "JNJ"]
            idx = {c["ticker"]: c for c in companies}
            return [idx[t] for t in popular if t in idx][:limit]
        scored: list[tuple[int, dict]] = []
        for c in companies:
            tk, title = c["ticker"].lower(), c["title"].lower()
            if tk == q:
                s = 200
            elif tk.startswith(q):
                s = 180
            elif title.startswith(q):
                s = 160
            elif q in tk:
                s = 140
            elif q in title:
                s = 130 - min(20, title.index(q))
            else:
                continue
            scored.append((s, c))
        seen_cik = {c["cik"] for _, c in scored}
        # widen to the full universe when the ticker list is thin on matches.
        # Degrades gracefully to ticker-only results if the lookup is unavailable.
        if len(scored) < limit and len(q) >= 3:
            try:
                for name, cik in self._load_entities():
                    if cik in seen_cik:
                        continue
                    low = name.lower()
                    if low.startswith(q):
                        s = 60
                    elif q in low:
                        s = 40 - min(20, low.index(q))
                    else:
                        continue
                    scored.append((s, {"ticker": f"CIK{cik}", "title": name.title(), "cik": cik}))
                    seen_cik.add(cik)
                    if len(scored) > limit * 6:
                        break
            except Exception:
                pass
        scored.sort(key=lambda x: (-x[0], len(x[1]["title"])))
        return [c for _, c in scored[:limit]]

    # annual-report forms to try in order (US 10-K, foreign 20-F/40-F, legacy)
    _ANNUAL_FORMS = ["10-K", "10-K405", "20-F", "40-F"]

    def latest_filing(self, identifier: str, form: str = "10-K") -> dict:
        cik, _ = self.resolve_cik(identifier)
        sub = self._get(_SUBMISSIONS.format(cik=cik)).json()
        recent = sub["filings"]["recent"]
        # try the requested form first, then annual-report fallbacks
        wanted = [form] + [f for f in self._ANNUAL_FORMS if f != form]
        for want in wanted:
            for i, f in enumerate(recent["form"]):
                if f == want:
                    return {
                        "cik": cik,
                        "accession": recent["accessionNumber"][i].replace("-", ""),
                        "primary_doc": recent["primaryDocument"][i],
                        "date": recent["filingDate"][i],
                        "form": want,
                        "name": sub.get("name", identifier),
                    }
        have = sorted(set(recent["form"]))[:6]
        raise ValueError(
            f"{identifier} resolves to {sub.get('name', identifier)} (CIK {cik}), which "
            f"has no {form} or other annual report on EDGAR yet"
            + (f" — its filings are {', '.join(have)}" if have else "")
            + ". New holding companies, funds and ETFs often look like this.")

    @staticmethod
    def _clean_tokens(text: str) -> str:
        """Drop residual XBRL noise tokens and collapse whitespace."""
        out = []
        for tok in text.split():
            if _XBRL_TOK.match(tok):
                continue
            out.append(tok)
        return _WS.sub(" ", " ".join(out)).strip()

    @classmethod
    def _to_text(cls, html: str) -> str:
        """Parse inline-XBRL HTML into readable prose."""
        try:
            from bs4 import BeautifulSoup  # lazy
        except Exception:
            text = _TAG.sub(" ", html).replace("&nbsp;", " ").replace("&amp;", "&")
            return cls._clean_tokens(text)

        import warnings

        from bs4 import XMLParsedAsHTMLWarning
        warnings.filterwarnings("ignore", category=XMLParsedAsHTMLWarning)
        soup = BeautifulSoup(html, "lxml")
        # remove the XBRL header, hidden facts, scripts/styles, and display:none nodes
        for sel in ["ix\\:header", "ix\\:hidden", "script", "style", "head", "title"]:
            for node in soup.select(sel) if ":" not in sel else soup.find_all(
                    re.compile(sel.replace("\\:", ":"))):
                node.decompose()
        for node in soup.find_all(style=re.compile(r"display\s*:\s*none", re.I)):
            node.decompose()
        # block-level newlines so Item headers land at line starts
        for br in soup.find_all(["br", "p", "div", "tr", "h1", "h2", "h3"]):
            br.append("\n")
        text = soup.get_text(" ")
        lines = [cls._clean_tokens(ln) for ln in text.splitlines()]
        return "\n".join(ln for ln in lines if ln)

    @staticmethod
    def _item_sort_key(num: str) -> tuple[int, str]:
        m = re.match(r"(\d+)([A-C]?)", num.upper())
        return (int(m.group(1)), m.group(2)) if m else (99, "")

    @classmethod
    def _paginate(cls, text: str, max_pages: int = 400,
                  chars_per_page: int = 4000) -> list[str]:
        """Slice a filing into fixed-size pages.

        The cap has to clear a whole 10-K. At 40 pages (160K chars) NVIDIA's
        filing stopped inside Item 1A, so its income statement was never
        indexed and every revenue question was unanswerable while still
        looking answerable. JPMorgan's runs to 172 pages; 400 is a guard
        against pathological documents, not a budget.
        """
        """Section-aware pagination.

        A 10-K mentions each 'Item N.' at least twice — once in the table of
        contents (a short stub) and once as the real section. We group by item
        number, keep the LONGEST occurrence (the real prose, not the TOC line),
        order by canonical item number, drop stubs, then sub-split long
        sections so every page is a coherent, embeddable chunk.
        """
        matches = list(_ITEM.finditer(text))
        if len(matches) < 3:
            # not a standard filing layout — fall back to fixed-size chunks
            words, pages, buf, size = text.split(), [], [], 0
            for w in words:
                buf.append(w)
                size += len(w) + 1
                if size >= chars_per_page:
                    pages.append(" ".join(buf)); buf, size = [], 0
            if buf:
                pages.append(" ".join(buf))
            return pages[:max_pages] or [text[:chars_per_page]]

        best: dict[str, str] = {}
        for i, m in enumerate(matches):
            num = m.group(1).upper()
            end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
            sec = text[m.start():end].strip()
            if len(sec) > len(best.get(num, "")):
                best[num] = sec

        sections = [best[n] for n in sorted(best, key=cls._item_sort_key)
                    if len(best[n]) >= 300]          # drop TOC-only stubs
        if not sections:
            sections = [max(best.values(), key=len)]

        pages: list[str] = []
        for sec in sections:
            if len(sec) <= chars_per_page:
                pages.append(sec)
                continue
            words, buf, size = sec.split(), [], 0
            for w in words:
                buf.append(w)
                size += len(w) + 1
                if size >= chars_per_page:
                    pages.append(" ".join(buf)); buf, size = [], 0
            if buf:
                pages.append(" ".join(buf))
        pages = [p for p in pages if len(p) >= 60]   # drop page-footer fragments
        return pages[:max_pages] or [text[:chars_per_page]]

    @staticmethod
    def _section_label(page_text: str) -> str:
        m = _ITEM.search(page_text[:60])
        return f"Item {m.group(1)}" if m else ""

    def company_filings(self, identifier: str, forms: Optional[list[str]] = None,
                        limit: int = 30) -> dict:
        """Company profile + recent filing history from EDGAR — powers the
        'browse all filings from all companies' view. `identifier` is a ticker
        or a CIK; `forms` filters by type (e.g. ['10-K','10-Q']); None = all."""
        cik, label = self.resolve_cik(identifier)
        sub = self._get(_SUBMISSIONS.format(cik=cik)).json()
        recent = sub["filings"]["recent"]
        descs = recent.get("primaryDocDescription", [""] * len(recent["form"]))
        docs = recent.get("primaryDocument", [""] * len(recent["form"]))
        filings: list[dict] = []
        for i, f in enumerate(recent["form"]):
            if forms and f not in forms:
                continue
            if not docs[i]:
                continue
            filings.append({
                "form": f,
                "date": recent["filingDate"][i],
                "accession": recent["accessionNumber"][i].replace("-", ""),
                "primary_doc": docs[i],
                "description": descs[i] if i < len(descs) else "",
            })
            if len(filings) >= limit:
                break
        return {
            "ticker": label, "cik": cik, "name": sub.get("name", label),
            "industry": sub.get("sicDescription", ""),
            "tickers": sub.get("tickers", []), "filings": filings,
        }

    def _build(self, label: str, meta: dict, max_pages: int) -> Document:
        url = _ARCHIVE.format(cik=meta["cik"], acc=meta["accession"],
                              doc=meta["primary_doc"])
        html = self._get(url).text
        pages = self._paginate(self._to_text(html), max_pages=max_pages)
        actual = meta["form"]
        doc = build_document_from_pages(
            doc_id=f"{label.upper()}-{actual}-{meta['date']}",
            title=f"{meta['name']} {actual} ({meta['date']})",
            page_texts=pages, source=url, doc_type=actual, metadata=meta,
        )
        # carry the filing section forward so a page without its own "Item N."
        # header inherits the last seen section (for the UI + citations)
        last = ""
        for p in doc.pages:
            lbl = self._section_label(p.text)
            if lbl:
                last = lbl
            p.section = last
        return doc

    def fetch(self, identifier: str, form: str = "10-K", max_pages: int = 400) -> Document:
        _, label = self.resolve_cik(identifier)
        return self._build(label, self.latest_filing(identifier, form), max_pages)

    def fetch_specific(self, identifier: str, accession: str, primary_doc: str,
                       form: str, date: str, max_pages: int = 400) -> Document:
        """Ingest one specific filing (by accession) rather than the latest."""
        cik, label = self.resolve_cik(identifier)
        meta = {"cik": cik, "accession": accession.replace("-", ""),
                "primary_doc": primary_doc, "form": form, "date": date,
                "name": self.company_name(identifier)}
        return self._build(label, meta, max_pages)

    def company_name(self, identifier: str) -> str:
        for c in self._load_companies():
            if c["ticker"] == identifier.upper():
                return c["title"]
        return identifier.upper()
