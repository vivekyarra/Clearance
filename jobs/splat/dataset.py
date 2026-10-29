"""Build splat-training datasets from our ARKit poses.

Two layouts are written side by side:
* nerfstudio `transforms.json`: per-frame camera-to-world in the OpenGL/Blender camera convention
  (x right, y up, z back). That is ARKit's camera convention, so `transform_matrix = T_world_cam`.
* COLMAP text model `sparse/0/{cameras,images,points3D}.txt` (used by gsplat's trainers): world-to-camera
  in the OpenCV convention (x right, y down, z forward): R|t = inverse(T_world_cam @ diag(1,-1,-1,1)),
  stored as quaternion (qw, qx, qy, qz) + translation.
Poses are already in the site frame (Z up, floor Z=0), so the splat lands aligned with the twin.
"""
from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Sequence

import numpy as np

from clearance_core.schemas import PoseRecord
from jobs.semantics.camera import arkit_to_opencv_c2w, as_matrix, invert_rigid, rotmat_to_qvec
from jobs.semantics.capture_io import load_capture, load_feature_points, load_poses, write_ply_xyz

log = logging.getLogger(__name__)


@dataclass
class FrameCamera:
    name: str
    path: Path
    T_world_cam_arkit: np.ndarray
    fx: float
    fy: float
    cx: float
    cy: float
    w: int
    h: int

    @property
    def T_cv_world(self) -> np.ndarray:
        return invert_rigid(arkit_to_opencv_c2w(self.T_world_cam_arkit))


def colmap_extrinsics(T_world_cam_arkit) -> tuple[np.ndarray, np.ndarray]:
    """(qvec wxyz, tvec) of the COLMAP world-to-camera transform for an ARKit camera-to-world pose."""
    T = invert_rigid(arkit_to_opencv_c2w(as_matrix(T_world_cam_arkit)))
    return rotmat_to_qvec(T[:3, :3]), T[:3, 3].copy()


def _image_size(path: Path) -> Optional[tuple[int, int]]:
    try:
        from PIL import Image
        with Image.open(path) as im:
            return im.size
    except Exception:
        return None


def frame_cameras(capture_dir: str | Path, poses: Sequence[PoseRecord], frames_subdir: str,
                  include_limited: bool = False) -> list[FrameCamera]:
    """Cameras for frames on disk, intrinsics rescaled to each image's real size."""
    out = []
    for p in poses:
        if p.tracking != "normal" and not include_limited:
            continue
        path = Path(capture_dir) / frames_subdir / p.frame
        size = _image_size(path)
        if size is None:
            log.warning("skipping %s (missing or unreadable)", path)
            continue
        w, h = size
        sx, sy = w / p.w, h / p.h
        fx, fy, cx, cy = p.K
        out.append(FrameCamera(p.frame, path, as_matrix(p.T_world_cam), fx * sx, fy * sy, cx * sx, cy * sy, w, h))
    return out


def transforms_json(cams: Sequence[FrameCamera], image_root: Path, ply_name: Optional[str] = None) -> dict:
    frames = []
    for c in cams:
        try:
            rel = c.path.relative_to(image_root).as_posix()
        except ValueError:
            rel = c.path.resolve().as_posix()
        frames.append({"file_path": rel, "transform_matrix": c.T_world_cam_arkit.tolist(),
                       "fl_x": c.fx, "fl_y": c.fy, "cx": c.cx, "cy": c.cy, "w": c.w, "h": c.h})
    d = {"camera_model": "OPENCV", "k1": 0.0, "k2": 0.0, "p1": 0.0, "p2": 0.0, "frames": frames,
         "coordinate_note": "transform_matrix = camera-to-world, OpenGL camera axes (= ARKit); world = site frame, Z up"}
    if ply_name:
        d["ply_file_path"] = ply_name
    return d


