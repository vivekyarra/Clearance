"""Reading Capture directories and twin point clouds. Shared by J3/J4/J5."""
from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from clearance_core import storage
from clearance_core.schemas import Capture, CaptureFiles, Device, PoseRecord, Twin

log = logging.getLogger(__name__)

MANIFEST_NAMES = ("capture.json", "manifest.json", "meta.json")


def load_capture(capture_dir: str | Path) -> Capture:
    """Read the Capture manifest. If none exists, infer a minimal one from the files present."""
    d = Path(capture_dir)
    for name in MANIFEST_NAMES:
        p = d / name
        if p.exists():
            return Capture.model_validate(json.loads(p.read_text(encoding="utf-8")))
    files = CaptureFiles()
    if (d / "frames").is_dir():
        files.frames = "frames"
    if (d / "depth").is_dir():
        files.depth = "depth"
    if (d / "confidence").is_dir():
        files.confidence = "confidence"
    if (d / "feature_points.jsonl").exists():
        files.feature_points = "feature_points.jsonl"
    for v in ("observer.mp4", "observer.webm", "video.mp4"):
        if (d / v).exists():
            files.video = v
    if (d / "observer_pose.json").exists():
        files.observer_pose = "observer_pose.json"
    observer = files.observer_pose is not None
    if not (d / "poses.jsonl").exists():
        files.poses = None
    log.warning("no capture manifest in %s; inferred one", d)
    return Capture(
        capture_id=d.name, site_id="unknown",
        device=Device(model="unknown", tier="T3_FIXED_OBSERVER" if observer else "T2_VIO_MONO"),
        role="observer" if observer else "scanner", files=files)


def load_poses(capture_dir: str | Path, capture: Optional[Capture] = None) -> list[PoseRecord]:
    d = Path(capture_dir)
    capture = capture or load_capture(d)
    if not capture.files.poses:
        return []
    p = d / capture.files.poses
    if not p.exists():
        return []
    out = []
    for line in p.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line:
            out.append(PoseRecord.model_validate_json(line))
    return out


def frame_path(capture_dir, capture: Capture, frame: str) -> Optional[Path]:
    if not capture.files.frames:
        return None
    p = Path(capture_dir) / capture.files.frames / frame
    return p if p.exists() else None


def read_frame(capture_dir, capture: Capture, frame: str) -> Optional[np.ndarray]:
    p = frame_path(capture_dir, capture, frame)
    return None if p is None else cv2.imread(str(p), cv2.IMREAD_COLOR)


def _sibling_png(capture_dir, sub: Optional[str], frame: str) -> Optional[Path]:
    if not sub:
        return None
    base = Path(capture_dir) / sub
    for cand in (base / (Path(frame).stem + ".png"), base / frame):
        if cand.exists():
            return cand
    return None


def read_depth(capture_dir, capture: Capture, frame: str, min_conf: int = 1) -> Optional[np.ndarray]:
    """Depth in meters (float32, 0 = invalid). Pixels below ARKit confidence `min_conf` are zeroed."""
    p = _sibling_png(capture_dir, capture.files.depth, frame)
    if p is None:
        return None
    raw = cv2.imread(str(p), cv2.IMREAD_UNCHANGED)
    if raw is None:
        return None
    depth = raw.astype(np.float32) / 1000.0
    cp = _sibling_png(capture_dir, capture.files.confidence, frame)
    if cp is not None and min_conf > 0:
        conf = cv2.imread(str(cp), cv2.IMREAD_UNCHANGED)
        if conf is not None:
            if conf.shape != depth.shape:
                conf = cv2.resize(conf, depth.shape[::-1], interpolation=cv2.INTER_NEAREST)
            depth[conf < min_conf] = 0.0
    return depth


def load_feature_points(capture_dir, capture: Capture) -> Optional[np.ndarray]:
    if not capture.files.feature_points:
        return None
    p = Path(capture_dir) / capture.files.feature_points
    if not p.exists():
        return None
    pts = []
    for line in p.read_text(encoding="utf-8").splitlines():
        if line.strip():
            pts.extend(json.loads(line).get("points_world", []))
    return np.asarray(pts, dtype=np.float64).reshape(-1, 3) if pts else None


