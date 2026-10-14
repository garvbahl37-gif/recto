"""Every API path the frontend calls is proxied by Vercel to the API.

The frontend is served from Vercel and calls the API with relative URLs, so a
path missing from vercel.json's rewrites works locally and returns 404 in
production. That happened to /companies.
"""
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def test_every_api_path_the_frontend_calls_is_rewritten_to_the_api():
    api = (ROOT / "frontend/src/api.js").read_text()
    called = {m.split("?")[0] for m in re.findall(r"""["'`](/[a-z][a-z/]*)""", api)}
    rewrites = json.loads((ROOT / "vercel.json").read_text())["rewrites"]
    proxied = [r["source"] for r in rewrites if r["destination"].startswith("https://")]
    assert called, "no API paths found in api.js"
    for path in called:
        # /documents/${id}/pages/${n} is written as a template; its literal
        # prefix is matched by the /documents/:id... sources
        assert any(src == path or src.startswith(path.rstrip("/") + "/:") or src == path.rstrip("/")
                   for src in proxied), f"{path} is not proxied by vercel.json"
