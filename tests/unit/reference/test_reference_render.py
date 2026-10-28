import json

import cv2
import numpy as np
import pytest

from jobs.common.camera import K_scaled, backproject, look_rotation, make_T, pixel_rays_cam, project
from jobs.recon.ingest import load_capture
from jobs.reference.geometry import Solid, free_gap_2d, raycast_solids
from jobs.reference.render import DEPTH_H, DEPTH_W, image_K, plan_trajectory, render_capture, render_depth
from jobs.reference.rooms import make_room


def test_raycast_box_and_cylinder():
    box = Solid("B", "box", "b", 2.0, 0.0, 1.0, 1.0, 0.0, 1.0, yaw=np.pi / 4)
    cyl = Solid("C", "cyl", "c", 0.0, 3.0, 0.5, 0.5, 0.0, 2.0)
    o = np.array([0.0, 0.0, 0.5])
    dirs = np.array([[1.0, 0, 0], [0, 1.0, 0], [0, 0, -1.0], [0, 0, 1.0]])
    t, hid = raycast_solids(o, dirs, [box, cyl], floor=True, ceiling_z=2.5)
    assert t[0] == pytest.approx(2.0 - np.sqrt(0.5))   # box corner faces the ray
    assert t[1] == pytest.approx(3.0 - 0.25)
    assert t[2] == pytest.approx(0.5) and hid[2] == -2
    assert t[3] == pytest.approx(2.0) and hid[3] == -3
    t2, _ = raycast_solids(np.array([2.0, 0, 1.5]), np.array([[0, 0, -1.0]]), [box])
    assert t2[0] == pytest.approx(0.5)  # box top


def test_free_gap_2d_exact():
    a = Solid("A", "box", "b", 0.0, 0.0, 1.0, 1.0, 0, 1)
    b = Solid("B", "cyl", "c", 2.0, 0.0, 0.4, 0.4, 0, 1)
    pa, pb, w = free_gap_2d([a, b], (1.0, 0.0), (1, 0))
    assert w == pytest.approx(2.0 - 0.2 - 0.5) and pa == pytest.approx((0.5, 0.0))
    with pytest.raises(ValueError):
        free_gap_2d([a], (1.0, 0.0), (1, 0))  # unbounded on one side
    with pytest.raises(ValueError):
        free_gap_2d([a, b], (0.0, 0.0), (1, 0))  # midpoint inside a solid


def test_camera_convention_roundtrip():
    T = make_T(look_rotation(0.3, -0.5, 0.05), [1.0, 2.0, 1.4])
    K = (200.0, 200.0, 127.5, 95.5)
    depth = np.full((192, 256), 2.0)
    pts = backproject(depth, K, T, step=8)
    u, v, d, ok = project(pts, K, T, 256, 192)
    assert ok.all() and np.allclose(d, 2.0)
    _, uu, vv = pixel_rays_cam(K, 256, 192, 8)
    assert np.allclose(u, uu) and np.allclose(v, vv)
    # forward axis is -Z of the camera; looking down means negative world z
    assert (-T[:3, 2])[2] < 0
    # pixel above image center maps to a point higher in the world
    up = backproject(np.pad(np.full((1, 1), 2.0), ((0, 191), (127, 128))), K, T)
    assert up[0, 2] > pts[:, 2].mean()
    assert K_scaled((100, 100, 49.5, 49.5), 2, 2) == (200, 200, 99.5, 99.5)


def test_trajectory_is_handheld_and_dense():
    room = make_room("golden_empty")
    keys, arcs = plan_trajectory(room, np.random.default_rng(0))
    p = np.array([T[:3, 3] for T in keys])
    assert np.all(np.abs(p[:, 2] - 1.4) < 0.05)
    steps = np.linalg.norm(np.diff(p, axis=0), axis=1)
    assert steps.max() < 0.13 and np.median(steps) > 0.02
    fwd_z = np.array([-T[2, 2] for T in keys])
    assert np.all(fwd_z < 0)  # always looking somewhat down


@pytest.mark.parametrize("tier", ["T1_LIDAR", "T2_VIO_MONO"])
def test_render_capture_layout(tmp_path, tier):
    room = make_room("golden_threshold")
    out = render_capture(room, str(tmp_path / tier), tier, seed=0, max_frames=12)
    cd = load_capture(out)
    assert cd.tier == tier and len(cd.poses) == 12
    assert cd.poses[0].w == 320 and cd.poses[0].h == 240
    assert (tmp_path / tier / "frames" / "000000.jpg").is_file()
    n_fp = [len(v) for v in cd.feature_points.values()]
    assert min(n_fp) >= 100 and max(n_fp) <= 300
    meta = json.loads((tmp_path / tier / "render_meta.json").read_text())
    assert meta["n_frames"] == 12
    if tier == "T1_LIDAR":
        d = cv2.imread(str(tmp_path / tier / "depth" / "000005.png"), cv2.IMREAD_UNCHANGED)
        c = cv2.imread(str(tmp_path / tier / "confidence" / "000005.png"), cv2.IMREAD_UNCHANGED)
        assert d.dtype == np.uint16 and d.shape == (DEPTH_H, DEPTH_W) and set(np.unique(c)) <= {0, 1, 2}
        # noisy depth agrees with true depth (from the drift-free pose) to ~1 %
        K_d = K_scaled(image_K(), DEPTH_W / 320, DEPTH_H / 240)
        from jobs.reference.render import plan_trajectory as pt
        keys, _ = pt(room, np.random.default_rng(10_000))
        true, _ = render_depth(room, keys[5], K_d, DEPTH_W, DEPTH_H)
        m = (c == 2) & (d > 0)
        rel = np.abs(d[m] / 1000.0 - true[m]) / true[m]
        assert np.median(rel) < 0.012 and m.mean() > 0.6
        assert not (tmp_path / tier / "reldepth").exists()
    else:
        assert not (tmp_path / tier / "depth").exists()
        rd = cv2.imread(str(tmp_path / tier / "reldepth" / "000005.png"), cv2.IMREAD_UNCHANGED)
        assert rd.dtype == np.uint16 and rd.max() > 60000
        cj = json.loads((tmp_path / tier / "capture.json").read_text())
        assert cj["files"]["relative_depth"] == "reldepth"


def test_render_rejects_path_through_walls():
    room = make_room("golden_threshold")
    room.scan_path = [(1.0, 1.0), (5.0, 1.0)]  # crosses the divider wall away from the door
    with pytest.raises(ValueError, match="passes through"):
        plan_trajectory(room, np.random.default_rng(0))