def read_ply_xyz(path: str | Path) -> np.ndarray:
    """Minimal PLY reader for vertex x,y,z (ascii or binary_little_endian, any extra properties)."""
    data = Path(path).read_bytes()
    end = data.find(b"end_header")
    if end < 0:
        raise ValueError("not a PLY file")
    header = data[:end].decode("ascii", errors="replace").splitlines()
    body = data[data.index(b"\n", end) + 1:]
    fmt, n, props, in_vertex = "ascii", 0, [], False
    types = {"char": "i1", "uchar": "u1", "short": "i2", "ushort": "u2", "int": "i4", "uint": "u4",
             "float": "f4", "double": "f8", "int8": "i1", "uint8": "u1", "int16": "i2", "uint16": "u2",
             "int32": "i4", "uint32": "u4", "float32": "f4", "float64": "f8"}
    for line in header:
        tok = line.split()
        if not tok:
            continue
        if tok[0] == "format":
            fmt = tok[1]
        elif tok[0] == "element":
            in_vertex = tok[1] == "vertex"
            if in_vertex:
                n = int(tok[2])
        elif tok[0] == "property" and in_vertex:
            if tok[1] == "list":
                raise ValueError("list properties in vertex element not supported")
            props.append((tok[2], types[tok[1]]))
    names = [p[0] for p in props]
    if fmt == "ascii":
        arr = np.loadtxt(body.decode("ascii").splitlines()[:n], ndmin=2)
        cols = [names.index(c) for c in ("x", "y", "z")]
        return arr[:, cols].astype(np.float64)
    if fmt != "binary_little_endian":
        raise ValueError(f"unsupported PLY format {fmt}")
    dt = np.dtype([(nm, "<" + t) for nm, t in props])
    rec = np.frombuffer(body, dtype=dt, count=n)
    return np.stack([rec["x"], rec["y"], rec["z"]], axis=1).astype(np.float64)


def write_ply_xyz(path: str | Path, pts: np.ndarray) -> None:
    pts = np.asarray(pts, dtype=np.float32).reshape(-1, 3)
    header = (f"ply\nformat binary_little_endian 1.0\nelement vertex {len(pts)}\n"
              "property float x\nproperty float y\nproperty float z\nend_header\n").encode("ascii")
    Path(path).write_bytes(header + pts.tobytes())


CLOUD_ASSET_KEYS = ("pointcloud", "point_cloud", "points", "cloud", "pointcloud_ply", "pcd")


def load_cloud_file(path: Path) -> Optional[np.ndarray]:
    suf = path.suffix.lower()
    try:
        if suf == ".ply":
            return read_ply_xyz(path)
        if suf == ".npy":
            return np.load(path).astype(np.float64)[:, :3]
        if suf == ".npz":
            z = np.load(path)
            return z[z.files[0]].astype(np.float64)[:, :3]
    except Exception as e:  # corrupt / unsupported file: callers fall back to other sources
        log.warning("could not read point cloud %s: %s", path, e)
    return None


def load_twin_cloud(twin: Twin) -> Optional[np.ndarray]:
    """Point cloud exported by recon (J2), located via twin.assets. None if absent."""
    keys = [k for k in CLOUD_ASSET_KEYS if twin.assets.get(k)]
    keys += [k for k, v in twin.assets.items() if v and k not in keys
             and str(v).lower().endswith((".ply", ".npy", ".npz")) and "splat" not in k.lower()
             and "mesh" not in k.lower()]
    for k in keys:
        uri = str(twin.assets[k])
        p = Path(uri) if Path(uri).is_absolute() else storage.DATA / uri
        if p.exists():
            cloud = load_cloud_file(p)
            if cloud is not None and len(cloud):
                return cloud
    return None


def cloud_from_depth(capture_dir, capture: Capture, poses: list[PoseRecord], stride: int = 8,
                     max_depth: float = 5.0) -> Optional[np.ndarray]:
    """Back-project (subsampled) depth maps of the given poses into a world point cloud."""
    from jobs.semantics.camera import PinholeCamera

    chunks = []
    for p in poses:
        depth = read_depth(capture_dir, capture, p.frame)
        if depth is None:
            continue
        cam = PinholeCamera.from_pose_record(p).scaled(depth.shape[1], depth.shape[0])
        vs, us = np.mgrid[0:depth.shape[0]:stride, 0:depth.shape[1]:stride]
        z = depth[vs, us]
        ok = (z > 0.1) & (z < max_depth)
        if ok.any():
            uv = np.stack([us[ok] + 0.5, vs[ok] + 0.5], axis=1)
            chunks.append(cam.backproject(uv, z[ok]))
    return np.concatenate(chunks) if chunks else None
