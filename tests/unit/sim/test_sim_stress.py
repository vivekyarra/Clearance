"""Stress and edge cases for the simulator."""
import math
import time

import numpy as np
import pytest

from clearance_core.schemas import Task
from jobs.sim.api import check_feasibility, run_batch
from jobs.sim.cspace import build_cspace, make_kernels
from jobs.sim.episode import make_context, run_episode
from jobs.sim.twin_builder import RoomCanvas, box, make_twin, rect_robot

ROBOT = dict(width=0.5, length=0.6)


def busy_room(ppm_peak=120.0):
    c = RoomCanvas(10.0, 6.0, res=0.025)
    c.rect(4.9, 0, 5.1, 2.0)
    c.rect(4.9, 4.0, 5.1, 6)
    zones = [("W", [(0.3, 0.3), (1.6, 0.3), (1.6, 5.7), (0.3, 5.7)]),
             ("E", [(8.4, 0.3), (9.7, 0.3), (9.7, 5.7), (8.4, 5.7)])]
    return make_twin(c, "sites/stress/busy", [("A", 2.5, 3.0, 0.0, "dock"), ("B", 7.5, 3.0, 0.0, "dest")],
                     primitives=[box("T1", 3.0, 1.2, 0.8, 0.8)], zones=zones,
                     od_pairs=[("W", "E", 1.0), ("E", "W", 1.0)],
                     persons_per_min={"quiet": 2, "normal": 8, "peak": ppm_peak})


def test_thirty_pedestrians_peak_density_terminates():
    twin = busy_room()
    ctx = make_context(twin, rect_robot(**ROBOT), Task(sequence=["A", "B", "A"]), "peak", "nominal")
    t0 = time.perf_counter()
    peaks = []
    for ep in range(3):
        s, lines = run_episode(ctx, ep, 900 + ep, None, record=True)
        assert s["outcome"] in ("success", "blocked_timeout", "collision")
        assert s["elapsed_s"] <= 3.0 * s["nominal_time_s"] + 0.2 or s["elapsed_s"] <= 10.2
        peaks.append(s["peds_peak"])
        assert len(lines) >= 2
    assert max(peaks) >= 25
    assert time.perf_counter() - t0 < 60


def test_robot_starting_next_to_wall_facing_it():
    c = RoomCanvas(6.0, 4.0, res=0.025)
    # dock 0.2 m from the east wall, facing the wall: the footprint must rotate away first
    twin = make_twin(c, "sites/stress/wall", [("D", 5.65, 2.0, 0.0, "dock"), ("B", 1.0, 2.0, 0.0, "dest")])
    f = check_feasibility(twin, rect_robot(**ROBOT), Task(sequence=["D", "B"]))
    assert f.all_reachable is True
    b = run_batch(twin, rect_robot(**ROBOT), Task(sequence=["D", "B"]), "none", "nominal", 2, 0,
                  record_episodes=False)
    assert b.results.success_rate == 1.0


def test_waypoint_inside_obstacle_is_reported():
    c = RoomCanvas(6.0, 4.0, res=0.025)
    c.rect(4.0, 1.0, 5.5, 3.0)
    twin = make_twin(c, "sites/stress/inside", [("A", 1.0, 2.0, 0.0, "dock"), ("B", 4.75, 2.0, 0.0, "dest")])
    task = Task(sequence=["A", "B"])
    leg = check_feasibility(twin, rect_robot(**ROBOT), task).legs[0]
    assert leg.reachable is False and "inside an obstacle" in leg.reason
    b = run_batch(twin, rect_robot(**ROBOT), task, "none", "nominal", 2, 0, record_episodes=False)
    assert b.results.fail_reasons["no_path_static"] == 2


def test_unknown_waypoint_id_raises():
    c = RoomCanvas(4.0, 4.0)
    twin = make_twin(c, "sites/stress/ids", [("A", 1.0, 2.0, 0.0, "dock"), ("B", 3.0, 2.0, 0.0, "dest")])
    with pytest.raises(ValueError, match="unknown waypoint"):
        check_feasibility(twin, rect_robot(**ROBOT), Task(sequence=["A", "NOPE"]))


