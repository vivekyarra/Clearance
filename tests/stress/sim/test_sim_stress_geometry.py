"""Geometry and planning correctness: footprints at every heading, pinches, gaps, thresholds, waypoints."""
import math

import numpy as np
import pytest
import shapely

from clearance_core import storage
from clearance_core.schemas import RobotSpec, Task
from jobs.sim import params as P
from jobs.sim.api import check_feasibility, check_waypoints, run_batch
from jobs.sim.batch import wilson
from jobs.sim.episode import make_context, nominal_geom, plan_mission
from jobs.sim.geometry import transform
from jobs.sim.twin_builder import RoomCanvas, box, make_twin, rect_robot


def _penetration(geom, model, x, y, th):
    """Depth (m) by which the exact footprint polygon at a pose overlaps blocked grid cells."""
    L = geom.layers
    H, W = L.shape
    fp = shapely.Polygon(transform(model.footprint, x, y, th))
    r0, c0 = L.world_to_cell(x, y)
    n = int(math.ceil((model.radius + 0.05) / L.res))
    ra, rb, ca, cb = max(int(r0) - n, 0), min(int(r0) + n + 1, H), max(int(c0) - n, 0), min(int(c0) + n + 1, W)
    rr, cc = np.nonzero(geom.robot_blocked[ra:rb, ca:cb])
    if not len(rr):
        return 0.0
    x0 = L.origin[0] + (cc + ca) * L.res
    y0 = L.origin[1] + (H - (rr + ra) - 1) * L.res
    boxes = shapely.box(x0, y0, x0 + L.res, y0 + L.res)
    if not shapely.intersects(fp, boxes).any():
        return 0.0
    lo, hi = 0.0, 0.1
    for _ in range(12):
        mid = (lo + hi) / 2
        if shapely.intersects(fp.buffer(-mid), boxes).any():
            lo = mid
        else:
            hi = mid
    return lo


def _polygon_robot(points, robot_id="poly_ref", **kw):
    spec = rect_robot(0.5, 0.6, robot_id=robot_id, **kw)
    spec.footprint_polygon_m = points
    return spec


ROBOTS = {
    "rect_long": rect_robot(0.55, 0.74, robot_id="rect_long"),
    "rect_small": rect_robot(0.40, 0.47, robot_id="rect_small"),
    # L-shaped footprint with the rotation center off the middle
    "l_shape": _polygon_robot([(0.45, 0.25), (-0.15, 0.25), (-0.15, -0.25), (0.15, -0.25), (0.15, 0.0), (0.45, 0.0)]),
}


def _max_penetration(twin, robot, seq):
    ctx = make_context(twin, robot, Task(sequence=seq), "none", "nominal")
    geom = nominal_geom(ctx)
    rails, why = plan_mission(ctx, geom)
    assert rails is not None, why
    return max(_penetration(geom, ctx.model, r.x[i], r.y[i], r.th[i]) for r in rails for i in range(len(r.x)))


# the robot center is placed per cell, so overlap up to half a cell diagonal is grid resolution, not a collision
CELL_TOL = 0.5 * math.sqrt(2) * 0.025 + 0.002


@pytest.mark.parametrize("size", [(0.58, 0.89), (0.55, 0.74), (0.50, 0.60)])
def test_storeroom_rails_stay_clear_at_their_true_headings(size):
    # Regression: the c-space is built at 36 lattice headings and every pose was looked up at the nearest one, so a
    # shortcut at 24 degrees or an in-place turn through 38 degrees overlapped the storeroom shelves by up to 4.5 cm
    # while the episode reported success. Off-lattice headings are now checked against both neighbouring headings,
    # and a rail that still collides falls back to the exact lattice path.
    from jobs.reference.rooms import make_room
    twin = make_room("storeroom", 0).twin
    seq = [w for w in ("DOCK", "PICKUP", "S1", "S2", "S3", "S4", "DOCK") if any(x.id == w for x in twin.waypoints)]
    assert _max_penetration(twin, rect_robot(*size), seq) <= CELL_TOL


