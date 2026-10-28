"""Operator-confirmed clear zones (Twin.clear_zones): UNKNOWN -> FREE inside, OCC untouched, before the bound."""
from __future__ import annotations

import numpy as np
import pytest

from clearance_core import grid as cgrid
from clearance_core.grid import FREE, OCC, UNKNOWN
from clearance_core.schemas import ClearZone, Task, Twin
from jobs.sim.api import LiveSession, check_feasibility, rasterize_twin, run_batch
from jobs.sim.raster import apply_clear_zones, clear_zone_summary, layers_key, polygon_area_m2
from jobs.sim.twin_builder import RoomCanvas, make_twin, rect_robot

ROBOT = dict(width=0.5, length=0.6)
DOOR_ZONE = [(2.8, 1.4), (3.2, 1.4), (3.2, 2.6), (2.8, 2.6)]  # 0.48 m2, covers wall ends too


def pinch_twin(zones=(), p90=None, key="sites/unit/pinch") -> Twin:
    """Two rooms joined by a 1.0 m doorway (y 1.5..2.5) whose lower 0.75 m was never seen by the scan,
    leaving a 0.25 m gap - the real iPhone case."""
    c = RoomCanvas(6.0, 4.0, res=0.025)
    c.rect(2.9, 0.0, 3.1, 1.5)
    c.rect(2.9, 2.5, 3.1, 4.0)
    c.rect(2.9, 1.5, 3.1, 2.25, value=UNKNOWN)
    t = make_twin(c, key, [("A", 1.0, 2.0, 0.0, "dock"), ("B", 5.0, 2.0, 0.0, "dest")], p90=p90)
    return t.model_copy(update={"clear_zones": [ClearZone(id=f"C{i + 1}", polygon=z, confirmed_by="operator")
                                                for i, z in enumerate(zones)]})


def cell(g, x, y):
    r, c = g.world_to_cell(x, y)
    return int(g.data[int(r), int(c)])


def test_apply_clear_zones_unknown_to_free_only_inside():
    cells = np.full((40, 40), UNKNOWN, np.uint8)
    cells[:, :5] = OCC
    cells[:, 35:] = FREE
    cells[20, 20] = OCC  # measured obstacle inside the zone
    zone = ClearZone(id="C1", polygon=[(0.0, 0.0), (2.0, 0.0), (2.0, 2.0), (0.0, 2.0)])  # left half, 0.1 m cells
    out, n = apply_clear_zones(cells, [zone], 0.1, (0.0, 0.0))
    assert out is not cells and cells[25, 10] == UNKNOWN  # input untouched
    inside = np.zeros_like(cells, bool)
    inside[20:, :20] = True  # rows 20..39 are y < 2.0 (row 0 = max y)
    assert (out[(cells == OCC)] == OCC).all()
    assert (out[(cells == FREE)] == FREE).all()
    assert (out[inside & (cells == UNKNOWN)] == FREE).all()
    assert (out[~inside & (cells == UNKNOWN)] == UNKNOWN).all()
    assert n == int((inside & (cells == UNKNOWN)).sum())
    again, n2 = apply_clear_zones(out, [zone], 0.1, (0.0, 0.0))
    assert n2 == 0 and (again == out).all()  # idempotent
    assert apply_clear_zones(cells, [], 0.1, (0.0, 0.0)) == (cells, 0)


def test_degenerate_zone_is_ignored():
    cells = np.full((10, 10), UNKNOWN, np.uint8)
    out, n = apply_clear_zones(cells, [ClearZone(id="C1", polygon=[(0.0, 0.0), (1.0, 1.0)])], 0.1, (0.0, 0.0))
    assert n == 0 and (out == UNKNOWN).all()


