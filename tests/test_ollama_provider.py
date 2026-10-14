"""Ollama Cloud provider, exercised against a fake HTTP session (no network).

The model is untrusted input. Everything it returns is checked against the
pages we actually retrieved: claims citing pages we never showed it, quotes
that are not on the page, and citations the synthesizer invents are all
dropped before anything reaches the user.
"""
import json

import pytest

from insightledger.llm.ollama import OllamaProvider
from insightledger.observability.cost import CostMeter
from insightledger.schemas import Claim, Citation, RetrievedPage

DOC = "NVDA-10-K-2026-02-25"
PAGE_TEXT = ("Consolidated Statements of Income. Year Ended Jan 25, 2026 Jan 26, 2025. "
             "Revenue $ 165,218 $ 130,497. Cost of revenue 32,639 32,639.")


class _Resp:
    def __init__(self, payload, status=200):
        self._p, self.status_code = payload, status

    def json(self):
        return self._p

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(f"HTTP {self.status_code}")


class FakeSession:
    """Returns queued chat completions in order and records the requests."""

    def __init__(self, *contents, status=200, exc=None):
        self.queue, self.status, self.exc, self.sent = list(contents), status, exc, []

    def post(self, url, json=None, headers=None, timeout=None):
        if not self.sent or self.sent[-1] is not json:
            self.sent.append(json)
        if self.exc:
            raise self.exc
        content = self.queue.pop(0)
        return _Resp({"choices": [{"message": {"content": content}, "finish_reason": "stop"}],
                      "usage": {"prompt_tokens": 100, "completion_tokens": 20}}, self.status)


def _pages():
    return [RetrievedPage(doc_id=DOC, page_number=50, score=9.0, text=PAGE_TEXT)]


def _provider(session):
    from dataclasses import replace
    from insightledger.config import get_settings
    s = replace(get_settings(), ollama_api_key="test-key", ollama_model="big",
                ollama_model_cheap="small")
    return OllamaProvider(settings=s, session=session)


def test_extracts_claims_from_fenced_json():
    body = {"claims": [{"text": "NVIDIA's revenue was $165,218 million in fiscal 2026.",
                        "doc_id": DOC, "page": 50, "quote": "Revenue $ 165,218 $ 130,497"}]}
    sess = FakeSession("```json\n" + json.dumps(body) + "\n```")
    claims = _provider(sess).extract_claims("What was revenue?", _pages(), CostMeter())
    assert len(claims) == 1
    assert claims[0].citations[0].page_number == 50
    assert "165,218" in claims[0].citations[0].snippet


def _only_verbatim(claims):
    """Whatever survives must quote the page exactly — the model's invention
    is gone, though the deterministic reader may propose real lines."""
    from insightledger.llm.ollama import quote_on_page
    return all(quote_on_page(c.citations[0].snippet, PAGE_TEXT) for c in claims)


def test_drops_claims_citing_pages_the_model_never_saw():
    body = {"claims": [{"text": "Revenue was $1.", "doc_id": DOC, "page": 999,
                        "quote": "Revenue $ 165,218"}]}
    claims = _provider(FakeSession(json.dumps(body))).extract_claims(
        "What was revenue?", _pages(), CostMeter())
    assert all(c.text != "Revenue was $1." for c in claims)
    assert all(c.citations[0].page_number == 50 for c in claims) and _only_verbatim(claims)


def test_drops_claims_whose_quote_is_not_on_the_page():
    body = {"claims": [{"text": "Revenue was $999 billion.", "doc_id": DOC, "page": 50,
                        "quote": "Revenue $ 999,999"}]}
    claims = _provider(FakeSession(json.dumps(body))).extract_claims(
        "What was revenue?", _pages(), CostMeter())
    assert all("999" not in c.text for c in claims) and _only_verbatim(claims)


def _claim(text="NVIDIA's revenue was $165,218 million in fiscal 2026.",
           quote="Revenue $ 165,218 $ 130,497"):
    return Claim(text=text, citations=[Citation(doc_id=DOC, page_number=50, snippet=quote)])


def test_batch_verification_uses_one_call_for_all_claims():
    verdicts = {"verdicts": [
        {"i": 0, "supported": True, "responsive": True, "confidence": 0.95, "note": "matches"},
        {"i": 1, "supported": True, "responsive": False, "confidence": 0.9, "note": "off topic"},
    ]}
    sess = FakeSession(json.dumps(verdicts))
    out = _provider(sess).verify_claims("What was revenue?", [_claim(), _claim()],
                                        _pages(), CostMeter())
    assert len(sess.sent) == 1
    assert out[0][0] is True and out[0][1] == pytest.approx(0.95)
    assert out[1][0] is False, "a true but unresponsive claim must not verify"


def test_quote_missing_from_page_fails_without_trusting_the_model():
    verdicts = {"verdicts": [{"i": 0, "supported": True, "responsive": True,
                              "confidence": 1.0, "note": "looks right"}]}
    out = _provider(FakeSession(json.dumps(verdicts))).verify_claims(
        "What was revenue?", [_claim(quote="Revenue $ 1,000,000")], _pages(), CostMeter())
    assert out[0][0] is False


