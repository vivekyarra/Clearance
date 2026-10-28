import shutil

import pytest

from clearance_core import storage


@pytest.fixture(autouse=True)
def data_dir(tmp_path, monkeypatch):
    """Each test gets its own storage root."""
    monkeypatch.setattr(storage, "DATA", tmp_path / "data")
    return tmp_path / "data"


@pytest.fixture(scope="session")
def small_capture_src(tmp_path_factory):
    """A short T1 capture of the empty golden room (no limited-tracking bursts), rendered once."""
    from jobs.reference.render import render_capture
    from jobs.reference.rooms import make_room

    out = tmp_path_factory.mktemp("src") / "cap"
    render_capture(make_room("golden_empty", 0, save=False), str(out), "T1_LIDAR", seed=5, max_frames=70,
                   noise={"limited_bursts": 0})
    return out


@pytest.fixture
def small_capture(small_capture_src, tmp_path):
    """A private copy of small_capture_src that a test may damage."""
    dst = tmp_path / "cap"
    shutil.copytree(small_capture_src, dst)
    return dst
