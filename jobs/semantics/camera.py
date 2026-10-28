"""Camera math shared by J3/J4/J5.

Conventions
-----------
* Site/world frame: Z up, floor at Z=0 (clearance_core.schemas).
* ARKit / OpenGL camera frame: x right, y up, z backward (camera looks along -Z).
  `PoseRecord.T_world_cam` uses this convention.
* OpenCV / COLMAP camera frame: x right, y down, z forward. Pixel (u, v) = (fx*X/Z + cx, fy*Y/Z + cy).

The two camera frames differ by a rotation of 180 deg about x: diag(1, -1, -1).
"""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

ARKIT_TO_OPENCV = np.diag([1.0, -1.0, -1.0, 1.0])  # T_arkitcam_cvcam (its own inverse)


def as_matrix(T) -> np.ndarray:
    M = np.asarray(T, dtype=np.float64)
    if M.shape == (16,):
        M = M.reshape(4, 4)
    if M.shape != (4, 4):
        raise ValueError(f"expected 4x4 or 16 floats, got shape {M.shape}")
    return M


def arkit_to_opencv_c2w(T_world_cam_arkit) -> np.ndarray:
    """camera-to-world with an ARKit camera frame -> camera-to-world with an OpenCV camera frame."""
    return as_matrix(T_world_cam_arkit) @ ARKIT_TO_OPENCV


def opencv_to_arkit_c2w(T_world_cam_cv) -> np.ndarray:
    return as_matrix(T_world_cam_cv) @ ARKIT_TO_OPENCV


def invert_rigid(T: np.ndarray) -> np.ndarray:
    R, t = T[:3, :3], T[:3, 3]
    out = np.eye(4)
    out[:3, :3] = R.T
    out[:3, 3] = -R.T @ t
    return out


def K_matrix(K) -> np.ndarray:
    fx, fy, cx, cy = [float(v) for v in K]
    return np.array([[fx, 0.0, cx], [0.0, fy, cy], [0.0, 0.0, 1.0]])


@dataclass
class PinholeCamera:
    """OpenCV-convention pinhole camera with world pose."""
    T_world_cv: np.ndarray  # 4x4 camera-to-world, OpenCV camera frame
    K: np.ndarray           # 3x3
    w: int
    h: int

    @classmethod
    def from_arkit(cls, T_world_cam, K, w, h) -> "PinholeCamera":
        return cls(arkit_to_opencv_c2w(T_world_cam), K_matrix(K), int(w), int(h))

    @classmethod
    def from_pose_record(cls, pose) -> "PinholeCamera":
        return cls.from_arkit(pose.T_world_cam, pose.K, pose.w, pose.h)

    @property
    def T_cv_world(self) -> np.ndarray:
        return invert_rigid(self.T_world_cv)

    @property
    def center(self) -> np.ndarray:
        return self.T_world_cv[:3, 3]

    @property
    def forward(self) -> np.ndarray:
        """Viewing direction (+Z of the OpenCV frame) in world coordinates."""
        return self.T_world_cv[:3, 2]

    def scaled(self, w: int, h: int) -> "PinholeCamera":
        """Same camera with intrinsics rescaled to an image of size w x h."""
        sx, sy = w / self.w, h / self.h
        K = self.K.copy()
        K[0, :] *= sx
        K[1, :] *= sy
        return PinholeCamera(self.T_world_cv, K, int(w), int(h))

    def world_to_cam(self, pts: np.ndarray) -> np.ndarray:
        T = self.T_cv_world
        pts = np.asarray(pts, dtype=np.float64).reshape(-1, 3)
        return pts @ T[:3, :3].T + T[:3, 3]

    def project(self, pts: np.ndarray):
        """World points (N,3) -> (uv (N,2), depth (N,)). depth <= 0 means behind the camera."""
        pc = self.world_to_cam(pts)
        z = pc[:, 2]
        with np.errstate(divide="ignore", invalid="ignore"):
            u = self.K[0, 0] * pc[:, 0] / z + self.K[0, 2]
            v = self.K[1, 1] * pc[:, 1] / z + self.K[1, 2]
        return np.stack([u, v], axis=1), z

    def in_image(self, uv: np.ndarray, depth: np.ndarray, near: float = 0.05, far: float = np.inf) -> np.ndarray:
        return ((depth > near) & (depth < far) & (uv[:, 0] >= 0) & (uv[:, 0] < self.w)
                & (uv[:, 1] >= 0) & (uv[:, 1] < self.h))

    def rays(self, uv: np.ndarray) -> np.ndarray:
        """Pixel coords (N,2) -> unit ray directions in world (N,3)."""
        uv = np.asarray(uv, dtype=np.float64).reshape(-1, 2)
        x = (uv[:, 0] - self.K[0, 2]) / self.K[0, 0]
        y = (uv[:, 1] - self.K[1, 2]) / self.K[1, 1]
        d = np.stack([x, y, np.ones_like(x)], axis=1) @ self.T_world_cv[:3, :3].T
        return d / np.linalg.norm(d, axis=1, keepdims=True)

    def backproject(self, uv: np.ndarray, depth: np.ndarray) -> np.ndarray:
        """Pixels + metric z-depth (along the optical axis) -> world points."""
        uv = np.asarray(uv, dtype=np.float64).reshape(-1, 2)
        z = np.asarray(depth, dtype=np.float64).reshape(-1)
        x = (uv[:, 0] - self.K[0, 2]) / self.K[0, 0] * z
        y = (uv[:, 1] - self.K[1, 2]) / self.K[1, 1] * z
        pc = np.stack([x, y, z], axis=1)
        return pc @ self.T_world_cv[:3, :3].T + self.T_world_cv[:3, 3]


