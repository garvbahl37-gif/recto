"""Endpoints the reading interface depends on.

The console used to download every filing in full on load — about 4.5 MB for
thirteen filings — to look up section names. It now fetches the one page it
shows, and watches the pipeline as it runs instead of waiting on a spinner.
"""
import json

from fastapi.testclient import TestClient

from insightledger.api.main import _orchestrator, app

client = TestClient(app)


def setup_module():
    _orchestrator()             # builds the index these endpoints read, in any test order


def test_single_page_has_its_text_and_place_in_the_filing():
    r = client.get("/documents/ACME-10K-2024/pages/2")
    assert r.status_code == 200
    page = r.json()
    assert page["page_number"] == 2 and page["total_pages"] == 6
    assert "4,820" in page["text"]
    assert page["doc_id"] == "ACME-10K-2024" and page["title"]


def test_missing_page_is_404():
    assert client.get("/documents/ACME-10K-2024/pages/99").status_code == 404
    assert client.get("/documents/NOPE/pages/1").status_code == 404


def _events(resp):
    out = []
    for block in resp.text.split("\n\n"):
        data = [ln[5:].strip() for ln in block.splitlines() if ln.startswith("data:")]
        if data:
            out.append(json.loads("".join(data)))
    return out


def test_stream_reports_each_stage_then_the_answer():
    with client.stream("POST", "/query/stream", json={
            "question": "What was ACME's total revenue in fiscal 2024?",
            "doc_ids": ["ACME-10K-2024"]}) as resp:
        assert resp.status_code == 200
        assert resp.headers["content-type"].startswith("text/event-stream")
        resp.read()
        events = _events(resp)
    stages = [e["stage"] for e in events]
    for s in ("router", "retriever", "extractor", "verifier", "synthesizer", "answer"):
        assert s in stages, f"missing {s} in {stages}"
    assert stages[-1] == "answer"
    retrieved = next(e for e in events if e["stage"] == "retriever")
    assert retrieved["pages"] and "page_number" in retrieved["pages"][0]
    assert "4,820" in events[-1]["answer"]["text"]


def test_registry_is_read_once_until_the_file_changes():
    from insightledger import knowledge
    first = knowledge.load_registry()
    assert knowledge.load_registry() is first      # cached object, not a re-parse


def test_requests_too_large_to_answer_are_rejected_before_any_work():
    assert client.post("/query", json={"question": "x" * 2001}).status_code == 422
    assert client.post("/query", json={"question": "revenue?", "top_k": 500}).status_code == 422
    assert client.post("/query/stream", json={"question": "x" * 2001}).status_code == 422
