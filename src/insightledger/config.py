"""Central configuration. Reads environment (and optional .env) once.

Every setting has a safe default so the whole system runs on stub backends with
nothing configured. `resolve_*` helpers turn the `auto` selectors into a
concrete backend name given what is actually installed / credentialed.
"""
from __future__ import annotations

import importlib.util
import os
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path


def _load_dotenv() -> None:
    """Best-effort .env loader (no hard dependency on python-dotenv)."""
    try:
        from dotenv import load_dotenv  # type: ignore

        load_dotenv()
        return
    except Exception:
        pass
    # Minimal fallback parser.
    env_path = Path.cwd() / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            os.environ.setdefault(k.strip(), v.strip())


def _has(module: str) -> bool:
    return importlib.util.find_spec(module) is not None


@dataclass
class Settings:
    # Backend selectors
    embedder: str = "auto"
    vector_store: str = "auto"
    llm: str = "auto"
    graph: str = "auto"

    # Claude (optional — the app runs fully local on the stub without any key)
    anthropic_api_key: str = ""
    llm_model: str = "claude-opus-4-8"
    llm_model_cheap: str = "claude-haiku-4-5-20251001"

    # Ollama Cloud (OpenAI-compatible). Kimi models need paid credits on
    # ollama.com; gpt-oss is the strongest model on the free tier.
    ollama_api_key: str = ""
    ollama_base_url: str = "https://ollama.com/v1"
    ollama_model: str = "gpt-oss:120b"          # reasoning questions
    ollama_model_cheap: str = "gpt-oss:20b"     # lookups, verification
    llm_timeout: float = 90.0                   # seconds per model call

    # ColQwen (visual page embeddings)
    colqwen_model: str = "vidore/colqwen2-v1.0"
    device: str = "auto"

    # Qdrant
    qdrant_url: str = "http://localhost:6333"
    qdrant_api_key: str = ""

    # EDGAR
    edgar_user_agent: str = "Recto research example@example.com"
    # Thirty large filers across tech, finance, consumer, health and energy,
    # read whole: about 4,000 pages, ~200 MB resident with the index loaded,
    # inside Render free tier's 512 MB with room for fetched filings.
    seed_tickers: str = "AAPL,MSFT,NVDA,GOOGL,AMZN,META,TSLA,NFLX,ORCL,AVGO,AMD,CRM,JPM,BAC,GS,V,MA,BRK-B,WMT,COST,KO,PEP,PG,MCD,NKE,DIS,JNJ,LLY,UNH,CVX"
    seed_form: str = "10-K"
    # Ask about a company that is not indexed and Recto fetches its latest
    # annual report from EDGAR before answering. Fetched filings beyond the
    # budget are evicted least-recently-used first; seeds never are.
    auto_fetch: bool = True
    # Write fetched filings to disk. Off on Render, whose disk resets to the
    # image on restart anyway, so the write was pure cost on a slow CPU. Seeds
    # are always written: the Docker build bakes them into the image.
    persist_fetched: bool = True
    max_fetched: int = 32
    # Anyone can ask a question, so EDGAR fetches share one allowance per ten
    # minutes across the whole service (question-triggered and POST /ingest).
    fetch_rate: int = 20
    bootstrap: str = "live"                # live (EDGAR) | sample (fixture, offline)

    # Retrieval / routing
    top_k: int = 5
    verify_min_confidence: float = 0.6
    data_dir: Path = field(default_factory=lambda: Path("./data"))

    # Observability
    trace: bool = True

    # ---- resolved backend names (computed) ----
    def resolve_embedder(self) -> str:
        if self.embedder != "auto":
            return self.embedder
        # visual ColQwen if the full stack is present; else the dependency-free
        # stub (hash multi-vector + MaxSim). No API either way.
        if _has("torch") and _has("transformers") and _has("colpali_engine"):
            return "colqwen"
        return "stub"

    def resolve_vector_store(self) -> str:
        if self.vector_store != "auto":
            return self.vector_store
        return "qdrant" if _has("qdrant_client") else "memory"

    def resolve_llm(self) -> str:
        if self.llm != "auto":
            return self.llm
        # Fully local by default: the deterministic grounded stub. A configured
        # key opts into a real model — Claude first, then Ollama Cloud.
        if _has("anthropic") and self.anthropic_api_key:
            return "claude"
        if self.ollama_api_key:
            return "ollama"
        return "stub"

    def resolve_graph(self) -> str:
        if self.graph != "auto":
            return self.graph
        return "langgraph" if _has("langgraph") else "linear"

    def backend_report(self) -> dict[str, str]:
        return {
            "embedder": self.resolve_embedder(),
            "vector_store": self.resolve_vector_store(),
            "llm": self.resolve_llm(),
            "graph": self.resolve_graph(),
        }

    # ---- derived paths ----
    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def filings_dir(self) -> Path:
        return self.data_dir / "filings"

    @property
    def index_dir(self) -> Path:
        return self.data_dir / "index"

    @property
    def traces_dir(self) -> Path:
        return self.data_dir / "traces"

    @property
    def golden_dir(self) -> Path:
        return self.data_dir / "golden"

    def ensure_dirs(self) -> None:
        for p in (self.cache_dir, self.filings_dir, self.index_dir,
                  self.traces_dir, self.golden_dir):
            p.mkdir(parents=True, exist_ok=True)


