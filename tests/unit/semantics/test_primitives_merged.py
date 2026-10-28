"""Geometric detector on touching objects: table with chairs pushed against it (one occupancy blob),
rotated groups, touching objects without height data, and single objects that must stay whole."""
import math

import cv2
import numpy as np
import pytest

from clearance_core.grid import FREE, OCC, Grid
from jobs.semantics.detectors.geometric import GeometricDetector
from jobs.semantics.primitives import fit_primitive

from .scene_builder import RefBox, RefCyl, object_points


def room(res=0.025):
    W, H = int(6 / res), int(5 / res)
    g = Grid(np.full((H, W), FREE, np.uint8), res, (0.0, 0.0))
    t = max(1, int(0.05 / res))
    g.data[:t, :] = OCC
    g.data[-t:, :] = OCC
    g.data[:, :t] = OCC
    g.data[:, -t:] = OCC
    return g


def corners(ob):
    c, s = math.cos(ob.yaw), math.sin(ob.yaw)
    loc = np.array([[ob.l / 2, ob.w / 2], [-ob.l / 2, ob.w / 2], [-ob.l / 2, -ob.w / 2], [ob.l / 2, -ob.w / 2]])
    return loc @ np.array([[c, s], [-s, c]]) + [ob.cx, ob.cy]


def draw(g, ob):
    if isinstance(ob, RefBox):
        xy = corners(ob)
        r, c = g.world_to_cell(xy[:, 0], xy[:, 1])
        cv2.fillPoly(g.data, [np.stack([c, r], axis=1).astype(np.int32)], OCC)
    else:
        r, c = g.world_to_cell(ob.cx, ob.cy)
        cv2.circle(g.data, (int(c), int(r)), int(round(ob.d / 2 / g.res)), OCC, -1)


def local(cx, cy, yaw, u, v):
    c, s = math.cos(yaw), math.sin(yaw)
    return cx + c * u - s * v, cy + s * u + c * v


def table_with_chairs(cx=3.0, cy=2.5, yaw=0.0, sides=("w", "e"), chair_offset=0.0):
    """0.9 m square table (h 0.74) with 0.45 m chairs (seat h 0.46, back h 0.9) touching its sides."""
    objs = [("table", [RefBox("table", cx, cy, yaw, 0.9, 0.9, 0.74)])]
    for sd in sides:
        ang = {"e": 0.0, "n": math.pi / 2, "w": math.pi, "s": -math.pi / 2}[sd]
        d = 0.45 + 0.225  # chair centre distance from the table centre: touching
        u, v = d * math.cos(ang), d * math.sin(ang)
        tu, tv = -math.sin(ang) * chair_offset, math.cos(ang) * chair_offset
        sx, sy = local(cx, cy, yaw, u + tu, v + tv)
        bx, by = local(cx, cy, yaw, u + tu + 0.195 * math.cos(ang), v + tv + 0.195 * math.sin(ang))
        objs.append(("chair", [RefBox("chair", sx, sy, yaw + ang, 0.45, 0.45, 0.46),
                               RefBox("chair", bx, by, yaw + ang, 0.06, 0.45, 0.9)]))
    return objs


def scene(groups, seed=0, with_cloud=True):
    g = room()
    rng = np.random.default_rng(seed)
    cloud = []
    for _, parts in groups:
        for ob in parts:
            draw(g, ob)
            area = ob.l * ob.w if isinstance(ob, RefBox) else math.pi * ob.d ** 2 / 4
            cloud.append(object_points(ob, int(4000 * max(area, 0.05) / 0.2), rng))
    return g, (np.concatenate(cloud) if with_cloud else None)


@pytest.mark.parametrize("yaw", [0.0, 0.35, -1.1])
@pytest.mark.parametrize("seed", [0, 1])
def test_table_and_touching_chairs_are_separated_by_height(yaw, seed):
    groups = table_with_chairs(yaw=yaw)
    g, cloud = scene(groups, seed)
    found = GeometricDetector().detect_grid(g, cloud)
    labels = sorted(o.label for o in found)
    assert labels == ["chair", "chair", "table"], labels
    t = next(o for o in found if o.label == "table")
    pt = fit_primitive(t.points, t.label, prim_id="T1", confidence=t.score, shape=t.shape, height=t.height,
                       denoise=False)
    assert abs(pt.size[0] - 0.9) < 0.08 and abs(pt.size[1] - 0.9) < 0.08 and abs(pt.size[2] - 0.74) < 0.05
    assert math.hypot(pt.pose.xyz[0] - 3.0, pt.pose.xyz[1] - 2.5) < 0.05
    for o in found:
        assert o.extra.get("split") == "height"
        assert o.score < 0.35  # split pieces are less certain than a clean single blob
    for o in (o for o in found if o.label == "chair"):
        pc = fit_primitive(o.points, o.label, prim_id="C1", confidence=o.score, shape=o.shape, height=o.height,
                           denoise=False)
        assert 0.35 < pc.size[0] < 0.6 and 0.35 < pc.size[1] < 0.6


def test_four_chairs_around_rotated_table():
    groups = table_with_chairs(yaw=0.6, sides=("w", "e", "n", "s"))
    g, cloud = scene(groups, 3)
    labels = sorted(o.label for o in GeometricDetector().detect_grid(g, cloud))
    assert labels == ["chair"] * 4 + ["table"], labels


def test_touching_at_a_narrow_neck_without_heights_is_split_by_shape():
    # chair slid along the table edge so only 15 cm of it touches: a narrow neck in the blob
    groups = table_with_chairs(sides=("e",), chair_offset=0.60)
    g, _ = scene(groups, with_cloud=False)
    found = GeometricDetector().detect_grid(g, None)
    assert len(found) == 2, [(o.label, o.extra.get("area_m2")) for o in found]
    assert all(o.extra.get("split") == "shape" for o in found)
    areas = sorted(o.extra["area_m2"] for o in found)
    assert areas[0] == pytest.approx(0.45 * 0.45, rel=0.25) and areas[1] == pytest.approx(0.81, rel=0.15)


def test_unseparable_merge_is_flagged_with_lower_confidence():
    groups = table_with_chairs(sides=("w", "e"))
    g, _ = scene(groups, with_cloud=False)
    found = GeometricDetector().detect_grid(g, None)
    assert len(found) == 1
    o = found[0]
    assert o.extra.get("possible_merge") is True
    assert o.score < 0.2


@pytest.mark.parametrize("seed", range(4))
def test_single_objects_are_never_split(seed):
    rng = np.random.default_rng(seed)
    objs = [("table", [RefBox("table", 1.5, 1.5, float(rng.uniform(-1, 1)), 1.2, 0.7, 0.74)]),
            ("chair", [RefBox("chair", 4.0, 1.2, 0.3, 0.45, 0.45, 0.46), RefBox("chair", 4.0 + 0.195 * math.cos(0.3),
                                                                             1.2 + 0.195 * math.sin(0.3), 0.3, 0.06,
                                                                             0.45, 0.9)]),
            ("counter", [RefBox("counter", 3.0, 4.2, 0.0, 2.0, 0.6, 1.0), RefBox("box", 3.4, 4.2, 0.0, 0.3, 0.3, 1.3)]),
            ("trash bin", [RefCyl("trash bin", 4.8, 3.0, 0.4, 0.95)])]
    g, cloud = scene(objs, seed)
    found = GeometricDetector().detect_grid(g, cloud)
    assert sorted(o.label for o in found) == ["chair", "counter", "table", "trash bin"]
    assert not any(o.extra.get("split") for o in found)
    assert not any(o.extra.get("possible_merge") for o in found)