@pytest.mark.parametrize("name", sorted(ROBOTS))
def test_rails_among_rotated_boxes_stay_clear(name):
    c = RoomCanvas(7.0, 5.0, res=0.025)
    rng = np.random.default_rng(3)
    prims = []
    for i in range(7):
        x, y = rng.uniform(1.5, 5.5), rng.uniform(1.0, 4.0)
        prims.append(box(f"B{i}", float(x), float(y), float(rng.uniform(0.4, 0.9)), float(rng.uniform(0.3, 0.7)),
                         yaw=float(rng.uniform(0, math.pi)), movable=False))
    twin = make_twin(c, f"sites/geo/{name}", [("A", 0.6, 0.6, 0.3, "dock"), ("B", 6.4, 4.4, 2.0, "dest"),
                                              ("C", 6.3, 0.6, -1.0, "dest"), ("D", 0.7, 4.3, 1.2, "dest")],
                     primitives=prims)
    assert _max_penetration(twin, ROBOTS[name], ["A", "B", "C", "D", "A"]) <= CELL_TOL


def _strip_room(slack: float, plen: float, sy: float):
    """A pinch between the west wall and a block, `slack` wider than the robot, with a shelf above the entrance
    that leaves no room to turn right at the wall."""
    c = RoomCanvas(4.0, 5.0, res=0.025)
    c.rect(0.1 + 0.384 + slack, 2.0, 4.0, 2.0 + plen)
    c.rect(0.0, 3.4 + sy, 0.45, 3.9 + sy)
    return make_twin(c, f"sites/geo/strip_{slack}_{plen}_{sy}",
                     [("A", 1.2, 3.0 + plen, 0.0, "dock"), ("B", 1.5, 1.0, 0.0, "dest")])


def test_pinch_passable_on_one_heading_is_found_and_reachability_is_monotonic():
    # Regression: the search kept one state per (cell, heading); lining up for a pinch 6-10 cm wider than the robot
    # next to a wall needs sub-cell positioning, and the old planner answered "no path" for 10 cm of slack while
    # finding 8 cm (23% of DinerBot T8 cafe episodes failed this way). Exhausted searches that the c-space
    # connectivity check does not rule out are now repeated with a 4x finer closed set.
    robot = rect_robot(0.384, 0.468)
    task = Task(sequence=["A", "B"])
    for slack in (0.06, 0.08, 0.10):
        for plen, sy in ((0.5, 0.0), (0.8, 0.3)):
            leg = check_feasibility(_strip_room(slack, plen, sy), robot, task).legs[0]
            assert leg.reachable is True, (slack, plen, sy, leg.reason)
    leg = check_feasibility(_strip_room(-0.02, 0.5, 0.0), robot, task).legs[0]
    assert leg.reachable is False and "narrowest gap" in leg.reason


def test_robot_without_in_place_rotation_never_spins_and_reverses_only_near_the_start():
    c = RoomCanvas(8.0, 6.0, res=0.025)
    twin = make_twin(c, "sites/geo/car", [("A", 1.5, 1.5, 0.0, "dock"), ("B", 6.0, 4.5, math.pi / 2, "dest"),
                                         ("C", 1.5, 4.5, math.pi, "dest")])
    car = rect_robot(0.5, 0.8, robot_id="car", in_place=False, turn_radius=0.8, drive="ackermann")
    ctx = make_context(twin, car, Task(sequence=["A", "B", "C"]), "none", "nominal")
    rails, why = plan_mission(ctx, nominal_geom(ctx))
    assert rails is not None, why
    dth = 2 * math.pi / P.K_HEADINGS
    for rail in rails:
        spun = np.abs(np.diff(rail.th))[rail.rot[1:] == 1].sum()
        assert spun <= dth / 2 + 1e-9  # at most the snap of the start heading onto the lattice
        assert not rail.rev.any()
    # in-place robot facing a wall: it may back out, but only within REVERSE_RADIUS_M of the leg start
    c = RoomCanvas(6.0, 4.0, res=0.025)
    twin = make_twin(c, "sites/geo/back", [("D", 5.62, 2.0, 0.0, "dock"), ("B", 1.0, 2.0, 0.0, "dest")])
    ctx = make_context(twin, rect_robot(0.5, 0.6), Task(sequence=["D", "B"]), "none", "nominal")
    rails, why = plan_mission(ctx, nominal_geom(ctx))
    assert rails is not None, why
    r = rails[0]
    back = np.nonzero(r.rev)[0]
    if len(back):
        assert np.hypot(r.x[back] - r.x[0], r.y[back] - r.y[0]).max() <= P.REVERSE_RADIUS_M + 0.05


