import pytest

from clearance_core import storage


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """Every test writes its reference room into its own storage root."""
    monkeypatch.setattr(storage, "DATA", tmp_path)
    return tmp_path
