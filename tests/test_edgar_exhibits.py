"""Forms whose substance lives in exhibits are read with their exhibits.

An 8-K's main document is a cover page; the earnings release is Exhibit 99.1.
A Canadian 40-F attaches its annual report, MD&A and financial statements the
same way. Without the exhibits Recto indexed one page of boilerplate.
"""
from insightledger.ingestion.edgar import EdgarClient


class _R:
    def __init__(self, text="", data=None):
        self.text, self._data, self.content = text, data, text.encode()

    def json(self):
        return self._data


def _client(monkeypatch, files):
    c = EdgarClient()
    base = "https://www.sec.gov/Archives/edgar/data/789019/000119312526000001/"

    def get(url, **kw):
        if url.endswith("index.json"):
            return _R(data={"directory": {"item": [{"name": n} for n in files]}})
        name = url.rsplit("/", 1)[-1]
        return _R(text=files[name])
    monkeypatch.setattr(c, "_get", get)
    return c, {"cik": 789019, "accession": "000119312526000001", "primary_doc": "msft-8k.htm",
               "form": "8-K", "date": "2026-07-29", "name": "MICROSOFT CORP"}


def test_an_8k_is_read_with_its_press_release(monkeypatch):
    files = {
        "msft-8k.htm": "<html><body>FORM 8-K Item 2.02 Results of Operations. See Exhibit 99.1.</body></html>",
        "msft-ex991.htm": "<html><body>Revenue was $76.4 billion and increased 18%. Net income $27,233.</body></html>",
        "msft-ex101.htm": "<html><body>Indenture boilerplate</body></html>",
        "R1.htm": "<html><body>XBRL viewer page</body></html>",
    }
    c, meta = _client(monkeypatch, files)
    doc = c._build("MSFT", meta, max_pages=50)
    text = " ".join(p.text for p in doc.pages)
    assert "Revenue was $76.4 billion" in text, "Exhibit 99.1 was not read"
    assert "Indenture boilerplate" not in text and "XBRL viewer" not in text


def test_a_10k_does_not_go_looking_for_exhibits(monkeypatch):
    files = {"msft-8k.htm": "<html><body>Annual report text</body></html>"}
    c, meta = _client(monkeypatch, files)
    meta["form"] = "10-K"
    calls = []
    orig = c._get
    c._get = lambda url, **kw: (calls.append(url), orig(url))[1]
    c._build("MSFT", meta, max_pages=50)
    assert not any(u.endswith("index.json") for u in calls)


def test_a_40f_reads_its_annual_report_exhibits_but_not_the_boilerplate(monkeypatch):
    files = {
        "shop-20231231.htm": "<html><body>FORM 40-F cover</body></html>",
        "exhibit11annualinformation.htm": "<html><body>Annual Information Form: our business</body></html>",
        "exhibit13mdaq42023.htm": "<html><body>MD&A: revenue grew 26% to $7,060 million</body></html>",
        "shop-20231231_d2.htm": "<html><body>Consolidated financial statements: net income $132</body></html>",
        "exhibit231consentofpwcllp-.htm": "<html><body>Consent of auditors</body></html>",
        "exhibit311302soxceoandcfo-.htm": "<html><body>SOX certification</body></html>",
        "R7.htm": "<html><body>viewer</body></html>",
    }
    c, meta = _client(monkeypatch, files)
    meta.update(primary_doc="shop-20231231.htm", form="40-F")
    text = " ".join(p.text for p in c._build("SHOP", meta, max_pages=50).pages)
    for want in ("Annual Information Form", "revenue grew 26%", "financial statements"):
        assert want in text
    for unwanted in ("Consent of auditors", "SOX certification", "viewer"):
        assert unwanted not in text
