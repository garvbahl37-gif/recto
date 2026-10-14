"""LLM/VLM provider.

Exposes the *semantic operations* the agents need rather than a raw text->text
call, so that the real (Claude) and stub backends implement one clean interface:

    assess_difficulty(question)          -> Difficulty      (router)
    extract_claims(question, pages)      -> list[Claim]     (extractor / VLM read)
    verify_claim(question, claim, page)  -> (ok, conf, note)(verifier)
    synthesize(question, claims)         -> str             (synthesizer)

The stub backend is deterministic and *grounded*: it locates answer text inside
the retrieved page text and builds region-level citations, so the full
multi-agent + eval pipeline runs and passes with no GPU and no API key. The
Claude backend routes simple questions to a cheap model and complex ones to a
strong model, attaching page images when available (true VLM reasoning).
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from functools import lru_cache
from typing import Optional, Protocol

from ..config import Settings, get_settings
from ..observability.cost import CostMeter
from ..schemas import BBox, Citation, Claim, Difficulty, RetrievedPage
from ..words import stem

_WORD = re.compile(r"[A-Za-z0-9%$.\-]+")
_STOP = {
    "the", "a", "an", "of", "in", "on", "for", "to", "and", "or", "is", "are",
    "was", "were", "what", "which", "how", "much", "did", "does", "do", "that",
    "this", "with", "by", "at", "from", "its", "it", "as", "be", "year", "over",
}
# Generic finance/report words that are never the *focus* of a question — a
# claim matching only these is not really answering it. (Domain stoplist; in a
# real system you'd learn this or use IDF over a large filing corpus.)
_GENERIC_FINANCE = {
    "total", "per", "share", "shares", "fiscal", "annual", "report", "form",
    "company", "companys", "corporation", "inc", "commission", "compared",
    "prior", "approximately", "million", "billion", "during", "ended", "period",
    "statements", "consolidated", "results", "financial", "increase", "decrease",
    "value", "amount", "number", "file", "registrant", "pursuant", "requirements",
}
_GENERIC_STEMS = {stem(w) for w in _GENERIC_FINANCE}
_YEAR = re.compile(r"^(19|20)\d{2}$")
_COMPLEX_HINTS = (
    "change", "yoy", "year-over-year", "compare", "comparison", "versus", "vs",
    "trend", "difference", "growth", "decline", "drove", "driven", "segment",
    "across", "between", "chart", "both", "each", "why", "breakdown", "margin",
)


def _keywords(text: str) -> list[str]:
    # Stems, matched as substrings: "revenues" is asked, "Total revenue" is
    # printed, and both contain "revenue".
    return [stem(w.lower()) for w in _WORD.findall(text) if w.lower() not in _STOP and len(w) > 1]


def _sentences(text: str) -> list[str]:
    parts = re.split(r"(?<=[.;:])\s+|\n+", text)
    return [p.strip() for p in parts if p.strip()]


# A figure as a claim states it: grouped thousands, a decimal, or four or more
# digits. Years are dates rather than figures, and short counts are everywhere.
_FIGURE = re.compile(r"(?<![\d.,])(?:\d{1,3}(?:,\d{3})+(?:\.\d+)?|\d+\.\d+|\d{4,})")


def figures_missing(claim: str, page_text: str) -> list[str]:
    """Figures a claim states that its cited page does not print.

    A claim that "net revenue was $52,853 million" cannot be supported by a
    page without 52,853 on it, whatever a model says. When several claims
    read alike, a batched verifier can attach the right verdict to the wrong
    one; this check cannot, so a footnote never opens on a page (an auditor's
    report, say) that lacks the number it is cited for.
    """
    page = re.sub(r"(?<=\d),(?=\d{3}(?!\d))", "", page_text or "")     # 52,853 -> 52853
    missing = []
    for f in _FIGURE.findall(claim or ""):
        bare = f.replace(",", "")
        if "." not in bare and len(bare) == 4 and 1900 <= int(bare) <= 2100:
            continue
        if not re.search(rf"(?<![\d.]){re.escape(bare)}(?!\d)", page):
            missing.append(f)
    return missing


_YEAR_IN = re.compile(r"\b(?:19|20)\d{2}\b")
_AMOUNT = re.compile(r"\d{1,3}(?:,\d{3})+|\d+\.\d+")


def _is_column_header(line: str) -> bool:
    """'Years Ended (In Millions) Dec 27, 2025 Dec 28, 2024 Dec 30, 2023'"""
    return len(_YEAR_IN.findall(line)) >= 2 and not _AMOUNT.search(line)


def _score_line(line: str, kws: list[str]) -> float:
    low = line.lower()
    hits = sum(1 for k in kws if k in low)
    # reward lines that carry numbers (financial facts live in numbers)
    num_bonus = 0.5 if re.search(r"\d", line) else 0.0
    return hits + num_bonus


# How much of the question a claim must carry to count as answering it.
MIN_RELEVANCE = 0.34



@lru_cache(maxsize=64)
def _doc_page_texts(doc_id: str) -> tuple[str, ...]:
    """Lowercased page texts for a whole indexed document.

    Cached because the verifier asks for this once per claim, and a document's
    pages do not change once it has been ingested.
    """
    try:
        from ..knowledge import load_registry

        doc = load_registry().get(doc_id)
        if doc is None:
            return ()
        return tuple((p.text or " ".join(r.text for r in p.regions)).lower()
                     for p in doc.pages)
    except Exception:
        return ()


@lru_cache(maxsize=64)
def _doc_entity_terms(doc_id: str) -> frozenset:
    """Words naming the filer itself, from the document title.

    A company's own name is not a topic inside its own filing: "NVIDIA" appears
    on nearly every page of NVIDIA's 10-K, so a line matching it tells you
    nothing about whether it answers the question. The doc_id alone only yields
    the ticker ("nvda"), which is why the full name used to slip through and
    turn every NVLink paragraph into evidence.
    """
    try:
        from ..knowledge import load_registry

        doc = load_registry().get(doc_id)
        title = getattr(doc, "title", "") if doc is not None else ""
    except Exception:
        title = ""
    return frozenset(_keywords(title.replace("-", " "))) if title else frozenset()


def _page_frequency(term: str, pages: list[RetrievedPage]) -> float:
    """Share of pages containing `term`, measured over the WHOLE filings the
    retrieved pages came from.

    Measuring over just the retrieved subset makes this unstable: the graph
    widens top_k on a retry, which changed the page count and could flip a
    background term into a topical one mid-run. The full document is a fixed
    denominator.
    """
    corpus: list[str] = []
    for doc_id in {p.doc_id for p in pages}:
        corpus.extend(_doc_page_texts(doc_id))
    if not corpus:  # document not in the registry (tests, ad-hoc pages)
        corpus = [(p.text or " ".join(r.text for r in p.regions)).lower() for p in pages]
    if not corpus:
        return 0.0
    return sum(1 for c in corpus if term in c) / len(corpus)


@dataclass
class _Focus:
    """What the question is actually asking about, weighted by how much each
    term discriminates within the pages we retrieved.

    Two different judgements come out of this, and keeping them apart is the
    whole point:

    * Terms *absent* from the corpus say the filing may not cover the question
      — an answerability signal. They must not be charged against individual
      claims, or a correct answer phrased differently from the question ("third
      quarter" for "Q3") scores near zero.
    * Terms *present but everywhere* are background. A line matching only those
      is not evidence, which is how a cover page reading "ACME ROBOTICS
      CORPORATION" came to be offered as a headcount figure.
    """
    weights: dict[str, float]
    freqs: dict[str, float]

    @property
    def present(self) -> dict[str, float]:
        return {k: w for k, w in self.weights.items() if self.freqs.get(k, 0.0) > 0.0}

    @property
    def answerable(self) -> bool:
        """False when nothing the question is about appears in these pages at
        all — the filing simply does not cover it."""
        if not self.weights:
            return True          # nothing specific asked; fall back to retrieval order
        return bool(self.present)

    def relevance(self, text: str) -> float:
        """Share of the question's *findable* information this text carries."""
        pres = self.present
        if not self.weights:
            return 1.0
        if not pres:
            return 0.0
        total = sum(pres.values())
        if total <= 0:
            return 0.0
        low = text.lower()
        return sum(w for k, w in pres.items() if k in low) / total


def _focus(question: str, pages: list[RetrievedPage]) -> _Focus:
    weights, freqs = {}, {}
    for k in StubProvider._focus_keywords(question, pages):
        f = _page_frequency(k, pages)
        freqs[k] = f
        weights[k] = 1.0 - min(0.9, f)
    return _Focus(weights=weights, freqs=freqs)


@dataclass
class LLMResult:
    text: str
    tokens_in: int
    tokens_out: int
    model: str


class Provider(Protocol):
    name: str

    def assess_difficulty(self, question: str) -> Difficulty: ...
    def extract_claims(self, question: str, pages: list[RetrievedPage],
                       meter: CostMeter) -> list[Claim]: ...
    def verify_claim(self, question: str, claim: Claim, pages: list[RetrievedPage],
                     meter: CostMeter) -> tuple[bool, float, str]: ...
    def synthesize(self, question: str, claims: list[Claim],
                   meter: CostMeter) -> tuple[str, str]: ...  # (text, model_used)


# --------------------------------------------------------------------------- #
# Stub provider — deterministic, grounded, dependency-free.
# --------------------------------------------------------------------------- #
class StubProvider:
    name = "stub"

    def assess_difficulty(self, question: str) -> Difficulty:
        low = question.lower()
        if any(h in low for h in _COMPLEX_HINTS):
            return Difficulty.complex
        # two or more numbers implied, or multi-clause -> complex
        if low.count(" and ") >= 1 and "?" in low and len(low) > 80:
            return Difficulty.complex
        return Difficulty.simple

    def _best_region(self, page: RetrievedPage, line: str) -> Optional[BBox]:
        """Pick the region whose text best overlaps the located line."""
        if not page.regions:
            return None
        kws = set(_keywords(line))
        best, best_score = None, -1.0
        for r in page.regions:
            rk = set(_keywords(r.text))
            score = len(kws & rk) + (0.5 if r.type.value in ("table", "chart") else 0.0)
            if score > best_score:
                best, best_score = r, score
        return best.bbox if best else None

    @staticmethod
    def _focus_keywords(question: str, pages: list[RetrievedPage]) -> set[str]:
        """The question's *content* keywords: drop generic finance words,
        bare numbers/years, and entity identifiers derived from the retrieved
        doc_ids (e.g. 'acme', '10k', '2024'). A claim must hit one of these to
        be kept — so a question whose focus term ('compensation', 'dividend')
        is absent from the corpus yields no claims and the pipeline abstains."""
        entity = set()
        for p in pages:
            entity.update(_keywords(p.doc_id.replace("-", " ")))
            entity.update(_doc_entity_terms(p.doc_id))
        focus = set()
        for k in _keywords(question):
            if k in _GENERIC_STEMS or _YEAR.match(k) or k.isdigit():
                continue
            if k in entity:
                continue
            focus.add(k)
        return focus

    def extract_claims(self, question: str, pages: list[RetrievedPage],
                       meter: CostMeter) -> list[Claim]:
        kws = _keywords(question)
        focus = _focus(question, pages)
        if not focus.answerable:
            # Nothing the question is actually about appears in these pages.
            meter.add("stub", tokens_in=200, tokens_out=0)
            return []
        claims: list[Claim] = []
        seen: set[str] = set()
        for page in pages:
            corpus = page.text or " ".join(r.text for r in page.regions)
            header, since = "", 99
            for line in _sentences(corpus):
                since += 1
                if _is_column_header(line):
                    header, since = line, 0
                    continue
                # Keep only lines that carry enough of what was actually asked.
                # Hitting one background term (the company's own name) is not
                # evidence of anything.
                rel = focus.relevance(line)
                if rel < MIN_RELEVANCE:
                    continue
                sc = _score_line(line, kws)
                if sc < 1.0:
                    continue
                norm = line.lower().strip()
                if norm in seen:
                    continue
                seen.add(norm)
                bbox = self._best_region(page, line)
                region_id = None
                if bbox is not None:
                    for r in page.regions:
                        if r.bbox is bbox:
                            region_id = r.region_id
                            break
                # A table row means nothing without its columns: "Total net
                # revenue $ 52,853 $ 53,101 $ 54,228" says which year only
                # with the header above it, and a reader shown the row alone
                # rightly will not say.
                text = line
                if header and since <= 30 and len(_AMOUNT.findall(line)) >= 2:
                    text = f"{line} (columns: {header})"
                claims.append(Claim(
                    text=text,
                    citations=[Citation(
                        doc_id=page.doc_id, page_number=page.page_number,
                        bbox=bbox, region_id=region_id, snippet=line[:240],
                    )],
                    confidence=round(rel, 3),
                ))
        claims.sort(key=lambda c: c.confidence, reverse=True)
        meter.add("stub", tokens_in=200, tokens_out=80)
        return claims[:6]

    def verify_claim(self, question: str, claim: Claim, pages: list[RetrievedPage],
                     meter: CostMeter) -> tuple[bool, float, str]:
        meter.add("stub", tokens_in=120, tokens_out=20)
        if not claim.citations:
            return False, 0.0, "no citation"
        cit = claim.citations[0]
        page = next((p for p in pages
                     if p.doc_id == cit.doc_id and p.page_number == cit.page_number), None)
        if page is None:
            return False, 0.1, "cited page not in retrieved set"
        corpus = (page.text or " ".join(r.text for r in page.regions)).lower()
        snippet = (cit.snippet or claim.text).lower()
        # Grounding check: the claim's key tokens must actually appear on the page.
        kws = _keywords(snippet)
        if not kws:
            return False, 0.2, "empty claim"
        present = sum(1 for k in kws if k in corpus) / len(kws)
        if present < 0.7:
            return False, round(present, 3), f"only {present:.0%} of claim tokens on cited page"
        # Being printed on the page is necessary but not sufficient: the claims
        # are lifted verbatim off that page, so token overlap alone is
        # tautological and would score 1.00 for anything. What the verifier is
        # really for is whether the claim answers the question that was asked.
        focus = _focus(question, pages)
        rel = focus.relevance(claim.text)
        conf = round(present * rel, 3)
        if not focus.answerable:
            return False, conf, "the pages do not cover what the question asks"
        if rel < MIN_RELEVANCE:
            return False, conf, f"on the cited page but only {rel:.0%} responsive to the question"
        return True, conf, "grounded and responsive"

    def synthesize(self, question: str, claims: list[Claim],
                   meter: CostMeter) -> tuple[str, str]:
        verified = [c for c in claims if c.verified]
        use = verified or claims[:2]
        meter.add("stub", tokens_in=300, tokens_out=120)
        if not use:
            return ("I could not find grounded evidence for this question in the "
                    "retrieved pages.", "stub")
        lines = []
        for c in use[:3]:
            cite = c.citations[0] if c.citations else None
            tag = f" [{cite.doc_id} p.{cite.page_number}]" if cite else ""
            lines.append(f"- {c.text}{tag}")
        body = "\n".join(lines)
        return (f"Based on the retrieved pages:\n{body}", "stub")


# --------------------------------------------------------------------------- #
# Claude provider — real VLM reasoning with cost-based model routing.
# --------------------------------------------------------------------------- #
class ClaudeProvider:
    name = "claude"

    def __init__(self, settings: Optional[Settings] = None):
        import anthropic  # lazy

        self.s = settings or get_settings()
        self.client = anthropic.Anthropic(api_key=self.s.anthropic_api_key)

    # -- low-level call (optionally multimodal) --
    def _call(self, system: str, user: str, model: str,
              image_paths: Optional[list[str]], meter: CostMeter,
              max_tokens: int = 1024) -> LLMResult:
        content: list[dict] = []
        for path in (image_paths or [])[:8]:
            try:
                import base64

                with open(path, "rb") as f:
                    b = base64.standard_b64encode(f.read()).decode()
                media = "image/png" if path.lower().endswith("png") else "image/jpeg"
                content.append({"type": "image", "source": {
                    "type": "base64", "media_type": media, "data": b}})
            except Exception:
                continue
        content.append({"type": "text", "text": user})
        resp = self.client.messages.create(
            model=model, max_tokens=max_tokens, system=system,
            messages=[{"role": "user", "content": content}],
        )
        text = "".join(getattr(b, "text", "") for b in resp.content)
        ti = getattr(resp.usage, "input_tokens", 0)
        to = getattr(resp.usage, "output_tokens", 0)
        meter.add(model, ti, to)
        return LLMResult(text=text, tokens_in=ti, tokens_out=to, model=model)

    @staticmethod
    def _json(text: str) -> Optional[dict]:
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(0))
        except Exception:
            return None

    def assess_difficulty(self, question: str) -> Difficulty:
        # cheap heuristic first; only the reasoning steps need the model
        return StubProvider().assess_difficulty(question)

    def extract_claims(self, question: str, pages: list[RetrievedPage],
                       meter: CostMeter) -> list[Claim]:
        page_ctx = "\n\n".join(
            f"[{p.doc_id} p.{p.page_number}] regions="
            f"{[r.region_id for r in p.regions]}\n{p.text[:1500]}"
            for p in pages
        )
        images = [p.image_path for p in pages if p.image_path]
        system = (
            "You read financial/compliance document pages (images + text) and "
            "extract atomic factual claims that answer the question. Return JSON "
            '{"claims":[{"text":..,"doc_id":..,"page":int,"region_id":str|null,'
            '"snippet":..}]} Only include facts visible on the pages.'
        )
        user = f"Question: {question}\n\nPages:\n{page_ctx}\n\nReturn JSON only."
        res = self._call(system, user, self.s.llm_model, images, meter, max_tokens=1200)
        data = self._json(res.text) or {"claims": []}
        claims: list[Claim] = []
        for c in data.get("claims", []):
            claims.append(Claim(
                text=c.get("text", ""),
                citations=[Citation(
                    doc_id=c.get("doc_id", pages[0].doc_id if pages else ""),
                    page_number=int(c.get("page", pages[0].page_number if pages else 1)),
                    region_id=c.get("region_id"),
                    snippet=c.get("snippet", "")[:240],
                )],
                confidence=0.8,
            ))
        # attach bbox from region_id
        pmap = {(p.doc_id, p.page_number): p for p in pages}
        for cl in claims:
            cit = cl.citations[0]
            page = pmap.get((cit.doc_id, cit.page_number))
            if page and cit.region_id:
                for r in page.regions:
                    if r.region_id == cit.region_id:
                        cit.bbox = r.bbox
        return claims

    def verify_claim(self, question: str, claim: Claim, pages: list[RetrievedPage],
                     meter: CostMeter) -> tuple[bool, float, str]:
        cit = claim.citations[0] if claim.citations else None
        if not cit:
            return False, 0.0, "no citation"
        page = next((p for p in pages if p.doc_id == cit.doc_id
                     and p.page_number == cit.page_number), None)
        missing = figures_missing(claim.text, page.text) if page else []
        if missing:
            return False, 0.1, f"{missing[0]} is not printed on the cited page"
        images = [page.image_path] if page and page.image_path else []
        system = (
            "You are a strict verifier. Decide whether the claim is BOTH directly "
            "supported by the cited page AND responsive to the question asked. A "
            "true statement copied from the page that does not address the "
            "question is not supported. Return JSON "
            '{"supported":bool,"confidence":0..1,"note":str}. Default to '
            "supported=false when uncertain."
        )
        user = (f"Question: {question}\nClaim: {claim.text}\n"
                f"Cited: {cit.doc_id} p.{cit.page_number}\n"
                f"Page text: {(page.text[:1500] if page else '')}\nReturn JSON only.")
        res = self._call(system, user, self.s.llm_model, images, meter, max_tokens=300)
        data = self._json(res.text) or {}
        ok = bool(data.get("supported", False))
        conf = float(data.get("confidence", 0.0))
        return ok, conf, str(data.get("note", ""))

    def synthesize(self, question: str, claims: list[Claim],
                   meter: CostMeter) -> tuple[str, str]:
        difficulty = self.assess_difficulty(question)
        model = self.s.llm_model if difficulty == Difficulty.complex else self.s.llm_model_cheap
        verified = [c for c in claims if c.verified] or claims
        ev = "\n".join(
            f"- {c.text}  [{c.citations[0].doc_id} p.{c.citations[0].page_number}]"
            for c in verified if c.citations
        )
        system = (
            "Answer the question using ONLY the verified evidence. Cite each fact "
            "inline as [doc p.N]. If evidence is insufficient, say so. Be concise."
        )
        user = f"Question: {question}\n\nVerified evidence:\n{ev}"
        res = self._call(system, user, model, None, meter, max_tokens=700)
        return res.text, model

    def complete(self, system: str, user: str, meter: Optional[CostMeter] = None,
                 max_new_tokens: Optional[int] = None) -> str:
        return self._call(system, user, self.s.llm_model, None,
                          meter or CostMeter(), max_tokens=max_new_tokens or 700).text


def get_provider(settings: Optional[Settings] = None) -> Provider:
    """Fully local by default (deterministic grounded stub); Claude or Ollama
    Cloud when a key is configured. Any init failure falls back to the stub."""
    s = settings or get_settings()
    try:
        backend = s.resolve_llm()
        if backend == "claude":
            return ClaudeProvider(s)
        if backend == "ollama":
            from .ollama import OllamaProvider
            return OllamaProvider(s)
    except Exception:
        pass
    return StubProvider()
