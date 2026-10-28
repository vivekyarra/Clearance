"""Camera conventions shared by jobs.reference (rendering) and recon (back-projection).

Camera frame = ARKit convention: x right, y up, z backward (the camera looks along -Z).
Image pixels: u to the right, v down, origin at the top-left pixel corner; pixel centers at
integer + 0.5 are NOT used: like ARKit/OpenCV intrinsics, pixel (u, v) integer index is the center.

    depth d = -z_cam  (positive in front of the camera)
    u = cx + fx * x_cam / d
    v = cy - fy * y_cam / d

World = site frame: Z up, floor Z = 0, origin at marker center. T_world_cam is a 4x4
camera-to-world transform (row-major when flattened, as in PoseRecord).

ARKit's own world frame is Y-up (gravity). ARKIT_TO_SITE maps ARKit world -> Z-up site frame:
    site_x = ark_x, site_y = -ark_z, site_z = ark_y
"""
from __future__ import annotations

import numpy as np

ARKIT_TO_SITE = np.array([[1, 0, 0, 0], [0, 0, -1, 0], [0, 1, 0, 0], [0, 0, 0, 1]], dtype=float)
# OpenCV camera (x right, y down, z forward) <-> ARKit camera (x right, y up, z backward)
CV_TO_ARKIT_CAM = np.diag([1.0, -1.0, -1.0, 1.0])


def K_scaled(K, sx: float, sy: float):
    fx, fy, cx, cy = K
    # scale about the pixel-grid corner: centers move as (c + 0.5) * s - 0.5
    return (fx * sx, fy * sy, (cx + 0.5) * sx - 0.5, (cy + 0.5) * sy - 0.5)


def pixel_rays_cam(K, w: int, h: int, step: int = 1):
    """Unit-depth ray directions in camera frame for a pixel grid. Returns (dirs[N,3], u[N], v[N])
    where dirs has z = -1 (so point = dirs * depth)."""
    fx, fy, cx, cy = K
    vv, uu = np.mgrid[0:h:step, 0:w:step]
    u = uu.ravel().astype(np.float64)
    v = vv.ravel().astype(np.float64)
    dirs = np.stack([(u - cx) / fx, -(v - cy) / fy, -np.ones_like(u)], axis=1)
    return dirs, uu.ravel(), vv.ravel()


def backproject(depth: np.ndarray, K, T_world_cam: np.ndarray, mask: np.ndarray | None = None, step: int = 1):
    """depth HxW (m, 0 = invalid) -> world points [N,3] and camera centers broadcastable."""
    h, w = depth.shape
    dirs, uu, vv = pixel_rays_cam(K, w, h, step)
    d = depth[vv, uu].astype(np.float64)
    ok = d > 0
    if mask is not None:
        ok &= mask[vv, uu]
    pc = dirs[ok] * d[ok, None]
    R, t = T_world_cam[:3, :3], T_world_cam[:3, 3]
    return pc @ R.T + t


def project(points_world: np.ndarray, K, T_world_cam: np.ndarray, w: int, h: int):
    """world points [N,3] -> (u, v, depth, in_image mask)."""
    T_cw = np.linalg.inv(T_world_cam)
    pc = points_world @ T_cw[:3, :3].T + T_cw[:3, 3]
    d = -pc[:, 2]
    fx, fy, cx, cy = K
    with np.errstate(divide="ignore", invalid="ignore"):
        u = cx + fx * pc[:, 0] / d
        v = cy - fy * pc[:, 1] / d
    ok = (d > 1e-3) & (u >= -0.5) & (u < w - 0.5) & (v >= -0.5) & (v < h - 0.5)
    return u, v, d, ok


def look_rotation(yaw: float, pitch: float, roll: float = 0.0) -> np.ndarray:
    """Rotation (camera->world, Z-up world) for a camera whose forward (-Z_cam) points at heading
    `yaw` (rad, from +X toward +Y) tilted by `pitch` (rad, negative = looking down)."""
    fwd = np.array([np.cos(pitch) * np.cos(yaw), np.cos(pitch) * np.sin(yaw), np.sin(pitch)])
    right = np.array([np.sin(yaw), -np.cos(yaw), 0.0])
    up = np.cross(right, fwd)
    if roll:
        c, s = np.cos(roll), np.sin(roll)
        right, up = c * right + s * up, -s * right + c * up
    return np.stack([right, up, -fwd], axis=1)  # columns = camera x, y, z axes in world


def rot_z(a: float) -> np.ndarray:
    c, s = np.cos(a), np.sin(a)
    return np.array([[c, -s, 0], [s, c, 0], [0, 0, 1.0]])


def rot_axis(axis, angle: float) -> np.ndarray:
    axis = np.asarray(axis, float)
    n = np.linalg.norm(axis)
    if n < 1e-12 or abs(angle) < 1e-15:
        return np.eye(3)
    k = axis / n
    Kx = np.array([[0, -k[2], k[1]], [k[2], 0, -k[0]], [-k[1], k[0], 0]])
    return np.eye(3) + np.sin(angle) * Kx + (1 - np.cos(angle)) * Kx @ Kx


def make_T(R: np.ndarray, t) -> np.ndarray:
    T = np.eye(4)
    T[:3, :3] = R
    T[:3, 3] = t
    return T


def quat_xyzw_to_R(q) -> np.ndarray:
    x, y, z, w = q / np.linalg.norm(q)
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - z * w), 2 * (x * z + y * w)],
        [2 * (x * y + z * w), 1 - 2 * (x * x + z * z), 2 * (y * z - x * w)],
        [2 * (x * z - y * w), 2 * (y * z + x * w), 1 - 2 * (x * x + y * y)],
    ])
