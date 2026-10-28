"""Stray Scanner exports as they come off real phones: tracking-lost rows, no confidence maps, no
decodable video, odd frame rates, marker seen once or wrongly."""
import json

import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from jobs.common.camera import ARKIT_TO_SITE, CV_TO_ARKIT_CAM, look_rotation, make_T, rot_z
from jobs.common.imageio import imwrite
from jobs.recon.importers import consistent_marker_observations, import_stray_scanner, rgb_size_from_K
from jobs.recon.ingest import load_capture

K_1920 = (1450.0, 1450.0, 959.5, 719.5)


def _export(root, n=40, nan_rows=(), conf=True, header_k=True, frame_step=1):
    root.mkdir(parents=True)
    (root / "depth").mkdir()
    if conf:
        (root / "confidence").mkdir()
    lines = ["timestamp, frame, x, y, z, qx, qy, qz, qw" + (", fx, fy, cx, cy" if header_k else "")]
    Ts = []
    for i in range(n):
        p = np.array([0.15 * i, 0.4 * np.sin(i / 5), 1.4])
        T = make_T(look_rotation(0.2 * i, -0.5), p)
        Ts.append(T)
        T_ark_cv = np.linalg.inv(ARKIT_TO_SITE) @ T @ CV_TO_ARKIT_CAM
        q = Rotation.from_matrix(T_ark_cv[:3, :3]).as_quat()
        vals = [*T_ark_cv[:3, 3], *q]
        if i in nan_rows:
            vals = [float("nan")] * 3 + [0.0, 0.0, 0.0, 0.0]
        fid = i * frame_step
        row = [f"{i / 60 * frame_step:.4f}", str(fid)] + [f"{v:.8f}" for v in vals]
        if header_k:
            row += [f"{v}" for v in K_1920]
        lines.append(", ".join(row))
        imwrite(root / "depth" / f"{fid:06d}.png", np.full((192, 256), 1800, np.uint16))
        if conf:
            imwrite(root / "confidence" / f"{fid:06d}.png", np.full((192, 256), 2, np.uint8))
    (root / "odometry.csv").write_text("\n".join(lines) + "\n", encoding="utf-8")
    M = np.array([[K_1920[0], 0, K_1920[2]], [0, K_1920[1], K_1920[3]], [0, 0, 1]])
    np.savetxt(root / "camera_matrix.csv", M, delimiter=",")
    return Ts


def test_tracking_lost_rows_are_skipped(tmp_path):
    Ts = _export(tmp_path / "s", nan_rows={5, 6, 7})
    out = import_stray_scanner(tmp_path / "s", tmp_path / "c", keyframes=False)
    cj = json.loads((tmp_path / "c" / "capture.json").read_text(encoding="utf-8"))
    assert cj["import"]["n_rows_used"] == len(Ts) - 3 and cj["import"]["warnings"]
    cd = load_capture(out)
    assert len(cd.usable()) == len(Ts) - 3


def test_missing_confidence_and_odd_frame_ids(tmp_path):
    _export(tmp_path / "s", conf=False, frame_step=3)  # e.g. a 20 fps export of a 60 fps session
    out = import_stray_scanner(tmp_path / "s", tmp_path / "c", keyframes=False)
    cd = load_capture(out)
    assert cd.capture.files.confidence is None
    assert (tmp_path / "c" / "depth" / "000003.png").is_file()
    cj = json.loads((tmp_path / "c" / "capture.json").read_text(encoding="utf-8"))
    assert any("confidence" in w for w in cj["import"]["warnings"])


def test_rgb_size_without_video_comes_from_principal_point(tmp_path):
    _export(tmp_path / "s", header_k=False)
    import_stray_scanner(tmp_path / "s", tmp_path / "c", keyframes=False)
    cj = json.loads((tmp_path / "c" / "capture.json").read_text(encoding="utf-8"))
    assert cj["import"]["rgb_size"] == [1920, 1440] and cj["import"]["rgb_size_source"] == "principal_point"
    cd = load_capture(tmp_path / "c")
    assert (cd.poses[0].w, cd.poses[0].h) == (1920, 1440)
    assert rgb_size_from_K((1450.0, 1450.0, 719.5, 959.5)) == (1440, 1920)  # portrait
    assert rgb_size_from_K((10.0, 10.0, 3.0, 2.0)) is None


def test_wrong_rgb_size_is_reported(tmp_path):
    _export(tmp_path / "s")
    import_stray_scanner(tmp_path / "s", tmp_path / "c", keyframes=False, rgb_size=(1440, 1920))
    cj = json.loads((tmp_path / "c" / "capture.json").read_text(encoding="utf-8"))
    assert any("orientation" in w for w in cj["import"]["warnings"])


def test_marker_never_seen_is_reported(tmp_path):
    _export(tmp_path / "s")
    import_stray_scanner(tmp_path / "s", tmp_path / "c")
    cj = json.loads((tmp_path / "c" / "capture.json").read_text(encoding="utf-8"))
    assert not cj["import"]["anchored_to_marker"]
    assert any("marker" in w for w in cj["import"]["warnings"])


def test_inconsistent_marker_detections_are_left_out():
    rng = np.random.default_rng(0)
    base = make_T(rot_z(0.4), [1.0, 2.0, 0.0])
    good = [base @ make_T(rot_z(rng.normal(0, 0.005)), rng.normal(0, 0.005, 3)) for _ in range(6)]
    flipped = base @ make_T(Rotation.from_euler("x", 40, degrees=True).as_matrix(), [0, 0, 0])
    far = make_T(np.eye(3), [4.0, -1.0, 0.0])  # another tag-like pattern elsewhere
    keep = consistent_marker_observations(good + [flipped, far])
    assert len(keep) == 6
    assert consistent_marker_observations([]) == []


def test_all_rows_invalid_is_a_clear_error(tmp_path):
    _export(tmp_path / "s", n=12, nan_rows=set(range(12)))
    with pytest.raises(ValueError, match="finite pose"):
        import_stray_scanner(tmp_path / "s", tmp_path / "c")
