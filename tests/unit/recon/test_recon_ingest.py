import json

import numpy as np
import pytest

from jobs.recon.ingest import CaptureRejected, load_capture


def _write(tmp, n=30, limited=0, tier="T1_LIDAR", bad_T=False, fps=True, n_bad=1):
    (tmp / "depth").mkdir(parents=True, exist_ok=True)
    cap = {"capture_id": "c", "site_id": "s", "device": {"model": "x", "tier": tier}, "role": "scanner",
           "files": {"poses": "poses.jsonl", "depth": "depth" if tier == "T1_LIDAR" else None,
                     "feature_points": "fp.jsonl" if fps else None}}
    (tmp / "capture.json").write_text(json.dumps(cap))
    lines = []
    for i in range(n):
        T = np.eye(4)
        T[0, 3] = 0.1 * i
        if bad_T and 3 <= i < 3 + n_bad:
            T[0, 0] = 2.0
        lines.append(json.dumps({"t": 0.1 * i, "frame": f"{i:06d}.jpg", "T_world_cam": T.ravel().tolist(),
                                 "K": [200, 200, 128, 96], "w": 256, "h": 192,
                                 "tracking": "limited" if i < limited else "normal"}))
    (tmp / "poses.jsonl").write_text("\n".join(lines))
    if fps:
        (tmp / "fp.jsonl").write_text(json.dumps({"frame": "000000.jpg", "points_world": [[0, 0, -1], [1, 0, -2]]}))
    return tmp


def test_loads_valid_capture(tmp_path):
    cd = load_capture(_write(tmp_path, limited=5))
    assert len(cd.poses) == 30 and cd.tier == "T1_LIDAR"
    assert len(cd.usable()) == 25 and cd.limited_frac == pytest.approx(5 / 30)
    assert cd.feature_points["000000.jpg"].shape == (2, 3)
    assert cd.T(2)[0, 3] == pytest.approx(0.2)


def test_rejects_too_many_limited_frames(tmp_path):
    with pytest.raises(CaptureRejected, match="limited"):
        load_capture(_write(tmp_path, limited=7))  # 23 % > 20 %


def test_non_rigid_pose_is_not_used_and_many_reject(tmp_path):
    cd = load_capture(_write(tmp_path / "one", bad_T=True))
    assert 3 not in cd.usable() and len(cd.usable()) == 29
    assert any("rigid" in w for w in cd.warnings)
    with pytest.raises(CaptureRejected, match="invalid poses"):
        load_capture(_write(tmp_path / "many", bad_T=True, n_bad=7))


def test_rejects_missing_pieces(tmp_path):
    with pytest.raises(CaptureRejected, match="capture.json"):
        load_capture(tmp_path)
    d = _write(tmp_path / "a")
    (d / "depth").rmdir()
    with pytest.raises(CaptureRejected, match="depth"):
        load_capture(d)
    with pytest.raises(CaptureRejected, match="feature_points"):
        load_capture(_write(tmp_path / "b", tier="T2_VIO_MONO", fps=False))
    d = _write(tmp_path / "c")
    (d / "poses.jsonl").write_text('{"t": 0, "frame": "x"}\n')
    with pytest.raises(CaptureRejected, match="line 1"):
        load_capture(d)
    with pytest.raises(CaptureRejected, match="pose records"):
        load_capture(_write(tmp_path / "e", n=5))
