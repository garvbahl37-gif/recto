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

import mmap
import re
import threading
import time
from pathlib import Path
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
# The same anchor as a heading: at the start of a line, perhaps after its Part.
# A cross-reference in running prose ("as discussed in Item 8.") is not one.
_HEADING = re.compile(
    r"^[ \t]*(?:PART\s+[IV]+[\.,:]?\s*[—–-]?\s*)?Item\s+\d{1,2}[A-C]?\s*[\.\:—–-]",
    re.IGNORECASE | re.MULTILINE)
# a token that is XBRL noise: namespaced tags, CIKs, bare ISO dates
_XBRL_TOK = re.compile(r"^([a-z]+:[A-Za-z]|\d{10}$|\d{4}-\d{2}-\d{2}$)")


class EdgarClient:
    _companies: Optional[list[dict]] = None   # ticker'd companies (~10k)
    _entities: Optional[mmap.mmap] = None     # ALL EDGAR filers, mapped from disk
    _entities_lock = threading.Lock()

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

    def _load_entities(self) -> mmap.mmap:
        """Every entity that has ever filed with EDGAR, about a million rows of
        "name:cik:", lower-cased on disk once and mapped rather than parsed.

        Parsed into Python objects the list took 279 MB, more than half of a
        small server. Mapped, it is searched in place and the operating system
        keeps only the pages it has touched.
        """
        with EdgarClient._entities_lock:
            if EdgarClient._entities is None:
                path = self.s.cache_dir / "cik-lookup-data.lower.txt"
                if not path.exists():
                    self._fetch_entities(path)
                with open(path, "rb") as f:
                    EdgarClient._entities = mmap.mmap(f.fileno(), 0, access=mmap.ACCESS_READ)
            return EdgarClient._entities

    def _fetch_entities(self, path: Path) -> None:
        """Lower-case EDGAR's filer list into `path` a megabyte at a time, from a
        copy cached by earlier versions when there is one."""
        path.parent.mkdir(parents=True, exist_ok=True)
        part = path.with_suffix(".part")
        legacy = self.s.cache_dir / "cik-lookup-data.txt"
        with open(part, "wb") as out:
            if legacy.exists():
                with open(legacy, "rb") as src:
                    for chunk in iter(lambda: src.read(1 << 20), b""):
                        out.write(chunk.lower())
            else:
                with self.session.get(_CIK_LOOKUP_URL, stream=True, timeout=120) as r:
                    r.raise_for_status()
                    for chunk in r.iter_content(1 << 20):
                        out.write(chunk.lower())
        part.replace(path)

    def prefetch_entities(self) -> None:
        """Download the filer list ahead of time (the Docker build does), so the
        first search after a cold start does not wait 40 MB for it."""
        self._load_entities()

    def _search_entities(self, q: str, skip: set[int], want: int) -> list[tuple[int, dict]]:
        """Filers whose name contains `q`, in file order, up to `want` of them."""
        mm = self._load_entities()
        needle = q.encode("latin-1", "ignore")
        found: list[tuple[int, dict]] = []
        pos = mm.find(needle) if needle else -1
        while pos != -1 and len(found) < want:
            start = mm.rfind(b"\n", 0, pos) + 1
            end = mm.find(b"\n", pos)
            end = len(mm) if end == -1 else end
            line = mm[start:end].rstrip(b"\r")
            # format: "company name:0000320193:"; the match must be in the name
            i = line.rfind(b":", 0, len(line) - 1)
            cik = line[i + 1:].rstrip(b":") if i > 0 else b""
            if i > 0 and pos - start < i and cik.isdigit() and int(cik) not in skip:
                n = int(cik)
                skip.add(n)
                score = 60 if pos == start else 40 - min(20, pos - start)
                found.append((score, {"ticker": f"CIK{n}",
                                      "title": line[:i].decode("latin-1").title(), "cik": n}))
            pos = mm.find(needle, end)
        return found

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
                scored += self._search_entities(q, seen_cik, limit * 6 + 1 - len(scored))
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

    @classmethod
    def _paginate(cls, text: str, max_pages: int = 400,
                  chars_per_page: int = 4000) -> list[str]:
        """Cut a filing into pages without losing any of it.

        The filing is kept in its own order and split where an "Item N."
        heading starts a line. The only text dropped is the table of contents,
        whose entries are a heading and a page number each. This used to keep
        just the longest stretch per item number, which discarded everything
        before the first heading (the cover page, and all of Intel's and GE's
        reports, which index their items at the back) and every stretch a
        cross-reference cut off: JPMorgan's filing lost 44% of its text.

        400 pages clears the largest 10-K (JPMorgan's runs to about 250); it is
        a guard against pathological documents, not a budget.
        """
        cuts = [0] + [m.start() for m in _HEADING.finditer(text)] + [len(text)]
        sections = [text[a:b].strip() for a, b in zip(cuts, cuts[1:])]
        pages: list[str] = []
        for sec in sections:
            if len(sec) >= 300:                     # shorter is a contents entry
                pages += cls._chunk(sec, chars_per_page)
        pages = [pg for pg in pages if len(pg) >= 60]   # page-footer fragments
        return pages[:max_pages] or [text[:chars_per_page].strip()]

    @staticmethod
    def _chunk(section: str, size: int) -> list[str]:
        """Pages of up to `size` characters, broken between lines so a table row
        stays whole; a paragraph longer than a page breaks between words."""
        pages: list[str] = []
        buf: list[str] = []
        n = 0
        for line in section.split("\n"):
            while len(line) > size:
                cut = line.rfind(" ", 0, size)
                cut = cut if cut > 0 else size
                if buf:
                    pages.append("\n".join(buf)); buf, n = [], 0
                pages.append(line[:cut])
                line = line[cut:].lstrip()
            if buf and n + len(line) > size:
                pages.append("\n".join(buf)); buf, n = [], 0
            buf.append(line)
            n += len(line) + 1
        if buf:
            pages.append("\n".join(buf))
        return pages

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

    # Forms whose substance is filed as exhibits: an 8-K's earnings release is
    # Exhibit 99.1, and a 40-F attaches the annual report and financials.
    _EXHIBIT_FORMS = {"8-K", "8-K/A", "6-K", "6-K/A", "40-F", "40-F/A"}
    # Never content: auditor consents (23), SOX certifications (31, 32),
    # clawback policies (97), XBRL (101, 104), viewer pages, indexes, and the
    # full-submission text file (named for the accession number).
    _BOILERPLATE = re.compile(
        r"(?:ex|exhibit)[-_ ]?(?:23|31|32|97|101|104)(?!\d)|^r\d+\.htm$|index|\d{10}-\d{2}-\d{6}",
        re.I)
    # An 8-K or 6-K carries its release as Exhibit 99.
    _RELEASE = re.compile(r"(?:ex|exhibit)[-_ ]?99|dex99", re.I)
    # A 40-F carries its annual information form, statements and MD&A as
    # Exhibits 1.x or 99.x, or as supplementary documents (name_d2.htm).
    _ANNUAL = re.compile(r"(?:ex|exhibit)[-_ ]?(?:99|1[1-3])|dex99|_d\d+\.htm$", re.I)
    # Some 10-Ks (IBM's) incorporate their financial statements and MD&A from
    # the annual report to shareholders, filed alongside as Exhibit 13 or as a
    # supplementary document (IBM's is ibm-20251231_d2.htm).
    _EX13 = re.compile(r"(?:ex|exhibit)[-_ ]?13(?!\d)|dex13|_d\d+\.htm$", re.I)
    _BY_REFERENCE = re.compile(r"annual report to (?:stock|share)holders", re.I)

    def _exhibit_texts(self, meta: dict, limit: int = 4,
                       max_chars: int = 900_000,
                       wanted: Optional[re.Pattern] = None) -> list[tuple[str, str]]:
        """(name, text) of the documents that carry a filing's substance."""
        base = _ARCHIVE.format(cik=meta["cik"], acc=meta["accession"], doc="")
        try:
            items = self._get(base + "index.json").json()["directory"]["item"]
        except Exception:  # noqa: BLE001 — no index, read the main document alone
            return []
        if wanted is None:
            wanted = self._ANNUAL if meta["form"].upper().startswith("40-F") else self._RELEASE
        out, total = [], 0
        for it in items:
            name = str(it.get("name", ""))
            if (name == meta["primary_doc"] or not name.lower().endswith((".htm", ".html", ".txt"))
                    or self._BOILERPLATE.search(name) or not wanted.search(name)):
                continue
            try:
                text = self._to_text(self._get(base + name).text)
            except Exception:  # noqa: BLE001
                continue
            out.append((name, text))
            total += len(text)
            if len(out) >= limit or total >= max_chars:
                break
        return out

    def _build(self, label: str, meta: dict, max_pages: int) -> Document:
        url = _ARCHIVE.format(cik=meta["cik"], acc=meta["accession"],
                              doc=meta["primary_doc"])
        html = self._get(url).text
        text = self._to_text(html)
        if meta["form"].upper() in self._EXHIBIT_FORMS:
            for name, body in self._exhibit_texts(meta):
                text += f"\n\nExhibit {name}\n{body}"
        elif meta["form"].upper().startswith("10-K") and self._BY_REFERENCE.search(text):
            for name, body in self._exhibit_texts(meta, limit=2, wanted=self._EX13):
                text += f"\n\nExhibit {name}\n{body}"
        pages = self._paginate(text, max_pages=max_pages)
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
