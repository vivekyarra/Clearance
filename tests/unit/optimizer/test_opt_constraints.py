import math

import numpy as np
import pytest

import opt_fixtures as fx
from jobs.optimizer.candidates import (bottleneck_peaks, candidate_objects, compass, describe_move,
                                       generate_moves)
from jobs.optimizer.constraints import ConstraintChecker, Rules, robot_radius
from jobs.optimizer.geometry import (bfs_reachable, clear_mask, egress_status, fallback_rasterize,
                                     polygon_cells, primitive_polygon)
from clearance_core.schemas import Waypoint


@pytest.fixture
def twin():
    return fx.make_twin()


def checker(twin, **rules):
    return ConstraintChecker(twin, fx.make_robot(), fx.scripted_rasterize, Rules.from_dict(rules))


def test_baseline_layout_is_admissible(twin):
    ch = checker(twin)
    assert ch.egress_baseline == {"ENT": True}
    assert ch.check({}).ok
    assert ch.check({"T4": (0.0, 0.2, 0.0)}).ok


def test_overlap_with_other_primitive(twin):
    ch = checker(twin)
    r = ch.check({"S1": (0.0, 0.25, 0.0)})  # stool pushed into the counter
    assert not r.ok and r.reason == "overlap:CNT"


def test_pair_moves_overlap_each_other(twin):
    ch = checker(twin)
    # chair moved south-east next to the table, table moved north-west into it
    r = ch.check({"C1": (0.9, -0.7, 0.0), "T4": (-0.1, 0.1, 0.0)})
    assert not r.ok and r.reason.startswith("overlap:")


def test_outside_floor_bounds(twin):
    r = checker(twin).check({"T4": (0.0, -0.75, 0.0)})
    assert not r.ok and r.reason == "outside_floor"


def test_overlap_with_wall_cells_when_no_bounds_polygon():
    tw = fx.make_twin(with_bounds=False)
    r = checker(tw).check({"T4": (0.0, -0.8, 0.0)})  # 10 cm into the south wall
    assert not r.ok and r.reason == "overlap:wall"


def test_keepout(twin):
    r = checker(twin).check({"T4": (0.15, 0.0, 0.0)})
    assert not r.ok and r.reason == "keepout:K1"
    assert checker(twin).check({"T4": (0.05, 0.0, 0.0)}).ok


def test_waypoint_min_gap(twin):
    twin.waypoints.append(Waypoint(id="PICK", xy=(3.0, 1.95), kind="pickup"))
    ch = checker(twin)
    assert ch.wp_min_gap == pytest.approx(robot_radius(fx.make_robot()))
    assert ch.check({"T4": (0.0, 0.05, 0.0)}).ok          # 0.40 m > 0.354 m
    r = ch.check({"T4": (0.0, 0.15, 0.0)})                # 0.30 m < 0.354 m
    assert not r.ok and r.reason == "waypoint:PICK"


def test_existing_waypoint_violation_can_only_get_better(twin):
    twin.waypoints.append(Waypoint(id="PICK", xy=(3.0, 1.6), kind="pickup"))  # already 0.1 m from T4
    ch = checker(twin)
    assert ch.check({"T4": (0.0, -0.05, 0.0)}).ok         # moves away: allowed
    assert not ch.check({"T4": (0.0, 0.05, 0.0)}).ok      # moves closer: rejected