def rotmat_to_qvec(R: np.ndarray) -> np.ndarray:
    """Rotation matrix -> unit quaternion (w, x, y, z) with w >= 0 (COLMAP order)."""
    R = np.asarray(R, dtype=np.float64)
    tr = np.trace(R)
    if tr > 0:
        s = np.sqrt(tr + 1.0) * 2
        q = [0.25 * s, (R[2, 1] - R[1, 2]) / s, (R[0, 2] - R[2, 0]) / s, (R[1, 0] - R[0, 1]) / s]
    elif R[0, 0] > R[1, 1] and R[0, 0] > R[2, 2]:
        s = np.sqrt(1.0 + R[0, 0] - R[1, 1] - R[2, 2]) * 2
        q = [(R[2, 1] - R[1, 2]) / s, 0.25 * s, (R[0, 1] + R[1, 0]) / s, (R[0, 2] + R[2, 0]) / s]
    elif R[1, 1] > R[2, 2]:
        s = np.sqrt(1.0 + R[1, 1] - R[0, 0] - R[2, 2]) * 2
        q = [(R[0, 2] - R[2, 0]) / s, (R[0, 1] + R[1, 0]) / s, 0.25 * s, (R[1, 2] + R[2, 1]) / s]
    else:
        s = np.sqrt(1.0 + R[2, 2] - R[0, 0] - R[1, 1]) * 2
        q = [(R[1, 0] - R[0, 1]) / s, (R[0, 2] + R[2, 0]) / s, (R[1, 2] + R[2, 1]) / s, 0.25 * s]
    q = np.asarray(q)
    q /= np.linalg.norm(q)
    return q if q[0] >= 0 else -q


def qvec_to_rotmat(q) -> np.ndarray:
    w, x, y, z = np.asarray(q, dtype=np.float64) / np.linalg.norm(q)
    return np.array([
        [1 - 2 * (y * y + z * z), 2 * (x * y - w * z), 2 * (x * z + w * y)],
        [2 * (x * y + w * z), 1 - 2 * (x * x + z * z), 2 * (y * z - w * x)],
        [2 * (x * z - w * y), 2 * (y * z + w * x), 1 - 2 * (x * x + y * y)],
    ])


def look_at_arkit(eye, target, up=(0.0, 0.0, 1.0)) -> np.ndarray:
    """ARKit-convention camera-to-world looking from eye to target (helper for building reference scenes)."""
    eye, target, up = (np.asarray(v, dtype=np.float64) for v in (eye, target, up))
    back = eye - target
    back /= np.linalg.norm(back)
    right = np.cross(up, back)
    if np.linalg.norm(right) < 1e-9:  # looking straight up/down
        right = np.cross(np.array([0.0, 1.0, 0.0]), back)
    right /= np.linalg.norm(right)
    cam_up = np.cross(back, right)
    T = np.eye(4)
    T[:3, 0], T[:3, 1], T[:3, 2], T[:3, 3] = right, cam_up, back, eye
    return T
