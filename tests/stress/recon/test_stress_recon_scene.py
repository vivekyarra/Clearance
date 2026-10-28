"""A scan of a room with things reference rooms do not have: a person standing in the scan who then
leaves, a glass panel and a black rug that return no depth, and a thin pole."""
import numpy as np
import pytest

from clearance_core.grid import FREE, OCC
from jobs.recon.measure import evaluate_grid
from jobs.recon.pipeline import reconstruct
from jobs.reference.geometry import Solid
from jobs.reference.render import plan_trajectory, render_capture
from jobs.reference.rooms import make_room


@pytest.fixture(scope="module")
def scene(tmp_path_factory):
    room = make_room("golden_empty", 0, save=False)
    n = len(plan_trajectory(room, np.random.default_rng(10_000))[0])
    person = Solid("PERSON", "box", "person", 1.5, 2.0, 0.4, 0.3, 0.0, 1.7)
    room.solids += [Solid("GLASS", "box", "glass", 4.7, 2.7, 0.8, 0.06, 0.0, 2.0),
                    Solid("POLE", "cyl", "pole", 3.0, 3.0, 0.04, 0.04, 0.0, 2.0),
                    Solid("RUG", "box", "rug", 3.0, 1.0, 0.6, 0.4, 0.0, 0.01)]
    cap = render_capture(room, str(tmp_path_factory.mktemp("scene") / "cap"), "T1_LIDAR", 0,
                         noise={"limited_bursts": 0}, transients=[(person, 0, n // 3)],
                         no_depth_ids=["GLASS", "RUG"])
    return room, reconstruct(cap)


def _cells(g, x0, y0, x1, y1):
    xs = np.arange(x0, x1 + 1e-9, 0.025)
    ys = np.arange(y0, y1 + 1e-9, 0.025)
    X, Y = np.meshgrid(xs, ys)
    r, c = g.world_to_cell(X.ravel(), Y.ravel())
    return g.data[r, c]


def test_person_who_left_does_not_become_a_wall(scene):
    _, r = scene
    g = r["grid"]
    cells = _cells(g, 1.32, 1.87, 1.68, 2.13)
    assert (cells == FREE).mean() > 0.95, np.unique(cells, return_counts=True)
    assert r["stats"]["transient_refusion"]


def test_glass_without_depth_is_never_free(scene):
    _, r = scene
    assert not (_cells(r["grid"], 4.32, 2.69, 5.08, 2.71) == FREE).any()


def test_thin_pole_survives_speckle_cleanup(scene):
    _, r = scene
    g = r["grid"]
    assert (_cells(g, 2.99, 2.99, 3.01, 3.01) == OCC).all()


def test_unseen_floor_is_reported_for_rescan(scene):
    _, r = scene
    regs = r["stats"]["unknown_regions"]
    rug = [q for q in regs if abs(q["centroid_xy"][0] - 3.0) < 0.15 and abs(q["centroid_xy"][1] - 1.0) < 0.15]
    assert rug and rug[0]["area_m2"] == pytest.approx(0.24, rel=0.35) and rug[0]["next_to_free"]


def test_room_distances_stay_accurate(scene):
    room, r = scene
    keep = [m for m in room.gt_measurements if m["name"] != "room_depth_x3"]  # crosses the rug and the pole
    ev = evaluate_grid(r["grid"], keep)
    assert ev["median_abs_err_m"] <= 0.03, ev