def _env(name: str, default: str) -> str:
    return os.environ.get(name, default)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    _load_dotenv()
    return Settings(
        embedder=_env("IL_EMBEDDER", "auto"),
        vector_store=_env("IL_VECTOR_STORE", "auto"),
        llm=_env("IL_LLM", "auto"),
        graph=_env("IL_GRAPH", "auto"),
        anthropic_api_key=_env("ANTHROPIC_API_KEY", ""),
        llm_model=_env("IL_LLM_MODEL", "claude-opus-4-8"),
        llm_model_cheap=_env("IL_LLM_MODEL_CHEAP", "claude-haiku-4-5-20251001"),
        ollama_api_key=_env("OLLAMA_API_KEY", ""),
        ollama_base_url=_env("OLLAMA_BASE_URL", "https://ollama.com/v1"),
        ollama_model=_env("IL_OLLAMA_MODEL", "gpt-oss:120b"),
        ollama_model_cheap=_env("IL_OLLAMA_MODEL_CHEAP", "gpt-oss:20b"),
        llm_timeout=float(_env("IL_LLM_TIMEOUT", "90")),
        colqwen_model=_env("IL_COLQWEN_MODEL", "vidore/colqwen2-v1.0"),
        device=_env("IL_DEVICE", "auto"),
        qdrant_url=_env("QDRANT_URL", "http://localhost:6333"),
        qdrant_api_key=_env("QDRANT_API_KEY", ""),
        edgar_user_agent=_env("IL_EDGAR_USER_AGENT", "Recto research example@example.com"),
        seed_tickers=_env("IL_SEED_TICKERS", "AAPL,MSFT,NVDA,GOOGL,AMZN,META,TSLA,NFLX,ORCL,AVGO,AMD,CRM,JPM,BAC,GS,V,MA,BRK-B,WMT,COST,KO,PEP,PG,MCD,NKE,DIS,JNJ,LLY,UNH,CVX"),
        seed_form=_env("IL_SEED_FORM", "10-K"),
        auto_fetch=_env("IL_AUTO_FETCH", "1") == "1",
        persist_fetched=_env("IL_PERSIST_FETCHED", "1") == "1",
        max_fetched=int(_env("IL_MAX_FETCHED", "32")),
        fetch_rate=int(_env("IL_FETCH_RATE", "20")),
        bootstrap=_env("IL_BOOTSTRAP", "live"),
        top_k=int(_env("IL_TOP_K", "5")),
        verify_min_confidence=float(_env("IL_VERIFY_MIN_CONFIDENCE", "0.6")),
        data_dir=Path(_env("IL_DATA_DIR", "./data")),
        trace=_env("IL_TRACE", "1") == "1",
    )
