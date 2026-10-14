"""Visual page embeddings with a *late-interaction* (multi-vector) interface.

Both backends return a matrix of token vectors per page/query and are scored
with MaxSim — the ColPali/ColQwen scoring function:

    score(q, d) = sum_i  max_j  <q_i, d_j>

- ColQwenEmbedder: real visual retrieval over rendered page images.
- StubEmbedder: deterministic hashing of page/query tokens into unit vectors,
  so MaxSim reduces to soft lexical late-interaction. No torch, fully runnable,
  and it genuinely retrieves the right pages for the golden set.
"""
from __future__ import annotations

import hashlib
import re
from typing import Optional, Protocol

import numpy as np

from ..config import Settings, get_settings
from ..schemas import Page

_DIM = 128
_TOK = re.compile(r"[A-Za-z0-9%$.\-]+")


def best_per_token(query: np.ndarray, doc: np.ndarray) -> np.ndarray:
    """For each query token, its best similarity to any document token (Tq,)."""
    if query.size == 0 or doc.size == 0:
        return np.zeros(max(len(query), 1), dtype=np.float32)
    # numpy 2.0 on macOS Accelerate raises a spurious divide-by-zero flag on
    # float32 matmul even though every value is finite. Silence the flag, then
    # check the result ourselves so a genuinely bad vector still cannot hide.
    with np.errstate(divide="ignore", over="ignore", invalid="ignore"):
        sim = query @ doc.T           # (Tq, Td)
    if not np.isfinite(sim).all():
        sim = np.nan_to_num(sim, nan=0.0, posinf=1.0, neginf=-1.0)
    return sim.max(axis=1)


def maxsim(query: np.ndarray, doc: np.ndarray) -> float:
    """Late-interaction MaxSim between two (T, D) multi-vector matrices."""
    if query.size == 0 or doc.size == 0:
        return 0.0
    return float(best_per_token(query, doc).sum())


# Hashed stub vectors only score ~1.0 against the identical token, so a best
# match above this means "the page contains this word".
EXACT_MATCH = 0.98


def idf_weights(best: np.ndarray) -> np.ndarray:
    """BM25-style weight per query token from a (pages, tokens) matrix of best
    matches. Words on most pages ("how", "did", a company's own name inside its
    filing) count for almost nothing; rare words decide the ranking. A word no
    page contains cannot tell pages apart, so it gets no weight at all."""
    n_pages = best.shape[0]
    have = (best > EXACT_MATCH).sum(axis=0).astype(np.float64)
    w = np.where(have > 0, np.log((n_pages - have + 0.5) / (have + 0.5) + 1.0), 0.0)
    return w if w.any() else np.ones_like(w)


class Embedder(Protocol):
    name: str
    dim: int

    def embed_page(self, page: Page) -> np.ndarray: ...
    def embed_query(self, text: str) -> np.ndarray: ...


def _hash_vec(token: str, dim: int) -> np.ndarray:
    h = hashlib.sha256(token.encode("utf-8")).digest()
    # expand digest deterministically to `dim` floats in [-1, 1]
    raw = np.frombuffer((h * ((dim // len(h)) + 1))[: dim * 2], dtype=np.uint8)[:dim]
    v = (raw.astype(np.float32) / 127.5) - 1.0
    n = np.linalg.norm(v)
    return v / n if n else v


class StubEmbedder:
    name = "stub"
    dim = _DIM

    def __init__(self) -> None:
        self._cache: dict[str, np.ndarray] = {}

    def _tokens(self, text: str) -> list[str]:
        return [t.lower() for t in _TOK.findall(text)]

    @staticmethod
    def _unique(tokens: list[str]) -> list[str]:
        """First occurrence of each token, in order.

        MaxSim scores a document by the *best* match for each query token, so
        repeated document tokens add nothing — collapsing them is lossless.
        This replaces a hard 512-token cap that silently dropped the tail of
        81% of live 10-K pages; a page's vocabulary is only ~260-370 words.
        """
        return list(dict.fromkeys(tokens))

    def _matrix(self, tokens: list[str]) -> np.ndarray:
        if not tokens:
            return np.zeros((1, self.dim), dtype=np.float32)
        rows = []
        for t in tokens:
            v = self._cache.get(t)
            if v is None:
                v = _hash_vec(t, self.dim)
                self._cache[t] = v
            rows.append(v)
        return np.vstack(rows)

    def embed_page(self, page: Page) -> np.ndarray:
        text = page.text or " ".join(r.text for r in page.regions)
        return self._matrix(self._unique(self._tokens(text)))

    def embed_query(self, text: str) -> np.ndarray:
        # Queries keep repeats: every query token is a separate term in the
        # MaxSim sum. Long pasted questions are clipped to stay cheap.
        return self._matrix(self._tokens(text)[:256])


class ColQwenEmbedder:
    """Real ColQwen2 late-interaction embeddings over page images."""
    name = "colqwen"

    def __init__(self, settings: Optional[Settings] = None) -> None:
        import torch  # lazy
        from transformers import AutoProcessor  # type: ignore

        try:
            from colpali_engine.models import ColQwen2  # type: ignore
        except Exception as exc:  # pragma: no cover
            raise RuntimeError(
                "colpali-engine not installed. `pip install colpali-engine`") from exc

        self.s = settings or get_settings()
        device = self.s.device
        if device == "auto":
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self.device = device
        self.torch = torch
        self.model = ColQwen2.from_pretrained(
            self.s.colqwen_model,
            torch_dtype=torch.bfloat16 if device == "cuda" else torch.float32,
        ).to(device).eval()
        self.processor = AutoProcessor.from_pretrained(self.s.colqwen_model)
        self.dim = getattr(self.model.config, "dim", 128)

    def _load_image(self, path: str):
        from PIL import Image  # type: ignore

        return Image.open(path).convert("RGB")

    def embed_page(self, page: Page) -> np.ndarray:
        if not page.image_path:
            # fall back to a text embedding via the stub to stay functional
            return StubEmbedder().embed_page(page)
        img = self._load_image(page.image_path)
        batch = self.processor.process_images([img]).to(self.device)
        with self.torch.no_grad():
            emb = self.model(**batch)
        return emb[0].float().cpu().numpy()

    def embed_query(self, text: str) -> np.ndarray:
        batch = self.processor.process_queries([text]).to(self.device)
        with self.torch.no_grad():
            emb = self.model(**batch)
        return emb[0].float().cpu().numpy()


def get_embedder(settings: Optional[Settings] = None) -> Embedder:
    s = settings or get_settings()
    try:
        if s.resolve_embedder() == "colqwen":
            return ColQwenEmbedder(s)
    except Exception:
        # missing deps / no GPU -> dependency-free stub (still fully local)
        pass
    return StubEmbedder()
