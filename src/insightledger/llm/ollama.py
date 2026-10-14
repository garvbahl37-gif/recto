"""Ollama Cloud provider — real reading and writing through an
OpenAI-compatible endpoint (https://ollama.com/v1).

The model is treated as an untrusted collaborator. Everything it returns is
checked against the pages that were actually retrieved before it can reach the
user:

* a claim must cite a page the model was shown, and quote text that is
  genuinely on that page, or it is dropped at extraction;
* verification starts with a deterministic quote check and only then asks the
  model whether the claim is supported *and* responsive to the question;
* the synthesizer may only cite claims that were verified — any other citation
  it writes is removed, and a bullet left with no citation is removed with it.

Routing mirrors the Claude provider: lookups go to the fast model, reasoning
questions to the strong one. Verification is batched into one call rather than
one call per claim. Any network or parsing failure falls back to the
deterministic stub for that step, so a slow or rate-limited model degrades the
answer instead of breaking the request.
"""
from __future__ import annotations

import json
import logging
import re
import time
from typing import Any, Optional

from ..config import Settings, get_settings
from ..observability.cost import CostMeter
from ..schemas import Citation, Claim, Difficulty, RetrievedPage
from .provider import StubProvider

_FENCE = re.compile(r"^```(?:json)?\s*|\s*```$", re.MULTILINE)
# Same citation shape the frontend parses: [NVDA-10-K-2026-02-25 p.50]
_CITE = re.compile(r"\[\s*([A-Za-z0-9.\-]+)\s*,?\s*p(?:age)?\.?\s*(\d+)\s*\]")
_ABSTAIN = "I could not find grounded evidence for this question in the retrieved pages."

log = logging.getLogger("insightledger.llm.ollama")
_RETRYABLE = {429, 500, 502, 503, 504}

_PAGE_CHARS = 3500      # per page sent to the extractor
# The verifier sees the whole cited page. At 2,500 characters it was shown a
# truncated page, could not find a quote sitting lower down, and rejected a
# claim the deterministic quote check had already proven was on the page.
_VERIFY_CHARS = 6000


def _squash(text: str) -> str:
    """Lowercase, normalise quotes, drop all whitespace — so a quote survives
    the model re-spacing '$ 165,218' as '$165,218'."""
    text = text.replace("’", "'").replace("‘", "'")
    text = text.replace("“", '"').replace("”", '"')
    return re.sub(r"\s+", "", text.lower())


def quote_on_page(quote: str, page_text: str) -> bool:
    """True when every fragment of `quote` (split on ellipses) is on the page."""
    if not quote or not page_text:
        return False
    page = _squash(page_text)
    fragments = [f for f in re.split(r"\.\.\.|…", quote) if len(f.strip()) >= 6]
    if not fragments:
        return False
    return all(_squash(f) in page for f in fragments)


def _parse_json(text: str) -> Optional[dict]:
    if not text:
        return None
    body = _FENCE.sub("", text.strip())
    start, end = body.find("{"), body.rfind("}")
    if start < 0 or end <= start:
        return None
    try:
        out = json.loads(body[start:end + 1])
    except json.JSONDecodeError:
        return None
    return out if isinstance(out, dict) else None