def test_rasterize_opens_unseen_floor_and_keeps_walls():
    plain = rasterize_twin(pinch_twin())
    zoned = rasterize_twin(pinch_twin([DOOR_ZONE]))
    assert cell(plain, 3.0, 1.8) == UNKNOWN and cell(zoned, 3.0, 1.8) == FREE
    assert cell(zoned, 3.0, 1.0) == OCC and cell(zoned, 3.0, 1.45) == OCC  # wall inside the zone stays
    assert cell(zoned, 3.0, 2.55) == OCC
    changed = plain.data != zoned.data
    assert (plain.data[changed] == UNKNOWN).all() and (zoned.data[changed] == FREE).all()


def test_conservative_bound_still_dilates_real_obstacles():
    t = pinch_twin([DOOR_ZONE], p90=0.05)
    nom, con = rasterize_twin(t), rasterize_twin(t, "conservative")
    # 3 cm below the upper wall, inside the zone: free nominally, grown over by the conservative bound
    assert cell(nom, 3.0, 2.47) == FREE and cell(con, 3.0, 2.47) == OCC
    assert cell(con, 3.0, 2.0) == FREE  # the confirmed doorway centre stays open


def test_feasibility_through_a_pinch_only_the_zone_opens():
    task = Task(sequence=["A", "B"])
    robot = rect_robot(**ROBOT)
    f0 = check_feasibility(pinch_twin(), robot, task)
    assert f0.all_reachable is False
    f1 = check_feasibility(pinch_twin([DOOR_ZONE]), robot, task)
    assert f1.all_reachable is True
    assert f1.legs[0].narrowest_gap_m is None or f1.legs[0].narrowest_gap_m >= 0.5
    # determinism: same twin, same answer; zones are part of the cache key
    assert check_feasibility(pinch_twin([DOOR_ZONE]), robot, task).model_dump() == f1.model_dump()
    assert layers_key(pinch_twin(), "nominal", None, None) != layers_key(pinch_twin([DOOR_ZONE]), "nominal", None, None)


def test_zone_over_a_measured_obstacle_does_not_open_it():
    c = RoomCanvas(6.0, 4.0, res=0.025)
    c.rect(2.9, 0.0, 3.1, 4.0)  # solid measured wall
    t = make_twin(c, "sites/unit/wall", [("A", 1.0, 2.0, 0.0, "dock"), ("B", 5.0, 2.0, 0.0, "dest")])
    t = t.model_copy(update={"clear_zones": [ClearZone(id="C1", polygon=DOOR_ZONE)]})
    assert cell(rasterize_twin(t), 3.0, 2.0) == OCC
    assert check_feasibility(t, rect_robot(**ROBOT), Task(sequence=["A", "B"])).all_reachable is False


def test_batches_and_live_session_use_the_zones():
    t = pinch_twin([DOOR_ZONE])
    task = Task(sequence=["A", "B"])
    b = run_batch(t, rect_robot(**ROBOT), task, "none", "nominal", 2, 0, record_episodes=False)
    assert b.results.success_rate == 1.0
    b0 = run_batch(pinch_twin(), rect_robot(**ROBOT), task, "none", "nominal", 2, 0, record_episodes=False)
    assert b0.results.success_rate == 0.0
    s = LiveSession(t, rect_robot(**ROBOT), task, "none", 1)
    assert s.step()["robot"]["state"]


def test_clear_zone_summary():
    t = pinch_twin([DOOR_ZONE, [(0.5, 0.5), (1.0, 0.5), (1.0, 1.0)]])
    s = clear_zone_summary(t, cgrid.load(t.occupancy))
    assert s["count"] == 2 and s["confirmed_by"] == ["operator"] and s["ids"] == ["C1", "C2"]
    assert s["area_m2"] == pytest.approx(0.48 + 0.125, abs=1e-6)
    assert s["unseen_area_m2"] == pytest.approx(0.2 * 0.75, abs=0.02)
    assert clear_zone_summary(pinch_twin()) is None
    assert polygon_area_m2([(0, 0), (2, 0), (2, 2), (0, 2)]) == pytest.approx(4.0)
