import pytest

from clearance_core import storage


@pytest.fixture(autouse=True)
def isolated_usage(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA", tmp_path)
    monkeypatch.setenv("CLEARANCE_USAGE_DB", str(tmp_path / "usage.db"))
    for k in ("CLEARANCE_LLM_MODE", "NEBIUS_API_KEY", "CLEARANCE_VISION_BASE_URL", "CLEARANCE_VISION_API_KEY"):
        monkeypatch.delenv(k, raising=False)
    return tmp_path
