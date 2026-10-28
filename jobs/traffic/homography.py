"""Image <-> floor (Z=0) homography for a fixed, marker-localized observer camera (PLAN 7.5 step 3).

observer_pose.json: {"T_site_cam": [16 row-major], "K": [fx, fy, cx, cy], "w": W, "h": H,
                     "convention": "arkit" | "opencv" (optional)}
T_site_cam is camera-to-site. solvePnP-based localizers naturally produce an OpenCV camera frame,
while scanner poses use ARKit's. When "convention" is absent we detect it: the two readings
point the camera in opposite directions, and only one of them sends the bottom-centre pixel ray
down toward the floor.

Near the horizon the mapping blows up: one pixel of foot-point error moves the floor point by
about d^2 / (camera height * f) metres at distance d (0.8 m per pixel at 25 m for a level camera
1.6 m high). Floor points whose one-pixel sensitivity exceeds `max_m_per_px` are therefore
invalid, as are cameras lower than `min_height_m` (everything would collapse onto the camera).
"""
from __future__ import annotations

import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

import numpy as np

from jobs.semantics.camera import K_matrix, PinholeCamera, arkit_to_opencv_c2w, as_matrix

log = logging.getLogger(__name__)


def floor_homography(cam: PinholeCamera) -> np.ndarray:
    """H (3x3) mapping floor points (x, y, 1) on Z=0 to homogeneous pixels: p ~ K [r1 r2 t] X."""
    T = cam.T_cv_world
    M = np.c_[T[:3, 0], T[:3, 1], T[:3, 3]]
    return cam.K @ M


@dataclass
class ObserverCamera:
    cam: PinholeCamera
    convention: str
    max_range_m: float = 25.0
    max_m_per_px: float = 0.5
    min_height_m: float = 0.2

    def __post_init__(self):
        T = np.asarray(self.cam.T_world_cv, dtype=np.float64)
        if not np.isfinite(T).all() or not np.isfinite(self.cam.K).all():
            raise ValueError("observer pose / intrinsics contain NaN or inf")
        if abs(np.linalg.det(T[:3, :3]) - 1.0) > 1e-3:
            raise ValueError("observer pose rotation is not a proper rotation matrix")
        if self.cam.center[2] <= 0:
            raise ValueError("observer camera must be above the floor (Z > 0)")
        if self.cam.center[2] < self.min_height_m:
            raise ValueError(f"observer camera is {self.cam.center[2]:.3f} m above the floor; "
                             f"at least {self.min_height_m} m is needed to map feet to the floor")
        self.H_floor_to_img = floor_homography(self.cam)
        self.H_img_to_floor = np.linalg.inv(self.H_floor_to_img)

    @classmethod
    def from_pose(cls, T_site_cam, K, w, h, convention: Optional[str] = None, **kw) -> "ObserverCamera":
        T = as_matrix(T_site_cam)
        Km = K_matrix(K)
        if convention is None:
            convention = detect_convention(T, Km, int(w), int(h))
        convention = convention.lower()
        if convention not in ("arkit", "opencv"):
            raise ValueError(f"unknown camera convention {convention!r}")
        T_cv = arkit_to_opencv_c2w(T) if convention == "arkit" else T
        return cls(PinholeCamera(T_cv, Km, int(w), int(h)), convention, **kw)

    @classmethod
    def from_json(cls, data: dict, **kw) -> "ObserverCamera":
        return cls.from_pose(data["T_site_cam"], data["K"], data["w"], data["h"], data.get("convention"), **kw)

    @classmethod
    def from_file(cls, path: str | Path, **kw) -> "ObserverCamera":
        return cls.from_json(json.loads(Path(path).read_text(encoding="utf-8")), **kw)

    def scaled(self, w: int, h: int) -> "ObserverCamera":
        """Same camera for video decoded at a different resolution than the calibration."""
        if (w, h) == (self.cam.w, self.cam.h):
            return self
        return ObserverCamera(self.cam.scaled(w, h), self.convention, self.max_range_m, self.max_m_per_px,
                              self.min_height_m)

    def _project(self, uv: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        p = np.c_[uv, np.ones(len(uv))] @ self.H_img_to_floor.T
        w = p[:, 2]
        with np.errstate(divide="ignore", invalid="ignore", over="ignore"):
            xy = p[:, :2] / w[:, None]
        ok = (w > 1e-12) & np.isfinite(xy).all(axis=1)  # w = 1/depth for floor points in front of the camera
        return xy, ok

    def image_to_floor(self, uv: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        """Pixels (N,2) -> floor xy (N,2) and validity (finite, in front of the camera, below the
        horizon, in range, one-pixel sensitivity <= max_m_per_px). Invalid rows are NaN."""
        uv = np.asarray(uv, dtype=np.float64).reshape(-1, 2)
        xy, valid = self._project(uv)
        valid &= np.isfinite(uv).all(axis=1)
        with np.errstate(invalid="ignore", over="ignore"):
            dist = np.linalg.norm(xy - self.cam.center[None, :2], axis=1)
            valid &= np.isfinite(dist) & (dist <= self.max_range_m)
            if self.max_m_per_px is not None and valid.any():
                sens = np.zeros(len(uv))
                for d in ((1.0, 0.0), (0.0, 1.0), (0.0, -1.0)):
                    xy2, ok2 = self._project(uv + np.asarray(d))
                    step = np.linalg.norm(xy2 - xy, axis=1)
                    sens = np.maximum(sens, np.where(ok2 & np.isfinite(step), step, np.inf))
                valid &= sens <= self.max_m_per_px
        xy = np.where(valid[:, None], xy, np.nan)
        return xy, valid

    def floor_to_image(self, xy: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
        xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
        p = np.c_[xy, np.ones(len(xy))] @ self.H_floor_to_img.T
        with np.errstate(divide="ignore", invalid="ignore"):
            uv = p[:, :2] / p[:, 2:3]
        valid = (p[:, 2] > 1e-12) & (uv[:, 0] >= 0) & (uv[:, 0] < self.cam.w) & (uv[:, 1] >= 0) & (uv[:, 1] < self.cam.h)
        return uv, valid


def detect_convention(T_site_cam: np.ndarray, K: np.ndarray, w: int, h: int) -> str:
    """'arkit' or 'opencv': whichever makes the bottom-centre pixel's ray point down at the floor."""
    x = (w / 2 - K[0, 2]) / K[0, 0]
    y = (h - 1 - K[1, 2]) / K[1, 1]
    R = T_site_cam[:3, :3]
    d_cv = R @ np.array([x, y, 1.0])        # ray if the matrix is OpenCV-framed
    d_ar = R @ np.array([x, -y, -1.0])      # ray if ARKit-framed (camera y and z flipped)
    dz_cv, dz_ar = d_cv[2] / np.linalg.norm(d_cv), d_ar[2] / np.linalg.norm(d_ar)
    if abs(dz_cv - dz_ar) < 1e-9:
        log.warning("observer camera convention ambiguous (horizontal view); assuming arkit")
        return "arkit"
    return "opencv" if dz_cv < dz_ar else "arkit"
