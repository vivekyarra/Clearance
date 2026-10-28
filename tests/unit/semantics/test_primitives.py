import math

import cv2
import numpy as np
import pytest

from clearance_core.grid import FREE, OCC, UNKNOWN, Grid
from jobs.semantics.cluster import dbscan, voxel_downsample
from jobs.semantics.detectors.geometric import GeometricDetector, classify_blob
from jobs.semantics.primitives import (default_movable, fit_circle, fit_footprint_box, fit_primitive,
                                       footprint_iou, footprint_shape_stats, is_round, next_ids, normalize_label,
                                       wrap_yaw)

from .scene_builder import RefBox, RefCyl, object_points


def yaw_err(a, b):
    """Box yaw difference modulo pi (a box is symmetric under a half turn)."""
    d = (a - b) % math.pi
    return min(d, math.pi - d)


@pytest.mark.parametrize("yaw", [0.0, 0.3, 0.785, 1.2, -1.0, math.pi / 2 - 0.01, -math.pi / 2 + 0.02])
@pytest.mark.parametrize("seed", [0, 1])
def test_box_fit_recovers_yaw_and_size(yaw, seed):
    rng = np.random.default_rng(seed)
    ob = RefBox("table", 1.5, -0.7, yaw, 1.2, 0.6, 0.75)
    pts = object_points(ob, 5000, rng) + rng.normal(0, 0.008, (5000, 3))
    outliers = rng.uniform([-1, -3, 0], [4, 2, 2.5], (100, 3))  # 2% scattered junk
    p = fit_primitive(np.r_[pts, outliers], "table", prim_id="T1", confidence=0.8)
    assert p.type == "box"
    assert yaw_err(p.pose.yaw, yaw) < math.radians(3)
    assert abs(p.size[0] - 1.2) < 0.03 and abs(p.size[1] - 0.6) < 0.03
    assert abs(p.size[2] - 0.75) < 0.03
    assert np.hypot(p.pose.xyz[0] - 1.5, p.pose.xyz[1] + 0.7) < 0.02
    assert -math.pi / 2 <= p.pose.yaw < math.pi / 2
    assert p.movable and p.source == "auto"


def test_near_square_box_reports_length_ge_width():
    rng = np.random.default_rng(3)
    ob = RefBox("box", 0, 0, 0.4, 0.62, 0.6, 0.5)
    b = fit_footprint_box(object_points(ob, 3000, rng)[:, :2])
    assert b.length >= b.width
    assert abs(b.length - 0.62) < 0.03 and abs(b.width - 0.6) < 0.03


@pytest.mark.parametrize("d", [0.3, 0.45, 0.8])
def test_cylinder_fit_filled_and_partial(d):
    rng = np.random.default_rng(0)
    ob = RefCyl("trash bin", -0.4, 2.0, d, 0.7)
    pts = object_points(ob, 4000, rng) + rng.normal(0, 0.005, (4000, 3))
    p = fit_primitive(pts, "trash bin", prim_id="TB1", confidence=0.5)
    assert p.type == "cylinder" and abs(p.size[0] - d) < 0.02 and abs(p.size[2] - 0.7) < 0.03
    # Only the half facing a camera is observed: circle fit on the arc still finds the full diameter.
    a = rng.uniform(-math.pi / 2, math.pi / 2, 1500)
    arc = np.c_[-0.4 + d / 2 * np.cos(a), 2.0 + d / 2 * np.sin(a)] + rng.normal(0, 0.004, (1500, 2))
    c = fit_circle(arc)
    assert abs(c.diameter - d) < 0.04
    assert np.hypot(c.center_xy[0] + 0.4, c.center_xy[1] - 2.0) < 0.02


def test_shape_decision_for_unknown_labels():
    rng = np.random.default_rng(1)
    disc = object_points(RefCyl("x", 0, 0, 0.5, 0.5), 3000, rng)
    sq = object_points(RefBox("x", 0, 0, 0.3, 0.5, 0.5, 0.5), 3000, rng)
    rect = object_points(RefBox("x", 0, 0, 0.0, 0.58, 0.5, 0.5), 3000, rng)
    assert is_round(footprint_shape_stats(disc[:, :2]))
    assert not is_round(footprint_shape_stats(sq[:, :2]))
    assert not is_round(footprint_shape_stats(rect[:, :2]))
    assert fit_primitive(disc, "thing", prim_id="O1", confidence=0.3).type == "cylinder"
    assert fit_primitive(sq, "thing", prim_id="O2", confidence=0.3).type == "box"


def test_label_defaults_and_ids():
    for lab in ("chair", "stool", "cart", "table"):
        assert default_movable(lab)
    for lab in ("shelf", "counter"):
        assert not default_movable(lab)
    assert normalize_label("Trash_Can") == "trash bin"
    assert normalize_label("chairs") == "chair"
    ids = next_ids(["table", "table", "chair", "mystery"], taken=["T1"])
    assert ids == ["T2", "T3", "C1", "O1"]
    assert wrap_yaw(math.pi) == pytest.approx(0.0, abs=1e-12)


def test_footprint_iou():
    a = fit_primitive(object_points(RefBox("table", 0, 0, 0, 1, 1, 0.7), 2000), "table", prim_id="T1", confidence=1)
    b = fit_primitive(object_points(RefBox("table", 0.5, 0, 0, 1, 1, 0.7), 2000), "table", prim_id="T2", confidence=1)
    assert footprint_iou(a, a) == pytest.approx(1.0, abs=1e-6)
    assert footprint_iou(a, b) == pytest.approx(1 / 3, abs=0.05)


