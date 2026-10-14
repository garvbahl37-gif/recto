r"""Multi-agent orchestration.

Graph shape (a real cycle — this is what separates it from a linear chain):

    router -> retriever -> extractor -> verifier --(low confidence)--> retriever
                                              \--(confident/exhausted)--> synthesizer

- router     : assess difficulty, size retrieval (complex questions fan wider).
- retriever  : visual MaxSim retrieval over page images.
- extractor  : VLM reads the pages, emits atomic claims with region citations.
- verifier   : each claim checked against its cited page; overall confidence.
- (loop)     : if confidence < threshold and budget remains, widen k and retry.
- synthesizer: compose the grounded answer from verified claims.

Runs under LangGraph when available; an equivalent linear runner otherwise.
Both drive the same node methods, so behaviour is identical.
"""
from __future__ import annotations

import time
from typing import Optional

from ..config import Settings, get_settings
from ..observability.cost import CostMeter
from ..observability.tracing import Tracer, get_tracer
from ..llm import Provider, get_provider
from ..retrieval import Retriever
from ..schemas import Answer, Citation, Difficulty, Query
from .state import ILState


def _emit(state: ILState, stage: str, **payload) -> None:
    """Report progress to a listener, if there is one. Never lets a broken
    listener take the pipeline down with it."""
    fn = state.get("emit")
    if fn is None:
        return
    try:
        fn(stage, payload)
    except Exception:  # noqa: BLE001
        pass