def test_unknown_cells_are_blocked():
    c = RoomCanvas(6.0, 4.0, res=0.025)
    c.rect(2.9, 0.0, 3.1, 4.0, value=127)  # unscanned band separating the rooms
    twin = make_twin(c, "sites/stress/unknown", [("A", 1.0, 2.0, 0.0, "dock"), ("B", 5.0, 2.0, 0.0, "dest")])
    f = check_feasibility(twin, rect_robot(**ROBOT), Task(sequence=["A", "B"]))
    assert f.all_reachable is False
    assert "walls or unscanned" in f.legs[0].reason


def test_cspace_build_is_fast_on_400x320():
    rng = np.random.default_rng(0)
    blocked = (rng.random((400, 320)) > 0.97).astype(np.uint8)
    kern = make_kernels(np.array([[0.35, 0.3], [-0.35, 0.3], [-0.35, -0.3], [0.35, -0.3]]), 0.025)
    t0 = time.perf_counter()
    cs = build_cspace(blocked, kern)
    assert time.perf_counter() - t0 < 2.0
    assert cs.shape == (36, 400, 320)


def test_large_grid_feasibility_performance():
    c = RoomCanvas(30.0, 24.0, res=0.025)  # 1200 x 960 cells
    for i in range(1, 6):
        c.rect(i * 5.0 - 0.1, 0.0, i * 5.0 + 0.1, 20.0 if i % 2 else 24.0)
        if i % 2 == 0:
            c.rect(i * 5.0 - 0.1, 0.0, i * 5.0 + 0.1, 4.0)
            c.rect(i * 5.0 - 0.1, 0.0, i * 5.0 + 0.1, 0.01)
    twin = make_twin(c, "sites/stress/large", [("A", 2.0, 2.0, 0.0, "dock"), ("B", 28.0, 22.0, 0.0, "dest")])
    t0 = time.perf_counter()
    f = check_feasibility(twin, rect_robot(**ROBOT), Task(sequence=["A", "B"]))
    el = time.perf_counter() - t0
    assert f.legs[0].reachable is not None
    assert el < 60, el


def test_footprint_fallback_and_missing_dimensions():
    from jobs.sim.geometry import robot_model
    spec = rect_robot(**ROBOT)
    spec.footprint_polygon_m = None
    spec.min_aisle_width_vendor_m = 0.7
    m = robot_model(spec)
    assert m.footprint_approx and m.width == pytest.approx(0.665, abs=0.01)
    spec.min_aisle_width_vendor_m = None
    with pytest.raises(ValueError):
        robot_model(spec)


def test_blocked_leg_reports_the_pinch_of_the_widest_route():
    # Wall at x=5 with a 0.30 m gap on the straight line and a 0.45 m gap far to the north: neither fits the 0.5 m
    # robot. The binding pinch is the wider gap (any fix has to widen the best passage), not the one on the
    # shortest line.
    c = RoomCanvas(10.0, 6.0, res=0.025)
    c.rect(4.9, 0.0, 5.1, 2.85)
    c.rect(4.9, 3.15, 5.1, 4.8)
    c.rect(4.9, 5.25, 5.1, 6.0)
    twin = make_twin(c, "sites/stress/two_gaps", [("A", 2.0, 3.0, 0.0, "dock"), ("B", 8.0, 3.0, 0.0, "dest")])
    leg = check_feasibility(twin, rect_robot(**ROBOT), Task(sequence=["A", "B"])).legs[0]
    assert leg.reachable is False
    assert leg.narrowest_gap_m == pytest.approx(0.45, abs=0.03)
    assert abs(leg.narrowest_xy[1] - 5.025) < 0.15 and abs(leg.narrowest_xy[0] - 5.0) < 0.2