def test_synthesis_strips_citations_that_were_never_verified():
    text = ("NVIDIA's revenue rose.\n"
            f"- Revenue was $165,218 million [{DOC} p.50]\n"
            "- Margins were record highs [MADEUP-10-K-2026 p.7]")
    c = _claim(); c.verified = True
    out, model = _provider(FakeSession(text)).synthesize("What was revenue?", [c], CostMeter())
    assert f"[{DOC} p.50]" in out
    assert "MADEUP" not in out
    assert model in ("small", "big")


def test_network_failure_falls_back_to_the_stub():
    sess = FakeSession(exc=ConnectionError("down"))
    claims = _provider(sess).extract_claims("What was revenue in fiscal 2026?", _pages(), CostMeter())
    # the stub still finds the revenue line on the page
    assert claims and "165,218" in claims[0].text


def test_resolves_to_ollama_when_a_key_is_configured():
    from dataclasses import replace
    from insightledger.config import get_settings
    s = replace(get_settings(), llm="auto", anthropic_api_key="", ollama_api_key="k")
    assert s.resolve_llm() == "ollama"


def test_a_rate_limited_call_is_retried_once():
    class Flaky(FakeSession):
        def post(self, url, json=None, headers=None, timeout=None):
            self.sent.append(json)
            if len(self.sent) == 1:
                r = _Resp({}, status=429); r.headers = {"Retry-After": "0"}
                return r
            return super().post(url, json, headers, timeout)

    body = {"claims": [{"text": "Revenue was $165,218 million.", "doc_id": DOC, "page": 50,
                        "quote": "Revenue $ 165,218 $ 130,497"}]}
    sess = Flaky(json.dumps(body))
    claims = _provider(sess).extract_claims("What was revenue?", _pages(), CostMeter())
    assert len(sess.sent) == 2 and len(claims) == 1


def test_one_lead_sentence_then_cited_bullets_only():
    text = (f"Meta's advertising revenue was $196,175 million in 2025.\n"
            f"- $196,175 million in 2025. [{DOC} p.50]\n"
            f"Meta's advertising revenue was $160,633 million in 2024.\n"
            f"- $160,633 million in 2024. [{DOC} p.50]")
    c = _claim(); c.verified = True
    out, _ = _provider(FakeSession(text)).synthesize("What was revenue?", [c], CostMeter())
    lines = out.splitlines()
    assert sum(1 for ln in lines if not ln.startswith("-")) == 1, out
    assert sum(1 for ln in lines if ln.startswith("-")) == 2


def test_a_single_cited_sentence_becomes_a_bullet():
    c = _claim(); c.verified = True
    out, _ = _provider(FakeSession(f"Revenue was $165,218 million. [{DOC} p.50]")).synthesize(
        "What was revenue?", [c], CostMeter())
    assert out.startswith("- ") and f"[{DOC} p.50]" in out


def test_an_empty_model_read_falls_back_to_verbatim_candidates():
    """The model sometimes returns no claims for a page that answers the
    question; the deterministic reader proposes the verbatim line instead."""
    claims = _provider(FakeSession('{"claims": []}')).extract_claims(
        "What was revenue in fiscal 2026?", _pages(), CostMeter())
    assert claims and "165,218" in claims[0].text
    assert claims[0].verified is None, "candidates are unverified until the verifier agrees"


def test_a_lead_that_repeats_its_only_bullet_is_dropped():
    c = _claim(); c.verified = True
    text = ("Starbucks operated 40,990 stores.\n"
            f"- Starbucks operated 40,990 stores. [{DOC} p.50]")
    out, _ = _provider(FakeSession(text)).synthesize("How many stores?", [c], CostMeter())
    assert out.count("40,990") == 1, out


def test_the_same_statement_from_two_pages_is_one_line_with_two_footnotes():
    a = _claim(); a.verified = True
    b = _claim(); b.verified = True
    b.citations[0].page_number = 59
    text = (f"NVIDIA's revenue grew.\n- Total revenue was $215,938 million. [{DOC} p.50]\n"
            f"- Total revenue was $215,938 million. [{DOC} p.59]")
    out, _ = _provider(FakeSession(text)).synthesize("Revenue?", [a, b], CostMeter())
    assert out.count("215,938") == 1, out
    assert f"[{DOC} p.50]" in out and f"[{DOC} p.59]" in out, out


def test_a_verdict_that_agrees_but_echoes_a_zero_confidence_still_counts():
    c = _claim()
    verdict = json.dumps({"verdicts": [{"i": 0, "supported": True, "responsive": True,
                                        "confidence": 0.0, "note": "matches"}]})
    [(ok, conf, _)] = _provider(FakeSession(verdict)).verify_claims(
        "What was revenue?", [c], _pages(), CostMeter())
    assert ok and conf > 0.5, "a zero would send a grounded answer round the retry loop"
