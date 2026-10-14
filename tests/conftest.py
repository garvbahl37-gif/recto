import os
import tempfile
from pathlib import Path

import pytest


@pytest.fixture(scope="session", autouse=True)
def _isolated_data(tmp_path_factory):
    """Force stub backends + a throwaway data dir so tests are hermetic."""
    d = tmp_path_factory.mktemp("il_data")
    os.environ["IL_DATA_DIR"] = str(d)
    os.environ["IL_EMBEDDER"] = "stub"
    os.environ["IL_VECTOR_STORE"] = "memory"
    os.environ["IL_LLM"] = "stub"
    os.environ["IL_BOOTSTRAP"] = "sample"   # offline fixture, no EDGAR network
    os.environ["IL_AUTO_FETCH"] = "0"       # never reach EDGAR from a test
    # clear cached settings
    from insightledger.config import get_settings
    get_settings.cache_clear()
    yield
