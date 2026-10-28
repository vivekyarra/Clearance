import numpy as np
import pytest

from clearance_core import grid as cgrid
from clearance_core.grid import FREE, OCC, UNKNOWN, Grid
from clearance_core.schemas import Twin
from jobs.recon.measure import aisle_passable, evaluate, evaluate_grid, measure_gap
from jobs.reference.geometry import Solid
from jobs.reference.rooms import rasterize


def _grid(solids, W=4.0, D=3.0):
    return rasterize(solids, W, D, [])


def test_gap_between_two_boxes_subcell():
    a = Solid("A", "box", "b", 1.0, 1.5, 1.0, 3.0, 0, 1)      # x in [0.5, 1.5]
    b = Solid("B", "box", "b", 2.5373, 1.5, 1.0, 3.0, 0, 1)   # x in [2.0373, 3.0373]
    g = _grid([a, b])
    r = measure_gap(g, (1.5, 1.5), (2.0373, 1.5))
    assert r["ok"]
    assert r["value_m"] == pytest.approx(0.5373, abs=0.026)


@pytest.mark.parametrize("yaw", [0.0, 0.3, -0.7])
def test_rotated_corridor_width(yaw):
    c, s = np.cos(yaw), np.sin(yaw)
    nrm = np.array([-s, c])
    ctr = np.array([2.0, 1.5])
    width = 0.83
    walls = [Solid(f"W{k}", "box", "wall", *(ctr + k * nrm * (width / 2 + 0.25)), 6.0, 0.5, 0, 2, yaw) for k in (-1, 1)]
    g = _grid(walls)
    r = measure_gap(g, ctr - nrm * 0.4, ctr + nrm * 0.4)
    assert r["value_m"] == pytest.approx(width, abs=0.03)


def test_blocked_midpoint_and_unknown_counts_as_blocked():
    g = Grid(np.full((40, 40), FREE, np.uint8), 0.025, (0.0, 0.0))
    g.data[:, 20] = OCC
    r = measure_gap(g, (0.4, 0.5), (0.6, 0.5))
    assert r["value_m"] == 0.0 and not r["ok"]
    g.data[:, 20] = FREE
    g.data[:, 30] = UNKNOWN
    g.data[:, 5] = OCC
    r = measure_gap(g, (0.2, 0.5), (0.7, 0.5))
    assert r["value_m"] == pytest.approx(0.75 - 0.15, abs=0.001)
    with pytest.raises(ValueError):
        measure_gap(g, (0.2, 0.5), (0.2, 0.5))


def test_open_ended_run_is_flagged():
    g = Grid(np.full((10, 200), FREE, np.uint8), 0.025, (0.0, 0.0))
    r = measure_gap(g, (2.0, 0.1), (2.2, 0.1), extend_m=0.5)
    assert not r["ok"]


def test_evaluate_twin_reads_grid_from_storage():
    a = Solid("A", "box", "b", 1.0, 1.5, 1.0, 3.0, 0, 1)
    g = _grid([a])
    occ = cgrid.save(g, "t/occ.png")
    twin = Twin(twin_id="t", site_id="s", occupancy=occ)
    gts = [{"name": "box_to_east_wall", "a_xy": (1.5, 1.0), "b_xy": (4.0, 1.0), "value_m": 2.5},
           {"name": "west_wall_to_box", "a_xy": (0.0, 2.0), "b_xy": (0.5, 2.0), "value_m": 0.5}]
    ev = evaluate(twin, gts)
    assert ev["n"] == 2 and ev["median_abs_err_m"] < 0.013 and ev["p90_abs_err_m"] < 0.026
    ev2 = evaluate_grid(g, gts[:1] + [{**gts[1], "value_m": 0.6}])
    assert ev2["max_abs_err_m"] == pytest.approx(0.1, abs=0.013)


def test_aisle_passable_and_narrowed():
    sA = Solid("A", "box", "shelf", 2.0, 0.6, 3.0, 0.6, 0, 2)
    sB = Solid("B", "box", "shelf", 2.0, 1.7, 3.0, 0.6, 0, 2)   # aisle y in [0.9, 1.4] = 0.5 m
    g = _grid([sA, sB])
    ok = aisle_passable(g, (0.5, 1.15), (3.5, 1.15), 0.5)
    assert ok["passable"] and ok["centerline_free"]
    box = Solid("C", "box", "box", 2.0, 1.0, 0.2, 0.2, 0, 1)    # intrudes 0.1 m into the aisle
    bad = aisle_passable(rasterize([sA, sB, box], 4.0, 3.0, []), (0.5, 1.15), (3.5, 1.15), 0.5)
    assert not bad["passable"]