@pytest.mark.parametrize("dy", [0.0, 1.0, 2.0])
def test_door_crossed_at_an_angle_reports_the_door_width(dy):
    # Regression: the gap was measured perpendicular to the travel direction only, so a 0.80 m door crossed
    # diagonally read 2.0-3.3 m (and the narrowest point moved away from the door).
    c = RoomCanvas(6.0, 6.0, res=0.025)
    c.rect(2.9, 0.0, 3.1, 2.6)
    c.rect(2.9, 3.4, 3.1, 6.0)
    twin = make_twin(c, f"sites/geo/door_{dy}", [("A", 1.0, 3.0 - dy, 0.0, "dock"), ("B", 5.0, 3.0 + dy, 0.0, "dest")])
    leg = check_feasibility(twin, rect_robot(0.5, 0.6), Task(sequence=["A", "B"])).legs[0]
    assert leg.reachable is True
    assert leg.narrowest_gap_m == pytest.approx(0.80, abs=0.03)
    assert leg.required_gap_m == pytest.approx(0.5)
    assert abs(leg.narrowest_xy[0] - 3.0) <= 0.15 and abs(leg.narrowest_xy[1] - 3.0) <= 0.45


def test_l_turn_corridor_reports_its_width():
    c = RoomCanvas(6.0, 6.0, res=0.025)
    c.rect(0.0, 0.8, 5.0, 6.0)
    twin = make_twin(c, "sites/geo/l_turn", [("A", 0.6, 0.45, 0.0, "dock"), ("B", 5.45, 5.0, 0.0, "dest")])
    leg = check_feasibility(twin, rect_robot(0.5, 0.6), Task(sequence=["A", "B"])).legs[0]
    assert leg.reachable is True
    assert leg.narrowest_gap_m == pytest.approx(0.70, abs=0.03)


def test_diagonal_threshold_is_detected_on_the_actual_path():
    # a 45-degree wall with a door; the threshold lies across the door, diagonal to the grid
    c = RoomCanvas(6.0, 6.0, res=0.025)
    for t in np.arange(0.0, 6.0, 0.02):
        if abs(t - 3.0) > 0.55:
            c.rect(t - 0.1, 6.0 - t - 0.1, t + 0.1, 6.0 - t + 0.1)
    seg = ((3.0 - 0.45 / math.sqrt(2) * 1.2, 3.0 + 0.45 / math.sqrt(2) * 1.2),
           (3.0 + 0.45 / math.sqrt(2) * 1.2, 3.0 - 0.45 / math.sqrt(2) * 1.2))
    for h, expect in ((0.03, False), (0.01, True)):
        twin = make_twin(c, f"sites/geo/diag_thr_{h}", [("A", 1.5, 1.5, 0.0, "dock"), ("B", 4.5, 4.5, 0.0, "dest")],
                         thresholds=[("D1", seg, h, "measured")])
        leg = check_feasibility(twin, rect_robot(0.4, 0.5, max_threshold=0.02), Task(sequence=["A", "B"])).legs[0]
        assert leg.reachable is expect, leg.reason
        if not expect:
            assert leg.blocking_threshold == "D1"


def _table_room(stop_xy):
    c = RoomCanvas(6.0, 4.0, res=0.025)
    prims = [box("TABLE1", 3.0, 2.0, 0.8, 0.8, label="table")]
    return make_twin(c, "sites/geo/stop", [("DOCK", 1.0, 2.0, 0.0, "dock"), ("T1", *stop_xy, 0.0, "dest")],
                     primitives=prims)


