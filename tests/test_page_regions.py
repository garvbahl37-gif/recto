"""Regions are worked out from page text, never stored twice."""
import json
from dataclasses import replace

from insightledger import knowledge
from insightledger.config import get_settings
from insightledger.ingestion import build_document_from_pages, detect_regions
from insightledger.schemas import Document, Page

TEXT = "CONSOLIDATED STATEMENTS\n\nNet income $ 58,471 $ 29,760\n\nSigned /s/ Jensen Huang"


def test_regions_follow_the_text():
    page = Page(doc_id="N-10-K", page_number=1, text=TEXT)
    assert [r.text for r in page.regions] == [r.text for r in detect_regions(TEXT)]
    assert [r.region_id for r in page.regions] == [r.region_id for r in detect_regions(TEXT)]


def test_a_stored_copy_is_ignored_on_load():
    stale = {"doc_id": "N-10-K", "page_number": 1, "text": TEXT,
             "regions": [{"region_id": "old", "type": "text",
                          "bbox": {"x0": 0, "y0": 0, "x1": 1, "y1": 1}, "text": "gone"}]}
    page = Page.model_validate(stale)
    assert "old" not in [r.region_id for r in page.regions]
    assert len(page.regions) == 3


def test_the_registry_is_saved_without_regions_and_the_api_still_gets_them(tmp_path):
    s = replace(get_settings(), data_dir=tmp_path)
    doc = build_document_from_pages("N-10-K-2025-01-01", "NVIDIA CORP", [TEXT, "Page two"])
    knowledge.save_registry([doc], s)
    raw = json.loads((s.index_dir / "doc_registry.json").read_text())
    assert all("regions" not in p for p in raw["N-10-K-2025-01-01"]["pages"])
    loaded = knowledge.load_registry(s)["N-10-K-2025-01-01"]
    assert len(loaded.pages[0].regions) == 3
    dumped = loaded.model_dump(mode="json")
    assert len(dumped["pages"][0]["regions"]) == 3, "the document endpoint still serves regions"
    assert isinstance(Document.model_validate(dumped), Document)