class Orchestrator:
    def __init__(self, retriever: Optional[Retriever] = None,
                 provider: Optional[Provider] = None,
                 settings: Optional[Settings] = None):
        self.s = settings or get_settings()
        self.retriever = retriever or Retriever(settings=self.s)
        self.provider = provider or get_provider(self.s)

    # ----------------------------- nodes ----------------------------------- #
    def route(self, state: ILState) -> ILState:
        tracer: Tracer = state["tracer"]
        with tracer.span("router", question=state["question"]):
            difficulty = self.provider.assess_difficulty(state["question"])
            base_k = state.get("top_k") or self.s.top_k
            # complex questions retrieve wider (more cross-page evidence)
            k = base_k + 3 if difficulty == Difficulty.complex else base_k
            state["difficulty"] = difficulty
            state["top_k"] = k
            state.setdefault("retries", 0)
            state["log"].append(f"router: difficulty={difficulty.value} top_k={k}")
        _emit(state, "router", difficulty=difficulty.value, top_k=k)
        return state

    def retrieve(self, state: ILState) -> ILState:
        tracer: Tracer = state["tracer"]
        with tracer.span("retriever", top_k=state["top_k"]):
            q = Query(text=state["question"], top_k=state["top_k"],
                      doc_ids=state.get("doc_ids"))
            state["retrieved"] = self.retriever.retrieve(q, tracer=tracer)
            state["log"].append(
                f"retriever: {len(state['retrieved'])} pages "
                f"[{', '.join(f'{p.doc_id} p.{p.page_number}' for p in state['retrieved'])}]")
        _emit(state, "retriever", pages=[
            {"doc_id": p.doc_id, "page_number": p.page_number, "score": round(p.score, 3)}
            for p in state["retrieved"]])
        return state

    def extract(self, state: ILState) -> ILState:
        tracer: Tracer = state["tracer"]
        with tracer.span("extractor"):
            claims = self.provider.extract_claims(
                state["question"], state["retrieved"], state["meter"])
            state["claims"] = claims
            state["log"].append(f"extractor: {len(claims)} candidate claims")
        _emit(state, "extractor", claims=len(claims))
        return state

    def verify(self, state: ILState) -> ILState:
        tracer: Tracer = state["tracer"]
        with tracer.span("verifier"):
            confs: list[float] = []
            claims = state["claims"]
            batch = getattr(self.provider, "verify_claims", None)
            if batch is not None and claims:
                # one model call for every claim, instead of one per claim
                verdicts = batch(state["question"], claims, state["retrieved"], state["meter"])
            else:
                verdicts = [self.provider.verify_claim(
                    state["question"], c, state["retrieved"], state["meter"]) for c in claims]
            for claim, (ok, conf, note) in zip(claims, verdicts):
                claim.verified = ok
                claim.confidence = conf
                claim.verifier_note = note
                if ok:
                    confs.append(conf)
            state["verified_confidence"] = max(confs) if confs else 0.0
            n_ok = sum(1 for c in state["claims"] if c.verified)
            state["log"].append(
                f"verifier: {n_ok}/{len(state['claims'])} grounded, "
                f"confidence={state['verified_confidence']:.2f}")
        _emit(state, "verifier", grounded=n_ok, total=len(state["claims"]),
              confidence=round(state["verified_confidence"], 3))
        return state

    def decide(self, state: ILState) -> str:
        """Conditional edge: retry retrieval or move to synthesis."""
        good = state.get("verified_confidence", 0.0) >= state["min_confidence"]
        exhausted = state.get("retries", 0) >= state["max_retries"]
        if good or exhausted:
            return "synthesize"
        state["retries"] = state.get("retries", 0) + 1
        state["top_k"] = state["top_k"] + 3   # widen the net on retry
        state["log"].append(
            f"decision: retry #{state['retries']} (widen to top_k={state['top_k']})")
        _emit(state, "retry", attempt=state["retries"], top_k=state["top_k"])
        return "retrieve"

    def synthesize(self, state: ILState) -> ILState:
        tracer: Tracer = state["tracer"]
        with tracer.span("synthesizer"):
            text, model = self.provider.synthesize(
                state["question"], state["claims"], state["meter"])
            citations: list[Citation] = []
            for c in state["claims"]:
                if c.verified:
                    citations.extend(c.citations)
            state["answer_text"] = text
            state["citations"] = citations
            state["model_used"] = model
            state["log"].append(f"synthesizer: model={model}, {len(citations)} citations")
        _emit(state, "synthesizer", model=model, citations=len(citations))
        return state

    # ----------------------------- runners --------------------------------- #
    def _initial_state(self, question: str, top_k: Optional[int],
                       doc_ids: Optional[list[str]], meter: CostMeter,
                       tracer: Tracer) -> ILState:
        return ILState(
            question=question, top_k=top_k or self.s.top_k, doc_ids=doc_ids,
            max_retries=1, min_confidence=self.s.verify_min_confidence,
            retries=0, meter=meter, tracer=tracer, log=[],
        )

    def _run_linear(self, state: ILState) -> ILState:
        state = self.route(state)
        while True:
            state = self.retrieve(state)
            state = self.extract(state)
            state = self.verify(state)
            if self.decide(state) == "synthesize":
                break
        return self.synthesize(state)

    def _run_langgraph(self, state: ILState) -> ILState:
        from langgraph.graph import END, START, StateGraph

        g = StateGraph(ILState)
        g.add_node("router", self.route)
        g.add_node("retriever", self.retrieve)
        g.add_node("extractor", self.extract)
        g.add_node("verifier", self.verify)
        g.add_node("synthesizer", self.synthesize)
        g.add_edge(START, "router")
        g.add_edge("router", "retriever")
        g.add_edge("retriever", "extractor")
        g.add_edge("extractor", "verifier")
        g.add_conditional_edges("verifier", self.decide,
                                {"retrieve": "retriever", "synthesize": "synthesizer"})
        g.add_edge("synthesizer", END)
        app = g.compile()
        return app.invoke(state, config={"recursion_limit": 25})

    def answer(self, question: str, top_k: Optional[int] = None,
               doc_ids: Optional[list[str]] = None, emit=None) -> Answer:
        meter = CostMeter()
        tracer = get_tracer()
        state = self._initial_state(question, top_k, doc_ids, meter, tracer)
        if emit is not None:
            state["emit"] = emit
        t0 = time.perf_counter()
        if self.s.resolve_graph() == "langgraph":
            try:
                state = self._run_langgraph(state)
            except Exception as exc:  # graceful fallback
                state["log"].append(f"langgraph failed ({exc!r}); using linear")
                state = self._run_linear(state)
        else:
            state = self._run_linear(state)
        latency_ms = (time.perf_counter() - t0) * 1000

        ans = Answer(
            query=question,
            text=state.get("answer_text", ""),
            citations=state.get("citations", []),
            claims=state.get("claims", []),
            confidence=state.get("verified_confidence", 0.0),
            difficulty=state.get("difficulty", Difficulty.simple),
            retrieved=state.get("retrieved", []),
            model_used=state.get("model_used", self.provider.name),
            retries=state.get("retries", 0),
            trace_id=tracer.trace_id,
            cost_usd=meter.total_usd,
            latency_ms=latency_ms,
        )
        tracer.flush(extra={
            "question": question, "backends": self.s.backend_report(),
            "confidence": ans.confidence, "cost_usd": ans.cost_usd,
            "latency_ms": round(latency_ms, 1), "log": state.get("log", []),
        })
        return ans


def answer_query(question: str, top_k: Optional[int] = None,
                 doc_ids: Optional[list[str]] = None,
                 settings: Optional[Settings] = None) -> Answer:
    return Orchestrator(settings=settings).answer(question, top_k=top_k, doc_ids=doc_ids)
