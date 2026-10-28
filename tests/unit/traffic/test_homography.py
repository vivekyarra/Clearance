import json

import numpy as np
import pytest

from jobs.semantics.camera import ARKIT_TO_OPENCV, PinholeCamera, look_at_arkit
from jobs.traffic.floor import foot_points
from jobs.traffic.homography import ObserverCamera, detect_convention, floor_homography

K = [500.0, 500.0, 320.0, 240.0]
W, H = 640, 480


def floor_grid():
    xs, ys = np.meshgrid(np.linspace(2.5, 6.5, 9), np.linspace(-1.5, 1.5, 7))
    return np.stack([xs.ravel(), ys.ravel()], axis=1)


def test_image_to_floor_recovers_known_points():
    T = look_at_arkit((0.0, 0.0, 2.5), (4.0, 0.0, 0.0))  # 2.5 m high, pitched ~32 deg down
    obs = ObserverCamera.from_pose(T, K, W, H, convention="arkit")
    cam = PinholeCamera.from_arkit(T, K, W, H)
    pts = floor_grid()
    uv, z = cam.project(np.c_[pts, np.zeros(len(pts))])
    assert cam.in_image(uv, z).all()
    xy, valid = obs.image_to_floor(uv)
    assert valid.all()
    assert np.abs(xy - pts).max() < 1e-9
    # floor -> image agrees with the full projection
    uv2, v2 = obs.floor_to_image(pts)
    assert v2.all() and np.abs(uv2 - uv).max() < 1e-7
    # the target point is the principal point
    xy_c, _ = obs.image_to_floor(np.array([[320.0, 240.0]]))
    assert np.allclose(xy_c, [[4.0, 0.0]], atol=1e-9)


def test_homography_matches_plane_projection_formula():
    T = look_at_arkit((1.0, -2.0, 3.0), (3.0, 1.0, 0.0))
    cam = PinholeCamera.from_arkit(T, K, W, H)
    Hm = floor_homography(cam)
    pts = np.array([[3.0, 1.0], [2.5, 0.0], [4.0, 2.0]])
    p = np.c_[pts, np.ones(3)] @ Hm.T
    uv_h = p[:, :2] / p[:, 2:]
    uv, _ = cam.project(np.c_[pts, np.zeros(3)])
    assert np.allclose(uv_h, uv, atol=1e-9)


def test_points_above_horizon_are_rejected():
    T = look_at_arkit((0.0, 0.0, 2.5), (30.0, 0.0, 0.0))  # nearly horizontal view
    obs = ObserverCamera.from_pose(T, K, W, H, convention="arkit", max_range_m=50.0)
    rows = np.arange(0, H, 20)
    uv = np.stack([np.full(len(rows), 320.0), rows + 0.5], axis=1)
    xy, valid = obs.image_to_floor(uv)
    assert not valid[0] and valid[-1]              # top rows look at the sky, bottom rows at the floor
    assert np.all(xy[valid][:, 0] > 0)             # every valid point is in front of the camera
    horizon_v = 240 - 500 * np.tan(np.arctan2(2.5, 30.0))
    assert np.all(uv[valid][:, 1] > horizon_v)
    # range limit
    obs_short = ObserverCamera.from_pose(T, K, W, H, convention="arkit", max_range_m=10.0)
    _, v_short = obs_short.image_to_floor(uv)
    assert v_short.sum() < valid.sum()


def test_convention_autodetect_and_equivalence(tmp_path):
    T_ar = look_at_arkit((0.5, 1.0, 2.2), (3.0, 0.5, 0.0))
    T_cv = T_ar @ ARKIT_TO_OPENCV
    assert detect_convention(T_ar, PinholeCamera.from_arkit(T_ar, K, W, H).K, W, H) == "arkit"
    assert detect_convention(T_cv, PinholeCamera.from_arkit(T_ar, K, W, H).K, W, H) == "opencv"
    a = ObserverCamera.from_pose(T_ar, K, W, H)
    b = ObserverCamera.from_pose(T_cv, K, W, H)
    assert a.convention == "arkit" and b.convention == "opencv"
    uv = np.array([[100.0, 400.0], [320.0, 300.0], [600.0, 470.0]])
    assert np.allclose(a.image_to_floor(uv)[0], b.image_to_floor(uv)[0], atol=1e-9)
    p = tmp_path / "observer_pose.json"
    p.write_text(json.dumps({"T_site_cam": T_cv.ravel().tolist(), "K": K, "w": W, "h": H, "convention": "opencv"}))
    c = ObserverCamera.from_file(p)
    assert np.allclose(c.image_to_floor(uv)[0], a.image_to_floor(uv)[0], atol=1e-9)


def test_scaled_resolution_and_bad_pose():
    T = look_at_arkit((0.0, 0.0, 2.5), (4.0, 0.0, 0.0))
    obs = ObserverCamera.from_pose(T, K, W, H, convention="arkit")
    half = obs.scaled(320, 240)
    assert np.allclose(half.image_to_floor(np.array([[160.0, 120.0]]))[0], [[4.0, 0.0]], atol=1e-9)
    below = look_at_arkit((0.0, 0.0, -1.0), (4.0, 0.0, -2.0))
    with pytest.raises(ValueError):
        ObserverCamera.from_pose(below, K, W, H, convention="arkit")
    with pytest.raises(ValueError):
        ObserverCamera.from_pose(T, K, W, H, convention="blender")


def test_foot_points():
    fp = foot_points(np.array([[10, 20, 30, 120], [0, 0, 4, 8]]))
    assert np.allclose(fp, [[20, 120], [2, 8]])
