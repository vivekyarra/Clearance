"""Traffic stress: crowds of 30+ people, passers-by in front of people standing still, overtaking with
occlusion, cameras nearly parallel to the floor in both pose conventions, out-of-order frame
timestamps, and HOG availability probing on OpenCV builds without HOG."""
import json
import math
import types

import cv2
import numpy as np
import pytest

from jobs.semantics.camera import ARKIT_TO_OPENCV, look_at_arkit
from jobs.traffic import detect
from jobs.traffic.floor import image_track_to_floor
from jobs.traffic.homography import ObserverCamera
from jobs.traffic.video import iter_frame_dir

from .test_tracker import Walker, coverage, id_switches, run


def crowd(seed, n=32, frames=240, standing=6):
    rng = np.random.default_rng(seed)
    ws = []
    for _ in range(n):
        start, dur = int(rng.integers(0, 80)), int(rng.integers(80, 160))
        y = rng.uniform(250, 700)
        h = 60 + 0.25 * (y - 250)
        left = rng.random() < 0.5
        ws.append(Walker(start, min(start + dur, frames - 1), 20 if left else 1260, y,
                         rng.uniform(3, 7) * (1 if left else -1), rng.normal(0, 0.4), w=0.4 * h, h=h))
    for _ in range(standing):
        y = rng.uniform(300, 700)
        h = 60 + 0.25 * (y - 250)
        ws.append(Walker(0, frames - 1, rng.uniform(200, 1100), y, 0.0, 0.0, w=0.4 * h, h=h))
    for k, wk in enumerate(ws[:6]):  # long occlusions (10..27 frames, buffer 30)
        s = wk.start + 20
        wk.hidden = set(range(s, s + 10 + 3 * k))
    return ws


@pytest.mark.parametrize("seed", range(3))
def test_crowd_of_38_with_standing_people_and_long_occlusions(seed):
    ws = crowd(seed)
    tr, assign = run(ws, 240, seed=seed, clutter_per_frame=0.3)
    assert id_switches(assign) <= 6  # people entering side by side at the same edge can swap once
    assert coverage(assign, ws) > 0.95
    assert len(tr.all_tracks(min_len=15)) <= len(ws) + 2
    standing = range(len(ws) - 6, len(ws))
    for i in standing:  # people standing still keep one id for the whole recording
        assert len({t for _, t in assign[i]}) == 1


@pytest.mark.parametrize("seed", range(4))
def test_passer_by_in_front_of_standing_person_keeps_both_ids(seed):
    s = Walker(0, 150, 600, 450, 0.0, 0.0)
    p = Walker(0, 150, 100, 455, 7.0, 0.0)
    s.hidden, s.low = set(range(65, 80)), {63, 64, 80}
    _, assign = run([s, p], 151, seed=seed)
    assert id_switches(assign) == 0
    assert [len({t for _, t in v}) for v in assign.values()] == [1, 1]


@pytest.mark.parametrize("seed", range(4))
def test_overtaking_with_occlusion_keeps_both_ids(seed):
    a = Walker(0, 120, 100, 400, 6.0, 0.0)
    b = Walker(0, 120, 200, 404, 4.0, 0.0, w=38, h=96)
    b.hidden, b.low = set(range(42, 58)), {40, 41, 58, 59}
    _, assign = run([a, b], 121, seed=seed)
    assert id_switches(assign) == 0


K = [500.0, 500.0, 320.0, 240.0]
W, H = 640, 480