class OllamaProvider:
    name = "ollama"

    def __init__(self, settings: Optional[Settings] = None, session: Any = None):
        import requests  # core dependency; imported lazily like the others

        self.s = settings or get_settings()
        self.base = self.s.ollama_base_url.rstrip("/")
        self.http = session or requests.Session()
        self.stub = StubProvider()
        self.last_model = self.s.ollama_model_cheap

    # ------------------------------------------------------------------ #
    def _chat(self, model: str, system: str, user: str, meter: CostMeter,
              max_tokens: int, effort: str = "low") -> str:
        """One chat completion, retried once on a transient failure.

        Free-tier keys are rate limited, and a question makes up to three calls
        in quick succession; without a retry, one 429 silently downgraded the
        whole answer to the stub.
        """
        payload = {
            "model": model,
            "messages": [{"role": "system", "content": system},
                         {"role": "user", "content": user}],
            "temperature": 0,
            "max_tokens": max_tokens,
            # gpt-oss reasons before it answers; low effort keeps the latency
            # of a three-call pipeline reasonable.
            "reasoning_effort": effort,
        }
        headers = {"Authorization": f"Bearer {self.s.ollama_api_key}",
                   "Content-Type": "application/json"}
        for attempt in (1, 2):
            try:
                resp = self.http.post(f"{self.base}/chat/completions", json=payload,
                                      headers=headers, timeout=self.s.llm_timeout)
            except Exception as exc:  # timeouts, resets
                if attempt == 2:
                    raise
                log.warning("%s request failed (%s); retrying", model, exc)
                time.sleep(1.5)
                continue
            if resp.status_code in _RETRYABLE and attempt == 1:
                retry_after = (getattr(resp, "headers", None) or {}).get("Retry-After", "2")
                try:
                    wait = min(max(float(retry_after), 0.5), 6.0)
                except ValueError:
                    wait = 2.0
                log.warning("%s returned %s; retrying in %.1fs", model, resp.status_code, wait)
                time.sleep(wait)
                continue
            resp.raise_for_status()
            data = resp.json()
            if "error" in data:
                raise RuntimeError(str(data["error"])[:200])
            choice = (data.get("choices") or [{}])[0]
            msg = choice.get("message") or {}
            usage = data.get("usage") or {}
            meter.add(model, int(usage.get("prompt_tokens", 0)),
                      int(usage.get("completion_tokens", 0)))
            content = (msg.get("content") or "").strip()
            if content:
                self.last_model = model
                return content
            if attempt == 1 and choice.get("finish_reason") == "length":
                # reasoning spent the whole budget; give it room and less effort
                payload["max_tokens"] = max_tokens * 2
                payload["reasoning_effort"] = "low"
                continue
            raise RuntimeError(f"{model} returned no content")
        raise RuntimeError(f"{model} failed after retry")

    def _model_for(self, question: str) -> str:
        complex_q = self.assess_difficulty(question) == Difficulty.complex
        return self.s.ollama_model if complex_q else self.s.ollama_model_cheap

    # ------------------------------------------------------------------ #
    def assess_difficulty(self, question: str) -> Difficulty:
        # A cheap heuristic is enough to route; only reading needs a model.
        return self.stub.assess_difficulty(question)

    def extract_claims(self, question: str, pages: list[RetrievedPage],
                       meter: CostMeter) -> list[Claim]:
        if not pages:
            return []
        context = "\n\n".join(
            f"[{p.doc_id} p.{p.page_number}]\n{(p.text or '')[:_PAGE_CHARS]}" for p in pages)
        system = (
            "You read pages from SEC filings and extract atomic factual claims that "
            "answer the question. Use only what is printed on the pages. Restate "
            "printed figures with their period and units; never compute a number "
            "that is not printed. Each claim must carry a verbatim quote copied "
            "exactly from its page. If the pages do not answer the question, return "
            'an empty list. Respond with JSON only: {"claims":[{"text":"...",'
            '"doc_id":"...","page":0,"quote":"..."}]} with at most 6 claims.'
        )
        user = f"Question: {question}\n\nPages:\n{context}\n\nJSON only."
        try:
            # Medium effort even for lookups: at low effort gpt-oss sometimes
            # returned no claims for a page that plainly answered the question
            # (Meta's advertising revenue), and the pipeline then abstained.
            raw = self._chat(self._model_for(question), system, user, meter,
                             max_tokens=2500, effort="medium")
            data = _parse_json(raw)
            if data is None:
                raise ValueError("extractor did not return JSON")
        except Exception as exc:  # noqa: BLE001 — degrade, never fail the request
            log.warning("extraction fell back to the stub: %s", exc)
            return self.stub.extract_claims(question, pages, meter)

        by_key = {(p.doc_id, p.page_number): p for p in pages}
        claims: list[Claim] = []
        for c in (data.get("claims") or [])[:6]:
            if not isinstance(c, dict):
                continue
            try:
                key = (str(c.get("doc_id", "")), int(c.get("page", -1)))
            except (TypeError, ValueError):
                continue
            page = by_key.get(key)
            quote = str(c.get("quote", "")).strip()
            text = str(c.get("text", "")).strip()
            # a page it was never shown, or a quote that is not on the page,
            # means the claim cannot be traced — drop it here
            if page is None or not text or not quote_on_page(quote, page.text):
                continue
            bbox = self.stub._best_region(page, quote)
            region_id = next((r.region_id for r in page.regions if r.bbox is bbox), None)
            claims.append(Claim(
                text=text,
                citations=[Citation(doc_id=page.doc_id, page_number=page.page_number,
                                    bbox=bbox, region_id=region_id, snippet=quote[:240])],
                confidence=0.8,
            ))
        if not claims:
            # A second, deterministic reader. Even at temperature 0 the model
            # occasionally returns nothing for a page that plainly answers the
            # question. The stub proposes verbatim lines instead; they are
            # candidates only, and the model verifier must still agree with
            # each one before it can reach an answer.
            claims = self.stub.extract_claims(question, pages, meter)
            if claims:
                log.info("model found no claims; checking %d verbatim candidates", len(claims))
        return claims

    def verify_claims(self, question: str, claims: list[Claim],
                      pages: list[RetrievedPage],
                      meter: CostMeter) -> list[tuple[bool, float, str]]:
        """Verify every claim in one model call."""
        by_key = {(p.doc_id, p.page_number): p for p in pages}
        results: list[Optional[tuple[bool, float, str]]] = [None] * len(claims)
        ask: list[int] = []
        for i, c in enumerate(claims):
            cit = c.citations[0] if c.citations else None
            page = by_key.get((cit.doc_id, cit.page_number)) if cit else None
            if cit is None or page is None:
                results[i] = (False, 0.0, "cited page not in the retrieved set")
            elif not quote_on_page(cit.snippet or "", page.text):
                results[i] = (False, 0.1, "quoted text not found on the cited page")
            else:
                ask.append(i)
        if not ask:
            return results  # type: ignore[return-value]

        cited = sorted({(claims[i].citations[0].doc_id, claims[i].citations[0].page_number)
                        for i in ask})
        page_block = "\n\n".join(
            f"[{d} p.{n}]\n{(by_key[(d, n)].text or '')[:_VERIFY_CHARS]}" for d, n in cited)
        claim_block = "\n".join(
            f'{i}. "{claims[i].text}" — cites [{claims[i].citations[0].doc_id} '
            f'p.{claims[i].citations[0].page_number}]' for i in ask)
        system = (
            "You are a strict verifier for answers drawn from SEC filings. Judge each "
            "claim ONLY against the page it cites; never support a claim with another "
            "page, because its footnote will point to the page it cites. For each claim "
            "decide two things separately: supported — the cited page states it, with "
            "matching figures, periods and units; responsive — it helps answer the "
            "question. A true statement that does not address the question is not "
            "responsive. Default to false when uncertain. Respond "
            'with JSON only: {"verdicts":[{"i":0,"supported":true,"responsive":true,'
            '"confidence":0.0,"note":"..."}]}'
        )
        user = (f"Question: {question}\n\nClaims:\n{claim_block}\n\n"
                f"Cited pages:\n{page_block}\n\nJSON only.")
        try:
            data = _parse_json(self._chat(self.s.ollama_model_cheap, system, user, meter,
                                          max_tokens=2000))
            verdicts = {int(v["i"]): v for v in (data or {}).get("verdicts", [])
                        if isinstance(v, dict) and "i" in v}
        except Exception as exc:  # noqa: BLE001
            log.warning("verification fell back to the stub: %s", exc)
            verdicts = None

        for i in ask:
            if verdicts is None:  # model unavailable: fall back to the stub check
                results[i] = self.stub.verify_claim(question, claims[i], pages, meter)
                continue
            v = verdicts.get(i)
            if v is None:
                results[i] = (False, 0.0, "verifier gave no verdict")
                continue
            supported, responsive = bool(v.get("supported")), bool(v.get("responsive"))
            try:
                conf = max(0.0, min(1.0, float(v.get("confidence", 0.0))))
            except (TypeError, ValueError):
                conf = 0.0
            note = str(v.get("note", ""))[:160]
            if not supported:
                results[i] = (False, round(conf * 0.2, 3), note or "not supported by the page")
            elif not responsive:
                results[i] = (False, round(conf * 0.4, 3),
                              note or "on the page but does not answer the question")
            else:
                results[i] = (True, round(conf, 3), note or "agreed to the cited page")
        return results  # type: ignore[return-value]

    def verify_claim(self, question: str, claim: Claim, pages: list[RetrievedPage],
                     meter: CostMeter) -> tuple[bool, float, str]:
        return self.verify_claims(question, [claim], pages, meter)[0]

    def synthesize(self, question: str, claims: list[Claim],
                   meter: CostMeter) -> tuple[str, str]:
        verified = [c for c in claims if c.verified and c.citations]
        if not verified:
            return _ABSTAIN, self.last_model
        allowed = {(c.citations[0].doc_id, c.citations[0].page_number) for c in verified}
        evidence = "\n".join(
            f"- {c.text} [{c.citations[0].doc_id} p.{c.citations[0].page_number}]"
            for c in verified)
        system = (
            "Answer the question using ONLY the verified evidence below. Write one "
            "short lead sentence, then 1-4 lines that each begin with '- '. End every "
            "line with the citation of the evidence it uses, copied exactly, for "
            "example [NVDA-10-K-2026-02-25 p.50]. Introduce no number, name or claim "
            "that is not in the evidence. The lead sentence only summarises the lines "
            "below it: no causes, comparisons or judgements the evidence does not "
            "state. Plain text, no markdown headings or bold."
        )
        user = f"Question: {question}\n\nVerified evidence:\n{evidence}"
        model = self._model_for(question)
        try:
            raw = self._chat(model, system, user, meter, max_tokens=1500)
        except Exception as exc:  # noqa: BLE001
            log.warning("synthesis fell back to the stub: %s", exc)
            return self.stub.synthesize(question, claims, meter)

        def keep(m: re.Match) -> str:
            key = (m.group(1), int(m.group(2)))
            return f"[{key[0]} p.{key[1]}]" if key in allowed else ""

        lede: Optional[str] = None
        bullets: list[str] = []
        for line in raw.splitlines():
            line = line.strip().replace("**", "")
            if not line:
                continue
            if line[:1] in "*\u2022":
                line = "- " + line[1:].strip()
            cleaned = _CITE.sub(keep, line).rstrip()
            if cleaned.startswith("-"):
                # every statement must stay traceable; drop one left uncited
                if _CITE.search(cleaned):
                    bullets.append(cleaned)
            elif lede is None and not bullets:
                lede = cleaned
            # Any other unbulleted line is a statement without a citation —
            # models like to restate each bullet as its own lead sentence.
        if not bullets and lede and _CITE.search(lede):
            bullets, lede = ["- " + lede], None      # one cited sentence is an answer
        if not bullets:
            log.warning("synthesis kept no cited lines; using the stub's wording")
            return self.stub.synthesize(question, claims, meter)
        return "\n".join(([lede] if lede else []) + bullets), model