def test_stop_point_too_close_to_a_table_is_named_early():
    robot = rect_robot(0.54, 0.6)
    twin = _table_room((3.0, 2.52))   # 0.12 m from the table edge, half the robot width is 0.27 m
    task = Task(sequence=["DOCK", "T1", "DOCK"])
    issues = check_waypoints(twin, robot, task)
    t1 = next(d for d in issues if d["waypoint"] == "T1")
    assert t1["ok"] is False and t1["inside"] is False
    assert t1["nearest_obstacle"].startswith("TABLE1")
    assert t1["nearest_obstacle_m"] == pytest.approx(0.12, abs=0.03)
    assert t1["required_clearance_m"] == pytest.approx(0.27, abs=0.01)
    assert t1["nearest_free_xy"] is not None and t1["nearest_free_m"] > P.WAYPOINT_SNAP_M
    assert "stop point T1 is too close to TABLE1 (table)" in t1["message"]
    assert next(d for d in issues if d["waypoint"] == "DOCK")["ok"] is True
    f = check_feasibility(twin, robot, task)
    assert f.legs[0].reachable is False and f.legs[0].reason == t1["message"]
    b = run_batch(twin, robot, task, "none", "nominal", 2, 0, record_episodes=False, out_prefix="analyses/wp/batches/",
                  sim_config={"jitter": False, "clutter_max": 0})
    assert b.results.fail_reasons["no_path_static"] == 2
    summ = storage.read_json(f"analyses/wp/batches/{b.batch_id}.summary.json")
    assert summ["waypoint_issues"][0]["waypoint"] == "T1"
    assert all("stop point T1" in e["why"] for e in summ["episodes"])
    # a stop point 0.30 m from the table is fine for this robot
    assert all(d["ok"] for d in check_waypoints(_table_room((3.0, 2.72)), robot, task))


def test_waypoint_inside_a_wall_and_outside_the_map():
    c = RoomCanvas(6.0, 4.0, res=0.025)
    c.rect(4.0, 1.0, 5.5, 3.0)
    twin = make_twin(c, "sites/geo/inside", [("A", 1.0, 2.0, 0.0, "dock"), ("B", 4.75, 2.0, 0.0, "dest"),
                                            ("Z", 9.0, 9.0, 0.0, "dest")])
    out = {d["waypoint"]: d for d in check_waypoints(twin, rect_robot(0.5, 0.6))}
    assert out["B"]["inside"] and "inside an obstacle" in out["B"]["message"]
    assert out["Z"]["inside"] and "outside the mapped area" in out["Z"]["message"]


def test_wilson_interval_edge_cases():
    assert wilson(0, 0) == (0.0, 1.0)
    lo, hi = wilson(0, 1)
    assert lo == 0.0 and hi == pytest.approx(0.7935, abs=1e-3)
    lo, hi = wilson(1, 1)
    assert hi == 1.0 and lo == pytest.approx(0.2065, abs=1e-3)
    for n in (1, 2, 7, 50, 1000):
        assert wilson(0, n)[0] == 0.0 and wilson(n, n)[1] == 1.0
        for k in range(0, n + 1, max(1, n // 7)):
            lo, hi = wilson(k, n)
            assert 0.0 <= lo <= k / n <= hi <= 1.0
    with pytest.raises(ValueError):
        wilson(3, 2)
    with pytest.raises(ValueError):
        wilson(-1, 2)


def test_polygon_spec_validation():
    bad = _polygon_robot([(0.3, 0.3), (-0.3, -0.3), (0.3, -0.3), (-0.3, 0.3)])  # self-intersecting bow tie
    c = RoomCanvas(4.0, 4.0)
    twin = make_twin(c, "sites/geo/bow", [("A", 1.0, 2.0, 0.0, "dock"), ("B", 3.0, 2.0, 0.0, "dest")])
    with pytest.raises(ValueError, match="valid simple polygon"):
        check_feasibility(twin, bad, Task(sequence=["A", "B"]))
    assert isinstance(bad, RobotSpec)