@pytest.mark.parametrize("height", [0.4, 1.6, 3.0])
@pytest.mark.parametrize("pitch_deg", [15.0, 2.0, 0.3, 0.0, -2.0])
def test_nearly_level_camera_both_conventions(height, pitch_deg):
    p = math.radians(pitch_deg)
    T_ar = look_at_arkit((1.0, 2.0, height), (1.0 + 100 * math.cos(p), 2.0, height - 100 * math.sin(p)))
    T_cv = T_ar @ ARKIT_TO_OPENCV
    a = ObserverCamera.from_pose(T_ar, K, W, H)
    b = ObserverCamera.from_pose(T_cv, K, W, H)
    assert (a.convention, b.convention) == ("arkit", "opencv")
    c = ObserverCamera.from_pose(T_cv.ravel().tolist(), K, W, H, convention="opencv")
    uu, vv = np.meshgrid(np.arange(0, W, 8.0) + 0.5, np.arange(0, H, 2.0) + 0.5)
    uv = np.stack([uu.ravel(), vv.ravel()], axis=1)
    xa, va = a.image_to_floor(uv)
    xb, vb = b.image_to_floor(uv)
    xc, vc = c.image_to_floor(uv)
    assert (va == vb).all() and (va == vc).all()
    assert np.isfinite(xa[va]).all() and np.isnan(xa[~va]).all()
    assert np.abs(xa[va] - xb[va]).max(initial=0) < 1e-6
    # valid points are in front, in range and stable: one pixel moves them <= 0.5 m
    d = np.linalg.norm(xa[va] - [1.0, 2.0], axis=1)
    assert (d <= 25.0 + 1e-9).all() and (xa[va][:, 0] > 1.0 - 1e-9).all()
    x2, v2 = a.image_to_floor(uv[va] + [0.0, 1.0])
    assert np.nanmax(np.linalg.norm(x2[v2] - xa[va][v2], axis=1), initial=0) <= 0.5 + 1e-9
    # round trip floor -> image -> floor
    uv2, ok = a.floor_to_image(xa[va])
    assert np.abs(uv2[ok] - uv[va][ok]).max(initial=0) < 1e-5


def test_degenerate_observer_poses_are_rejected():
    with pytest.raises(ValueError):
        ObserverCamera.from_pose(look_at_arkit((0, 0, 0.01), (5, 0, 0)), K, W, H, convention="arkit")
    bad = look_at_arkit((0, 0, 2.0), (5, 0, 0))
    bad[0, 0] = np.nan
    with pytest.raises(ValueError):
        ObserverCamera.from_pose(bad, K, W, H, convention="arkit")
    scaled = look_at_arkit((0, 0, 2.0), (5, 0, 0))
    scaled[:3, :3] *= 2.0
    with pytest.raises(ValueError):
        ObserverCamera.from_pose(scaled, K, W, H, convention="arkit")


def test_out_of_order_and_repeated_timestamps_in_floor_track():
    cam = ObserverCamera.from_pose(look_at_arkit((0, 0, 2.5), (4, 0, 0)), K, W, H, convention="arkit")
    pts = np.stack([np.linspace(2.5, 6.0, 40), np.zeros(40)], axis=1)
    uv, _ = cam.floor_to_image(pts)
    t = np.arange(40) * 0.125
    hist = [(i, t[i], uv[i, 0] - 15, uv[i, 1] - 60, uv[i, 0] + 15, uv[i, 1], 0.9) for i in range(40)]
    rng = np.random.default_rng(0)
    shuffled = [hist[i] for i in rng.permutation(40)] + [hist[5], hist[6]]  # plus repeated rows
    ft = image_track_to_floor(shuffled, cam, "c:1")
    assert ft is not None and np.all(np.diff(ft.t) > 0)
    assert np.abs(ft.xy[:, 0] - np.interp(ft.t, t, pts[:, 0])).max() < 0.05


