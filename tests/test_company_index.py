"""Every EDGAR company is findable: listed ones A to Z, every filer by search."""
import mmap
from dataclasses import replace

from fastapi.testclient import TestClient

from insightledger.api import main
from insightledger.config import get_settings
from insightledger.ingestion.edgar import EdgarClient

client = TestClient(main.app)

LISTED = [
    {"ticker": "GOOGL", "title": "Alphabet Inc.", "cik": 1652044},
    {"ticker": "GOOG", "title": "Alphabet Inc.", "cik": 1652044},
    {"ticker": "MMM", "title": "3M CO", "cik": 66740},
    {"ticker": "TTD", "title": "The Trade Desk, Inc.", "cik": 1671933},
    {"ticker": "AAPL", "title": "Apple Inc.", "cik": 320193},
] + [{"ticker": f"Z{i:03d}", "title": f"Zeta Fund {i:03d} Trust", "cik": 900000 + i} for i in range(60)]


def test_the_index_has_one_row_per_company_filed_a_to_z(monkeypatch):
    monkeypatch.setattr(EdgarClient, "_companies", LISTED)
    monkeypatch.setattr(main, "_DIRECTORY", None)
    r = client.get("/companies", headers={"Accept-Encoding": "gzip"})
    assert r.status_code == 200
    assert r.headers.get("content-encoding") == "gzip"
    d = r.json()
    assert d["count"] == 64, "share classes count once"
    sections = dict(d["sections"])
    assert [k for k, _ in d["sections"]] == ["#", "A", "T", "Z"]
    assert [row[0] for row in sections["A"]] == ["Alphabet Inc.", "Apple Inc."]
    assert sections["A"][0][1] == "GOOGL GOOG", "primary ticker first"
    assert sections["T"][0][0] == "The Trade Desk, Inc.", "a leading The is ignored"


def _filers(tmp_path, monkeypatch):
    s = replace(get_settings(), data_dir=tmp_path)
    (tmp_path / "cache").mkdir()
    (tmp_path / "cache" / "cik-lookup-data.txt").write_text(
        "ACME WIDGETS LLC:0000000042:\nAPPLE INC:0000320193:\nPINEAPPLE HOLDINGS:0000000099:\n",
        encoding="latin-1")
    monkeypatch.setattr(EdgarClient, "_entities", None)
    monkeypatch.setattr(EdgarClient, "_companies", [])
    return EdgarClient(s)


def test_every_filer_is_searched_on_disk_not_parsed_into_memory(tmp_path, monkeypatch):
    c = _filers(tmp_path, monkeypatch)
    hits = c.search_companies("apple", limit=5)
    assert [(h["cik"], h["title"]) for h in hits] == [(320193, "Apple Inc"), (99, "Pineapple Holdings")]
    assert hits[0]["ticker"] == "CIK320193"
    assert isinstance(EdgarClient._entities, mmap.mmap)


def test_a_search_never_matches_inside_a_cik(tmp_path, monkeypatch):
    c = _filers(tmp_path, monkeypatch)
    assert c.search_companies("0320193", limit=5) == []