def test_dbscan_separates_blobs_and_marks_noise():
    rng = np.random.default_rng(0)
    a = rng.normal([0, 0, 0], 0.03, (200, 3))
    b = rng.normal([1, 0, 0], 0.03, (200, 3))
    noise = rng.uniform(-3, 3, (10, 3)) + [10, 10, 10]
    lab = dbscan(np.r_[a, b, noise], eps=0.08, min_samples=5)
    assert len(set(lab[:200])) == 1 and len(set(lab[200:400])) == 1 and lab[0] != lab[200]
    assert (lab[400:] == -1).all()
    assert (dbscan(np.zeros((0, 3)), 0.1) == np.zeros(0)).all()
    c, n, inv = voxel_downsample(a, 0.05)
    assert n.sum() == 200 and len(inv) == 200


# ---------- geometry-based detector on a reference occupancy grid ----------
def make_room_grid(res=0.025):
    W, H = int(6 / res), int(5 / res)
    g = Grid(np.full((H, W), FREE, np.uint8), res, (0.0, 0.0))
    g.data[:, :] = FREE
    t = max(1, int(0.05 / res))  # 5 cm wall band
    g.data[:t, :] = OCC
    g.data[-t:, :] = OCC
    g.data[:, :t] = OCC
    g.data[:, -t:] = OCC
    return g


def draw_box(g: Grid, ob: RefBox):
    c, s = math.cos(ob.yaw), math.sin(ob.yaw)
    corners = np.array([[ob.l / 2, ob.w / 2], [-ob.l / 2, ob.w / 2], [-ob.l / 2, -ob.w / 2], [ob.l / 2, -ob.w / 2]])
    xy = corners @ np.array([[c, s], [-s, c]]) + [ob.cx, ob.cy]
    r, cc = g.world_to_cell(xy[:, 0], xy[:, 1])
    cv2.fillPoly(g.data, [np.stack([cc, r], axis=1).astype(np.int32)], OCC)


def draw_cyl(g: Grid, ob: RefCyl):
    r, c = g.world_to_cell(ob.cx, ob.cy)
    cv2.circle(g.data, (int(c), int(r)), int(round(ob.d / 2 / g.res)), OCC, -1)


def test_geometric_detector_labels_and_fits_blobs():
    g = make_room_grid()
    objs = [RefBox("table", 2.0, 2.5, 0.5, 1.2, 0.7, 0.74), RefBox("shelf", 4.8, 1.0, 0.0, 1.6, 0.4, 1.9),
            RefBox("chair", 1.0, 1.0, -0.3, 0.45, 0.45, 0.9), RefCyl("trash bin", 4.5, 4.0, 0.4, 0.95)]
    rng = np.random.default_rng(0)
    cloud = []
    for ob in objs:
        if isinstance(ob, RefBox):
            draw_box(g, ob)
        else:
            draw_cyl(g, ob)
        cloud.append(object_points(ob, 3000, rng))
    cloud = np.concatenate(cloud)
    found = GeometricDetector().detect_grid(g, cloud)
    assert len(found) == 4  # walls are not furniture
    by_label = {o.label: o for o in found}
    assert set(by_label) == {"table", "shelf", "chair", "trash bin"}
    for o in found:
        assert o.score <= 0.35
    t = fit_primitive(by_label["table"].points, "table", prim_id="T1", confidence=0.3,
                      shape=by_label["table"].shape, height=by_label["table"].height, denoise=False)
    assert yaw_err(t.pose.yaw, 0.5) < math.radians(3)
    assert abs(t.size[0] - 1.2) < 0.05 and abs(t.size[1] - 0.7) < 0.05 and abs(t.size[2] - 0.74) < 0.03
    b = by_label["trash bin"]
    assert b.shape == "cylinder"
    pb = fit_primitive(b.points, b.label, prim_id="TB1", confidence=0.3, shape=b.shape, height=b.height, denoise=False)
    assert abs(pb.size[0] - 0.4) < 0.05


def test_geometric_detector_without_heights_and_empty_grid():
    g = make_room_grid()
    draw_box(g, RefBox("table", 3.0, 2.5, 0.0, 1.0, 0.8, 0.7))
    found = GeometricDetector().detect_grid(g, None)
    assert len(found) == 1 and found[0].label == "table" and found[0].score <= 0.2
    empty = Grid(np.full((40, 40), UNKNOWN, np.uint8), 0.05, (0, 0))
    assert GeometricDetector().detect_grid(empty, None) == []


def test_classify_blob_heuristics():
    assert classify_blob(1.6, 0.4, 0.64, False, 1.9)[0] == "shelf"
    assert classify_blob(2.0, 0.6, 1.2, False, 1.0)[0] == "counter"
    assert classify_blob(1.2, 0.8, 0.96, False, 0.74)[0] == "table"
    assert classify_blob(0.45, 0.45, 0.2, False, 0.9)[0] == "chair"
    assert classify_blob(0.35, 0.35, 0.1, True, 0.65)[:2] == ("stool", "cylinder")
    assert classify_blob(0.4, 0.4, 0.12, True, 2.6)[0] == "pillar"
