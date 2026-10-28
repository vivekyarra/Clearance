import pytest

from clearance_core import storage


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """Each test gets its own storage root."""
    monkeypatch.setattr(storage, "DATA", tmp_path / "data")
    return tmp_path / "data"