def test_egress_min_gap_rule(twin):
    # chair C1 placed near the north wall above T4: north gap 0.925 m (egress path), south gap 0.7 m.
    # On the grid the chair covers the 2.425 m cell row, so the raster north gap is 0.90 m.
    c1 = next(p for p in twin.primitives if p.id == "C1")
    c1.pose.xyz = (3.0, 2.65, c1.pose.xyz[2])
    ch = checker(twin)
    assert ch.egress_required == {"ENT"} and ch.egress_width_m == {"ENT": pytest.approx(0.9)}
    r = ch.check({"T4": (0.0, 0.05, 0.0)})                  # north gap 0.875 m (raster 0.85 m) < 0.9 m
    assert not r.ok and r.reason == "egress:ENT"
    assert ch.check({"T4": (0.0, -0.05, 0.0)}).ok           # moving away keeps the 0.9 m path
    r = ch.check({"T4": (0.0, 0.1, 0.0)})                   # north 0.825 m, south 0.8 m: no 0.9 m path
    assert not r.ok and r.reason == "egress:ENT"
    assert ch.check({"T4": (0.0, 0.1, 0.0)}, egress=False).ok
    assert checker(twin, egress_min_gap_m=0.7).check({"T4": (0.0, 0.1, 0.0)}).ok
    # pair: moving the chair away too reopens the north path
    assert ch.check({"T4": (0.0, 0.1, 0.0), "C1": (-1.5, 0.0, 0.0)}).ok


def test_egress_unsatisfiable_baseline_must_not_get_worse(twin):
    # no 2.0 m path exists: the widest existing path (1.025 m) becomes the requirement
    ch = checker(twin, egress_min_gap_m=2.0)
    assert ch.egress_baseline == {"ENT": False} and not ch.egress_required
    assert ch.egress_width_required == {"ENT": pytest.approx(1.025)}
    r = ch.check({"T4": (0.0, 0.3, 0.0)})                  # narrows the path north of T4
    assert not r.ok and r.reason == "egress:ENT"
    assert ch.check({"T4": (0.0, -0.1, 0.0)}).ok
    assert ch.check({"S1": (0.0, -0.2, 0.0)}).ok


def test_egress_without_any_path_is_not_required(twin):
    twin.waypoints[2] = twin.waypoints[2].model_copy(update={"xy": (5.0, 2.6)})  # entrance inside the counter
    ch = checker(twin, egress_snap_m=0.05)
    assert ch.egress_width_m == {"ENT": 0.0} and not ch.egress_width_required
    assert ch.check({"T4": (0.0, 0.3, 0.0)}).ok


def test_egress_custom_kinds(twin):
    ch = checker(twin, egress_from_kinds=["dock"], egress_to_kinds=["dest"])
    assert set(ch.egress_baseline) == {"DOCK"}


def test_label_flood_fill_matches_bfs():
    rng = np.random.default_rng(3)
    blocked = rng.random((40, 50)) < 0.12
    mask = clear_mask(blocked, 0.05, 0.15)
    seeds = np.flatnonzero(mask)[:1]
    reach = bfs_reachable(mask, seeds)
    from scipy import ndimage
    lab, _ = ndimage.label(mask)
    np.testing.assert_array_equal(reach, lab == lab.flat[seeds[0]])


def test_clear_mask_corridor_width():
    # corridor exactly 0.9 m (36 cells at 2.5 cm) passes a 0.9 m rule, 0.8 m does not
    for width_cells, ok in ((36, True), (32, False)):
        blocked = np.ones((80, 40), bool)
        blocked[:, 2:2 + width_cells] = False
        st = egress_status(blocked, 0.025, (0.0, 0.0), [("A", (0.5, 0.1))], [("B", (0.5, 1.9))], 0.9, 0.5)
        assert st["A"] is ok


def test_polygon_cells_and_primitive_polygon():
    g = fx.walls_grid()
    p = fx.box("X", 1.0, 1.0, 0.5, 0.3, "t")
    poly = primitive_polygon(p)
    assert poly.area == pytest.approx(0.15)
    r, c = polygon_cells(poly, g.shape, g.res, g.origin)
    assert len(r) == 10 * 6
    rot = primitive_polygon(p, 0.1, 0.0, math.radians(90))
    minx, miny, maxx, maxy = rot.bounds
    assert (maxx - minx, maxy - miny) == (pytest.approx(0.3), pytest.approx(0.5))
    assert rot.centroid.x == pytest.approx(1.1)


