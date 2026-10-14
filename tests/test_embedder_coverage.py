"""The stub embedder must see the whole page.

It used to keep only the first 512 tokens of each page. Live 10-K pages run to
~800 tokens, so 81% of pages were truncated and ~6,000 distinct words across
the corpus could never be retrieved — a fact sitting at the bottom of a page
was invisible no matter how the question was phrased.
"""
import warnings

import numpy as np

from insightledger.retrieval.embedder import StubEmbedder, maxsim
from insightledger.schemas import Page


def _page(text: str) -> Page:
    return Page(doc_id="T-1", page_number=1, text=text, regions=[])


def _long_page_with_tail(tail: str) -> str:
    filler = " ".join(f"w{i}" for i in range(900))   # 900 distinct tokens
    return f"{filler} {tail}"


def test_words_past_the_512th_token_are_retrievable():
    e = StubEmbedder()
    page = _page(_long_page_with_tail("zygomorphic"))
    hit = maxsim(e.embed_query("zygomorphic"), e.embed_page(page))
    # an exact token match contributes ~1.0 to MaxSim; a miss stays well below
    assert hit > 0.99


def test_duplicate_tokens_are_collapsed_without_changing_scores():
    e = StubEmbedder()
    text = "revenue revenue revenue grew grew sharply revenue"
    m = e.embed_page(_page(text))
    assert m.shape[0] == len({"revenue", "grew", "sharply"})
    full = np.vstack([e._matrix([t]) for t in text.split()])
    q = e.embed_query("how sharply did revenue grow")
    assert abs(maxsim(q, m) - maxsim(q, full)) < 1e-5   # MaxSim is max over rows


def test_maxsim_is_finite_and_quiet():
    """Realistically sized pages trip a spurious divide-by-zero flag in numpy's
    matmul on macOS Accelerate; the values are fine, so it must not surface."""
    e = StubEmbedder()
    q = e.embed_query("what was total revenue in fiscal 2026 and how did it change")
    d = e.embed_page(_page(_long_page_with_tail("Total revenue was $165,218 million.")))
    with warnings.catch_warnings():
        warnings.simplefilter("error", RuntimeWarning)
        s = maxsim(q, d)
    assert np.isfinite(s)