def test_frame_dir_is_read_in_time_order(tmp_path):
    img = np.zeros((8, 8, 3), np.uint8)
    names = [f"frame_{i}.png" for i in range(12)]  # not zero-padded: plain sort puts 10, 11 after 1
    for i, n in enumerate(names):
        img[:] = i
        cv2.imwrite(str(tmp_path / n), img)
    out = list(iter_frame_dir(tmp_path, fps=12, target_fps=12))
    assert [int(f[0, 0, 0]) for _, _, f in out] == list(range(12))
    ts = {n: 100.0 + (11 - i) * 0.1 for i, n in enumerate(names)}  # timestamps say reverse order
    out = list(iter_frame_dir(tmp_path, fps=12, target_fps=12, timestamps=ts))
    assert [int(f[0, 0, 0]) for _, _, f in out] == list(range(11, -1, -1))
    assert all(b[0] > a[0] for a, b in zip(out, out[1:]))
    ts[names[3]] = ts[names[4]]  # repeated timestamp: file order, provided times kept
    out = list(iter_frame_dir(tmp_path, fps=12, target_fps=12, timestamps=ts))
    assert [int(f[0, 0, 0]) for _, _, f in out] == list(range(12))


class _CV2Without:
    """cv2 stand-in that delegates to the real module except for the HOG names."""

    def __init__(self, raise_on=(), extra=None):
        self._raise_on, self._extra = set(raise_on), extra or {}

    def __getattr__(self, name):
        if name in self._extra:
            return self._extra[name]
        if name in self._raise_on:
            raise ImportError(f"lazy submodule {name} failed to load")
        if name in ("HOGDescriptor", "HOGDescriptor_getDefaultPeopleDetector", "xobjdetect", "objdetect"):
            raise AttributeError(name)
        return getattr(cv2, name)


class _WorkingHOG:
    @staticmethod
    def getDefaultPeopleDetector():
        return np.ones(3781, np.float32)

    def setSVMDetector(self, svm):
        assert len(svm) == 3781

    def detectMultiScale(self, img, **kw):
        return (), ()


class _FailingHOG(_WorkingHOG):
    def setSVMDetector(self, svm):
        raise cv2.error("SVM detector unsupported in this build")


@pytest.mark.parametrize("mod", [
    _CV2Without(),
    _CV2Without(raise_on=("xobjdetect", "objdetect")),
    _CV2Without(extra={"HOGDescriptor": _FailingHOG}),
    _CV2Without(extra={"xobjdetect": types.SimpleNamespace(HOGDescriptor=_FailingHOG)}),
], ids=["absent", "lazy-import-error", "failing-main", "failing-contrib"])
def test_no_usable_hog_falls_back_to_motion(monkeypatch, mod):
    monkeypatch.setattr(detect, "cv2", mod)
    monkeypatch.setattr(detect, "_HOG_PROBE", None)
    monkeypatch.setattr(detect.RTDETRPersonDetector, "gpu_available", classmethod(lambda cls: False))
    assert not detect.HOGPersonDetector.available()
    with pytest.raises(RuntimeError):
        detect.HOGPersonDetector()
    assert detect.make_person_detector("auto").name == "motion"
    assert detect.make_person_detector("hog").name == "motion"
    monkeypatch.setenv("CLEARANCE_TRAFFIC_DETECTOR", " HOG ")
    assert detect.make_person_detector().name == "motion"


def test_working_contrib_hog_is_used(monkeypatch):
    monkeypatch.setattr(detect, "cv2", _CV2Without(extra={"xobjdetect": types.SimpleNamespace(HOGDescriptor=_WorkingHOG)}))
    monkeypatch.setattr(detect, "_HOG_PROBE", None)
    monkeypatch.setattr(detect.RTDETRPersonDetector, "gpu_available", classmethod(lambda cls: False))
    assert detect.HOGPersonDetector.available()
    d = detect.make_person_detector("auto")
    assert d.name == "hog"
    assert d.detect(np.zeros((240, 320, 3), np.uint8)).shape == (0, 5)
    assert d.detect(np.zeros((10, 10, 3), np.uint8)).shape == (0, 5)   # smaller than the window
    assert d.detect(np.zeros((0, 0, 3), np.uint8)).shape == (0, 5)


def test_real_build_probe_is_consistent():
    detect._HOG_PROBE = None
    ok = detect.HOGPersonDetector.available()
    assert ok == (detect._hog_api(refresh=True) is not None)
    assert detect.make_person_detector("hog").name == ("hog" if ok else "motion")