def test_peaks_nms_and_candidates(twin):
    g = fx.walls_grid()
    peaks = bottleneck_peaks(fx.make_bottleneck(g), g, k=3)
    assert len(peaks) == 3
    assert math.hypot(peaks[0].x - fx.P[0], peaks[0].y - fx.P[1]) <= fx.RES  # neighbours of P suppressed
    cands, skipped = candidate_objects(twin, peaks, 1.5, hint_ids=["S1", "CNT", "NOPE"])
    assert [c["id"] for c in cands] == ["S1", "C1", "T4"]  # nearest first after hints
    assert cands[0]["hint"] and skipped == ["CNT", "NOPE"]
    with pytest.raises(ValueError):
        bottleneck_peaks(np.zeros((3, 3)), g)


def test_generate_moves_pruning(twin):
    ch = checker(twin)
    g = fx.walls_grid()
    peaks = bottleneck_peaks(fx.make_bottleneck(g), g)
    moves, rejected = generate_moves(ch, "T4", peaks, max_moves=40)
    assert 0 < len(moves) <= 40
    for m in moves:
        d = math.hypot(m["dx"], m["dy"])
        assert d <= 0.30 + 1e-9
        assert abs(round(m["dx"] / 0.05) * 0.05 - m["dx"]) < 1e-9 and abs(round(m["dy"] / 0.05) * 0.05 - m["dy"]) < 1e-9
        assert round(math.degrees(m["dyaw"])) in (0, 15, -15)
        assert m["dy"] >= 0          # cone: never toward the south pinch
        assert m["gain"] >= 0
    rings = {math.ceil(math.hypot(m["dx"], m["dy"]) / 0.05 - 1e-9) for m in moves}
    assert len(rings) >= 6           # stratified: small and large moves both present
    assert "keepout" in rejected     # north-east moves hit K1
    stool, rej = generate_moves(ch, "S1", peaks, max_moves=40)
    assert all(m["dyaw"] == 0 for m in stool)  # cylinder: no rotations
    assert rej.get("overlap", 0) > 0           # north moves hit the counter


def test_descriptions(twin):
    ch = checker(twin)
    assert compass(1, 0) == "east" and compass(0, -1) == "south" and compass(-1, 1) == "north-west"
    assert describe_move(ch, "C1", 0.0, 0.1, 0.0) == "Move C1 0.10 m toward wall (north)"
    # T4: south wall (0.7 m behind) is nearer than the north wall (1.5 m ahead)
    assert describe_move(ch, "T4", 0.0, 0.2, 0.0) == "Move T4 0.20 m away from wall (north)"
    assert describe_move(ch, "S1", 0.0, -0.1, 0.0) == "Move S1 0.10 m away from counter (south)"
    assert describe_move(ch, "T4", 0.0, 0.0, math.radians(-15)) == "Rotate T4 15° clockwise"
    assert describe_move(ch, "T4", 0.05, 0.05, math.radians(15)).endswith("and rotate 15° counter-clockwise")


def test_fallback_rasterizer_reads_occupancy(tmp_path, monkeypatch, twin):
    import cv2
    from clearance_core import storage
    monkeypatch.setattr(storage, "DATA", tmp_path)
    occ_path = tmp_path / twin.occupancy.uri
    occ_path.parent.mkdir(parents=True)
    cv2.imwrite(str(occ_path), fx.walls_grid().data)
    g = fallback_rasterize(twin)
    ref = fx.scripted_rasterize(twin)
    assert (g.data == ref.data).mean() > 0.995
    moved = fallback_rasterize(twin, overrides={"move": {"T4": {"dx": 0, "dy": 0.3, "dyaw": 0}}, "remove": ["C1"]})
    assert fx.column_gap(moved, fx.P) == pytest.approx(1.0)
    assert fx.column_gap(moved, fx.Q) > 2.5