def write_colmap_text(cams: Sequence[FrameCamera], sparse_dir: Path, points: Optional[np.ndarray] = None,
                      colors: Optional[np.ndarray] = None) -> None:
    sparse_dir.mkdir(parents=True, exist_ok=True)
    intr: dict[tuple, int] = {}
    cam_lines, img_lines = [], []
    for c in cams:
        key = (c.w, c.h, round(c.fx, 3), round(c.fy, 3), round(c.cx, 3), round(c.cy, 3))
        if key not in intr:
            intr[key] = len(intr) + 1
            cam_lines.append(f"{intr[key]} PINHOLE {c.w} {c.h} {c.fx:.6f} {c.fy:.6f} {c.cx:.6f} {c.cy:.6f}")
    for i, c in enumerate(cams, start=1):
        q, t = colmap_extrinsics(c.T_world_cam_arkit)
        key = (c.w, c.h, round(c.fx, 3), round(c.fy, 3), round(c.cx, 3), round(c.cy, 3))
        img_lines.append(f"{i} {q[0]:.9f} {q[1]:.9f} {q[2]:.9f} {q[3]:.9f} {t[0]:.9f} {t[1]:.9f} {t[2]:.9f} "
                         f"{intr[key]} {c.name}")
        img_lines.append("")  # no 2D-3D correspondences
    (sparse_dir / "cameras.txt").write_text(
        "# CAMERA_ID, MODEL, WIDTH, HEIGHT, PARAMS[]\n" + "\n".join(cam_lines) + "\n", encoding="utf-8")
    (sparse_dir / "images.txt").write_text(
        "# IMAGE_ID, QW, QX, QY, QZ, TX, TY, TZ, CAMERA_ID, NAME\n# POINTS2D[] as (X, Y, POINT3D_ID)\n"
        + "\n".join(img_lines) + "\n", encoding="utf-8")
    lines = ["# POINT3D_ID, X, Y, Z, R, G, B, ERROR, TRACK[] as (IMAGE_ID, POINT2D_IDX)"]
    if points is not None:
        cols = colors if colors is not None else np.full((len(points), 3), 128)
        for k, (p, col) in enumerate(zip(points, cols), start=1):
            lines.append(f"{k} {p[0]:.6f} {p[1]:.6f} {p[2]:.6f} {int(col[0])} {int(col[1])} {int(col[2])} 0")
    (sparse_dir / "points3D.txt").write_text("\n".join(lines) + "\n", encoding="utf-8")


def init_points(capture_dir, capture, cams: Sequence[FrameCamera], cloud: Optional[np.ndarray] = None,
                max_points: int = 100_000, seed: int = 0) -> np.ndarray:
    """Initial gaussian centres: given cloud > ARKit feature points > random points in the camera hull."""
    rng = np.random.default_rng(seed)
    pts = cloud if cloud is not None else load_feature_points(capture_dir, capture)
    if pts is None or len(pts) == 0:
        c = np.array([cam.T_world_cam_arkit[:3, 3] for cam in cams])
        lo, hi = c.min(axis=0) - 2.0, c.max(axis=0) + 2.0
        lo[2], hi[2] = 0.0, max(hi[2], 2.5)
        pts = rng.uniform(lo, hi, size=(min(max_points, 20_000), 3))
    if len(pts) > max_points:
        pts = pts[rng.choice(len(pts), max_points, replace=False)]
    return np.asarray(pts, dtype=np.float64)


def prepare_dataset(capture_dir: str | Path, out_dir: str | Path, *, cloud: Optional[np.ndarray] = None,
                    copy_images: bool = False, include_limited: bool = False) -> dict:
    """Writes transforms.json + sparse/0/*.txt + points.ply into out_dir. Returns a summary."""
    cdir, out = Path(capture_dir), Path(out_dir)
    capture = load_capture(cdir)
    if not capture.files.frames:
        raise ValueError("capture has no frames directory")
    poses = load_poses(cdir, capture)
    cams = frame_cameras(cdir, poses, capture.files.frames, include_limited=include_limited)
    if len(cams) < 2:
        raise ValueError(f"need at least 2 posed frames on disk, found {len(cams)}")
    out.mkdir(parents=True, exist_ok=True)
    image_root = out
    if copy_images:
        img_dir = out / "images"
        img_dir.mkdir(exist_ok=True)
        for c in cams:
            dst = img_dir / c.name
            if not dst.exists():
                shutil.copy2(c.path, dst)
            c.path = dst
    pts = init_points(cdir, capture, cams, cloud)
    write_ply_xyz(out / "points.ply", pts)
    (out / "transforms.json").write_text(json.dumps(transforms_json(cams, image_root, "points.ply"), indent=1),
                                         encoding="utf-8")
    write_colmap_text(cams, out / "sparse" / "0", pts)
    summary = {"n_frames": len(cams), "n_points": int(len(pts)), "out_dir": str(out),
               "image_dir": str((out / "images") if copy_images else (cdir / capture.files.frames))}
    (out / "dataset.json").write_text(json.dumps(summary, indent=1), encoding="utf-8")
    return summary
