"""Stray Scanner importer on a small constructed export (poses + depth + a video showing the marker)."""
import json

import cv2
import numpy as np
import pytest
from scipy.spatial.transform import Rotation

from jobs.common.camera import ARKIT_TO_SITE, CV_TO_ARKIT_CAM, look_rotation, make_T, rot_z
from jobs.recon.importers import import_stray_scanner
from jobs.recon.ingest import load_capture

VW, VH = 640, 480
K = (520.0, 520.0, 319.5, 239.5)
MARKER = 0.16


def _site_poses(n=24):
    Ts = []
    for i in range(n):
        ang = -0.6 + 1.2 * i / (n - 1)
        r = 1.0 + 0.2 * np.sin(i)
        p = np.array([r * np.cos(ang) - 0.1, r * np.sin(ang), 1.1])
        yaw = np.arctan2(-p[1], -p[0])
        pitch = -np.arctan2(p[2], np.hypot(p[0], p[1]))
        Ts.append(make_T(look_rotation(yaw, pitch), p))
    return Ts


def _marker_image(T_site_cam):
    tag = cv2.aruco.generateImageMarker(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11), 0, 200)
    T_cv_site = np.linalg.inv(T_site_cam @ CV_TO_ARKIT_CAM)
    h = MARKER / 2
    obj = np.array([[-h, h, 0, 1], [h, h, 0, 1], [h, -h, 0, 1], [-h, -h, 0, 1]]).T
    pc = (T_cv_site @ obj)[:3]
    uv = np.stack([K[0] * pc[0] / pc[2] + K[2], K[1] * pc[1] / pc[2] + K[3]], axis=1).astype(np.float32)
    src = np.array([[0, 0], [200, 0], [200, 200], [0, 200]], np.float32)
    Hm = cv2.getPerspectiveTransform(src, uv)
    img = cv2.warpPerspective(tag, Hm, (VW, VH), borderMode=cv2.BORDER_CONSTANT, borderValue=255)
    return cv2.cvtColor(img, cv2.COLOR_GRAY2BGR)


def _export(root, with_video=True, extra_cols=True):
    root.mkdir(parents=True, exist_ok=True)
    (root / "depth").mkdir()
    (root / "confidence").mkdir()
    G = make_T(rot_z(0.7), [1.0, 2.0, -1.3])  # ARKit session origin is not the marker
    Ts = _site_poses()
    hdr = "timestamp, frame, x, y, z, qx, qy, qz, qw" + (", fx, fy, cx, cy" if extra_cols else "")
    lines = [hdr]
    writer = None
    if with_video:
        writer = cv2.VideoWriter(str(root / "rgb.mp4"), cv2.VideoWriter_fourcc(*"mp4v"), 10, (VW, VH))
        if not writer.isOpened():
            pytest.skip("no mp4 encoder in this OpenCV build")
    for i, T in enumerate(Ts):
        T_ark_cv = np.linalg.inv(ARKIT_TO_SITE) @ G @ T @ CV_TO_ARKIT_CAM
        q = Rotation.from_matrix(T_ark_cv[:3, :3]).as_quat()  # x, y, z, w
        row = [f"{0.1 * i:.3f}", f"{i}", *(f"{v:.6f}" for v in T_ark_cv[:3, 3]), *(f"{v:.8f}" for v in q)]
        if extra_cols:
            row += [f"{v}" for v in K]
        lines.append(", ".join(row))
        cv2.imwrite(str(root / "depth" / f"{i:06d}.png"), np.full((192, 256), 1500, np.uint16))
        cv2.imwrite(str(root / "confidence" / f"{i:06d}.png"), np.full((192, 256), 2, np.uint8))
        if writer is not None:
            writer.write(_marker_image(T))
    if writer is not None:
        writer.release()
    (root / "odometry.csv").write_text("\n".join(lines) + "\n")
    M = np.array([[K[0], 0, K[2]], [0, K[1], K[3]], [0, 0, 1]])
    np.savetxt(root / "camera_matrix.csv", M, delimiter=",")
    return Ts, G


def test_import_anchors_to_marker(tmp_path):
    Ts, _ = _export(tmp_path / "stray")
    out = import_stray_scanner(tmp_path / "stray", tmp_path / "cap", keyframes=False, frame_size=(VW, VH))
    cj = json.loads((tmp_path / "cap" / "capture.json").read_text())
    if not cj["import"]["video_decoded"]:
        pytest.skip("mp4 written but not decodable here")
    assert cj["import"]["anchored_to_marker"] and cj["import"]["marker_detections"] >= 20
    cd = load_capture(out)
    assert cd.tier == "T1_LIDAR" and len(cd.poses) == len(Ts)
    for i, T in enumerate(Ts):
        Ti = cd.T(i)
        # marker PnP on a compressed video frame: site origin within ~2 cm (floor fit later removes height/tilt)
        assert np.linalg.norm(Ti[:2, 3] - T[:2, 3]) < 0.025
        assert abs(Ti[2, 3] - T[2, 3]) < 0.04
        dR = Ti[:3, :3].T @ T[:3, :3]
        assert np.degrees(np.arccos(np.clip((np.trace(dR) - 1) / 2, -1, 1))) < 1.0
    assert cd.poses[0].K == pytest.approx(list(K), abs=1.0) and cd.poses[0].w == VW
    assert (tmp_path / "cap" / "depth" / "000003.png").is_file()


def test_import_without_video_keeps_arkit_frame_z_up(tmp_path):
    Ts, G = _export(tmp_path / "stray", with_video=False, extra_cols=False)
    out = import_stray_scanner(tmp_path / "stray", tmp_path / "cap", keyframes=True, rgb_size=(VW, VH))
    cd = load_capture(out)
    cj = json.loads((tmp_path / "cap" / "capture.json").read_text())
    assert not cj["import"]["anchored_to_marker"] and cd.capture.files.frames is None
    assert 3 <= len(cd.poses) < len(Ts)  # motion-based keyframes
    T0 = cd.T(0)
    assert np.allclose(T0, G @ Ts[0], atol=1e-5)  # ARKit world converted to Z up, origin unchanged
    # camera looks down: its -Z axis has a negative world z component
    assert (-T0[:3, 2])[2] < 0


def test_import_rejects_bad_odometry(tmp_path):
    (tmp_path / "s").mkdir()
    (tmp_path / "s" / "odometry.csv").write_text("timestamp, frame, x\n0, 0, 1\n")
    with pytest.raises(ValueError, match="column"):
        import_stray_scanner(tmp_path / "s", tmp_path / "o")
