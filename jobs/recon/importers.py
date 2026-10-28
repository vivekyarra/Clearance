"""Importers for third-party ARKit recorder exports -> our Capture layout.

Stray Scanner (iOS, https://github.com/strayrobots/scanner, format in docs/format.md):
    rgb.mp4                 HEVC video of the camera frames
    depth/000000.png        uint16 depth in mm, 256x192 (LiDAR devices)
    confidence/000000.png   uint8 0/1/2
    camera_matrix.csv       3x3 intrinsics (last frame; per-frame intrinsics are in odometry.csv)
    odometry.csv            header; timestamp, frame, x, y, z, qx, qy, qz, qw[, fx, fy, cx, cy, ...]
    imu.csv                 (not used)
Pose convention (verified in StrayScanner/Helpers/OdometryEncoder.swift, Oct 2026): position and
quaternion come from ARFrame.camera.transform multiplied by a fixed 180-degree rotation about X,
i.e. the stored rotation is camera(OpenCV: x right, y down, z forward) -> ARKit world (Y up).
We convert: T_world_arkitcam = T_world_cv @ diag(1,-1,-1,1), then T_site = ARKIT_TO_SITE @ T.
The intrinsics refer to the RGB resolution; read from the video when it can be decoded, otherwise
inferred from the principal point (about (w/2, h/2)), and only then 1920x1440 is assumed (VERIFY per
device; StrayVisualizer uses the same assumption).
Odometry rows with non-finite values or a zero quaternion (tracking lost) are skipped and counted.
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import cv2
import numpy as np

from clearance_core.schemas import Capture, CaptureFiles, Device, Marker, PoseRecord
from jobs.common.camera import ARKIT_TO_SITE, CV_TO_ARKIT_CAM, K_scaled, make_T, quat_xyzw_to_R
from jobs.common.imageio import imwrite

DEFAULT_RGB_SIZE = (1920, 1440)  # VERIFY per device


def _read_odometry(path: Path):
    rows = []
    with open(path, newline="", encoding="utf-8") as f:
        rd = csv.reader(f)
        header = [h.strip() for h in next(rd)]
        for r in rd:
            if not r or not r[0].strip():
                continue
            rows.append({h: (v.strip() if i < len(r) else "") for i, (h, v) in enumerate(zip(header, r))})
    if not rows:
        raise ValueError(f"{path} has no rows")
    for k in ("timestamp", "frame", "x", "y", "z", "qx", "qy", "qz", "qw"):
        if k not in rows[0]:
            raise ValueError(f"{path}: column {k!r} missing (header: {list(rows[0])})")
    return rows


def _finite_row(r: dict) -> bool:
    try:
        v = [float(r[k]) for k in ("timestamp", "frame", "x", "y", "z", "qx", "qy", "qz", "qw")]
    except (KeyError, ValueError):
        return False
    return bool(np.all(np.isfinite(v)) and np.linalg.norm(v[5:9]) > 1e-6)


def rgb_size_from_K(K, fallback=None):
    """Image size implied by the principal point (cx, cy ~ (w - 1) / 2, (h - 1) / 2), rounded to 8 px."""
    w = int(round(2 * (K[2] + 0.5) / 8.0) * 8)
    h = int(round(2 * (K[3] + 0.5) / 8.0) * 8)
    if w < 64 or h < 64 or w > 8192 or h > 8192:
        return fallback
    return w, h


def _select_keyframes(T_list, min_trans=0.10, min_rot_deg=10.0):
    keep, last = [], None
    for i, T in enumerate(T_list):
        if last is None:
            keep.append(i)
            last = T
            continue
        dt = np.linalg.norm(T[:3, 3] - last[:3, 3])
        ang = np.degrees(np.arccos(np.clip((np.trace(last[:3, :3].T @ T[:3, :3]) - 1) / 2, -1, 1)))
        if dt >= min_trans or ang >= min_rot_deg:
            keep.append(i)
            last = T
    return keep


def detect_marker_pose(image_gray: np.ndarray, K, marker_size_m: float, marker_id: int = 0,
                       family: str = "tag36h11"):
    """AprilTag pose in the OpenCV camera frame (T_cv_marker), or None. Marker frame: origin at the tag
    center, x/y in the tag plane, z out of the tag (up when it lies on the floor)."""
    if family != "tag36h11":
        raise ValueError(f"marker family {family!r} not supported")
    det = cv2.aruco.ArucoDetector(cv2.aruco.getPredefinedDictionary(cv2.aruco.DICT_APRILTAG_36h11),
                                  cv2.aruco.DetectorParameters())
    corners, ids, _ = det.detectMarkers(image_gray)
    if ids is None:
        return None
    for c, i in zip(corners, ids.ravel()):
        if int(i) != marker_id:
            continue
        h = marker_size_m / 2
        obj = np.array([[-h, h, 0], [h, h, 0], [h, -h, 0], [-h, -h, 0]], np.float64)
        fx, fy, cx, cy = K
        Km = np.array([[fx, 0, cx], [0, fy, cy], [0, 0, 1]], np.float64)
        ok, rvec, tvec = cv2.solvePnP(obj, c.reshape(4, 2).astype(np.float64), Km, None,
                                      flags=cv2.SOLVEPNP_IPPE_SQUARE)
        if ok:
            R, _ = cv2.Rodrigues(rvec)
            return make_T(R, tvec.ravel())
    return None


def _average_rigid(Ts: list[np.ndarray]) -> np.ndarray:
    t = np.median([T[:3, 3] for T in Ts], axis=0)
    M = sum(T[:3, :3] for T in Ts)
    U, _, Vt = np.linalg.svd(M)
    R = U @ np.diag([1, 1, np.linalg.det(U @ Vt)]) @ Vt
    return make_T(R, t)


def consistent_marker_observations(Ts: list[np.ndarray], max_dist_m: float = 0.05, max_ang_deg: float = 5.0):
    """Marker poses (world <- marker) that agree with their robust average within max_dist_m / max_ang_deg.
    Single false detections and flipped PnP solutions are left out."""
    if not Ts:
        return []

    def close(A, B):
        dR = A[:3, :3].T @ B[:3, :3]
        ang = np.degrees(np.arccos(np.clip((np.trace(dR) - 1) / 2, -1, 1)))
        return np.linalg.norm(A[:3, 3] - B[:3, 3]) <= max_dist_m and ang <= max_ang_deg

    # consensus: the observation that agrees with the most others, then agreement with their average
    support = [[j for j, B in enumerate(Ts) if close(A, B)] for A in Ts]
    best = max(range(len(Ts)), key=lambda i: len(support[i]))
    ref = _average_rigid([Ts[j] for j in support[best]])
    keep = [T for T in Ts if close(ref, T)]
    return keep


def import_stray_scanner(src_dir: str | Path, out_dir: str | Path, *, site_id: str = "site_import",
                         capture_id: str | None = None, keyframes: bool = True, frame_size=None,
                         rgb_size: tuple[int, int] | None = None, marker: Marker | None = None,
                         anchor_marker: bool = True, device_model: str = "iPhone (Stray Scanner)") -> str:
    """Convert a Stray Scanner export to a Capture directory. Returns out_dir.

    If anchor_marker and the AprilTag is seen in >= 3 frames, the site frame is re-anchored to the
    marker (origin at its center, Z up); otherwise the frame is ARKit's (converted to Z up) and the
    floor fit in recon fixes height and tilt. Import details are written to capture.json["import"]."""
    src, out = Path(src_dir), Path(out_dir)
    all_rows = _read_odometry(src / "odometry.csv")
    rows = [r for r in all_rows if _finite_row(r)]
    if len(rows) < 2:
        raise ValueError(f"{src / 'odometry.csv'}: fewer than 2 rows with a finite pose")
    marker = marker or Marker()
    K_file = None
    if (src / "camera_matrix.csv").is_file():
        M = np.loadtxt(src / "camera_matrix.csv", delimiter=",")
        K_file = (M[0, 0], M[1, 1], M[0, 2], M[1, 2])

    cap_video = cv2.VideoCapture(str(src / "rgb.mp4")) if (src / "rgb.mp4").is_file() else None
    video_ok = cap_video is not None and cap_video.isOpened()
    import_warnings = []
    if len(rows) < len(all_rows):
        import_warnings.append(f"{len(all_rows) - len(rows)} odometry rows without a finite pose were skipped")
    K0 = None
    if rows[0].get("fx"):
        K0 = tuple(float(rows[0][k]) for k in ("fx", "fy", "cx", "cy"))
    elif K_file is not None:
        K0 = K_file
    k_size = rgb_size_from_K(K0) if K0 is not None else None
    size_source = "argument"
    if rgb_size is None:
        if video_ok and cap_video.get(cv2.CAP_PROP_FRAME_WIDTH) > 0:
            rgb_size = (int(cap_video.get(cv2.CAP_PROP_FRAME_WIDTH)), int(cap_video.get(cv2.CAP_PROP_FRAME_HEIGHT)))
            size_source = "video"
        elif k_size is not None:
            rgb_size, size_source = k_size, "principal_point"
        else:
            rgb_size, size_source = DEFAULT_RGB_SIZE, "default"
    if k_size is not None and (abs(k_size[0] / rgb_size[0] - 1) > 0.1 or abs(k_size[1] / rgb_size[1] - 1) > 0.1):
        import_warnings.append(f"intrinsics principal point suggests a {k_size[0]}x{k_size[1]} image but the RGB "
                               f"size is {rgb_size[0]}x{rgb_size[1]} ({size_source}); check orientation")

    if frame_size is None:  # 640 px wide, aspect of the RGB stream
        frame_size = (640, int(round(640 * rgb_size[1] / rgb_size[0])))
    T_site, Ks, ts, frame_ids = [], [], [], []
    for r in rows:
        R_w_cv = quat_xyzw_to_R(np.array([float(r[k]) for k in ("qx", "qy", "qz", "qw")]))
        T_w_cv = make_T(R_w_cv, [float(r["x"]), float(r["y"]), float(r["z"])])
        T_site.append(ARKIT_TO_SITE @ T_w_cv @ CV_TO_ARKIT_CAM)
        if r.get("fx"):
            Ks.append((float(r["fx"]), float(r["fy"]), float(r["cx"]), float(r["cy"])))
        elif K_file is not None:
            Ks.append(K_file)
        else:
            raise ValueError("no intrinsics: odometry.csv lacks fx..cy and camera_matrix.csv is missing")
        ts.append(float(r["timestamp"]))
        frame_ids.append(int(float(r["frame"])))

    sel = _select_keyframes(T_site) if keyframes else list(range(len(rows)))
    has_depth = (src / "depth").is_dir()
    has_conf = has_depth and (src / "confidence").is_dir()
    if has_depth and not has_conf:
        import_warnings.append("no confidence maps: low-confidence LiDAR pixels cannot be filtered")
    for d in ("frames",) + ("depth",) * has_depth + ("confidence",) * has_conf:
        (out / d).mkdir(parents=True, exist_ok=True)

    # decode the selected video frames (sequential read; Stray frame index = video frame index)
    images: dict[int, np.ndarray] = {}
    if video_ok:
        want = {frame_ids[i] for i in sel}
        idx = 0
        while want:
            ok, img = cap_video.read()
            if not ok:
                break
            if idx in want:
                images[idx] = cv2.resize(img, frame_size, interpolation=cv2.INTER_AREA)
                want.discard(idx)
            idx += 1
        cap_video.release()
    fw, fh = frame_size if images else rgb_size

    marker_obs = []
    poses = []
    for n, i in enumerate(sel):
        fid = frame_ids[i]
        name = f"{n:06d}"
        K_img = K_scaled(Ks[i], fw / rgb_size[0], fh / rgb_size[1])
        if fid in images:
            imwrite(out / "frames" / f"{name}.jpg", images[fid], [cv2.IMWRITE_JPEG_QUALITY, 85])
            if anchor_marker:
                Tcm = detect_marker_pose(cv2.cvtColor(images[fid], cv2.COLOR_BGR2GRAY), K_img, marker.size_m, marker.id,
                                         marker.family)
                if Tcm is not None:
                    marker_obs.append(T_site[i] @ CV_TO_ARKIT_CAM @ Tcm)  # world <- cv cam <- marker
        if has_depth:
            for sub in ("depth",) + ("confidence",) * has_conf:
                p = src / sub / f"{fid:06d}.png"
                if p.is_file():
                    (out / sub / f"{name}.png").write_bytes(p.read_bytes())
        poses.append((name, i, K_img))

    anchor = np.eye(4)
    good_obs = consistent_marker_observations(marker_obs)
    anchored = len(good_obs) >= 3
    if anchored:
        anchor = np.linalg.inv(_average_rigid(good_obs))
    elif anchor_marker:
        import_warnings.append(f"marker {marker.family} id {marker.id} seen consistently in {len(good_obs)} frames "
                               f"(need 3): the site frame is ARKit's, not the marker's")
    recs = [PoseRecord(t=round(ts[i] - ts[sel[0]], 4), frame=f"{name}.jpg",
                       T_world_cam=[float(v) for v in (anchor @ T_site[i]).ravel()], K=list(K_img), w=int(fw), h=int(fh),
                       tracking="normal") for name, i, K_img in poses]
    (out / "poses.jsonl").write_text("\n".join(r.model_dump_json() for r in recs) + "\n", encoding="utf-8")
    files = CaptureFiles(frames="frames" if images else None, poses="poses.jsonl",
                         depth="depth" if has_depth else None, confidence="confidence" if has_conf else None)
    cap = Capture(capture_id=capture_id or f"cap_stray_{src.name}", site_id=site_id,
                  device=Device(model=device_model, os="", tier="T1_LIDAR" if has_depth else "T2_VIO_MONO"),
                  role="scanner", marker=marker, files=files, duration_s=round(ts[-1] - ts[0], 2))
    cj = cap.model_dump(mode="json")
    cj["import"] = {"source": "stray_scanner", "src": str(src), "n_odometry_rows": len(all_rows),
                    "n_rows_used": len(rows), "n_keyframes": len(sel),
                    "rgb_size": list(rgb_size), "rgb_size_source": size_source, "video_decoded": bool(images),
                    "marker_detections": len(marker_obs), "marker_detections_consistent": len(good_obs),
                    "anchored_to_marker": anchored, "warnings": import_warnings,
                    "tracking_note": "Stray Scanner does not export ARKit tracking state; all frames marked normal"}
    (out / "capture.json").write_text(json.dumps(cj, indent=2), encoding="utf-8")
    return str(out)
