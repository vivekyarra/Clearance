"""Reference scenes for J4 tests: analytic ray casting of upright boxes/cylinders on a floor, posed
ARKit cameras, and capture directories written in the Capture layout."""
from __future__ import annotations

import math
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np

from clearance_core.schemas import Capture, CaptureFiles, Device, PoseRecord
from jobs.semantics.camera import PinholeCamera, look_at_arkit


@dataclass
class RefBox:
    label: str
    cx: float
    cy: float
    yaw: float
    l: float
    w: float
    h: float


@dataclass
class RefCyl:
    label: str
    cx: float
    cy: float
    d: float
    h: float


def ray_hits(origins: np.ndarray, dirs: np.ndarray, objects) -> tuple[np.ndarray, np.ndarray]:
    """Nearest hit distance along each ray and the object index (-1 = floor, -2 = nothing)."""
    n = len(dirs)
    best_t = np.full(n, np.inf)
    best_id = np.full(n, -2, dtype=np.int64)
    with np.errstate(divide="ignore", invalid="ignore"):
        tf = -origins[:, 2] / dirs[:, 2]
    floor = (tf > 0) & np.isfinite(tf)
    best_t[floor] = tf[floor]
    best_id[floor] = -1
    for k, ob in enumerate(objects):
        if isinstance(ob, RefBox):
            c, s = math.cos(ob.yaw), math.sin(ob.yaw)
            R = np.array([[c, s, 0], [-s, c, 0], [0, 0, 1]])  # world -> box frame
            o = (origins - np.array([ob.cx, ob.cy, ob.h / 2])) @ R.T
            d = dirs @ R.T
            half = np.array([ob.l / 2, ob.w / 2, ob.h / 2])
            with np.errstate(divide="ignore", invalid="ignore"):
                t1 = (-half - o) / d
                t2 = (half - o) / d
            tmin = np.nanmax(np.minimum(t1, t2), axis=1)
            tmax = np.nanmin(np.maximum(t1, t2), axis=1)
            hit = (tmax >= np.maximum(tmin, 0)) & (tmin > 0)
            t = tmin
        else:
            o = origins - np.array([ob.cx, ob.cy, 0.0])
            r = ob.d / 2
            a = dirs[:, 0] ** 2 + dirs[:, 1] ** 2
            b = 2 * (o[:, 0] * dirs[:, 0] + o[:, 1] * dirs[:, 1])
            cc = o[:, 0] ** 2 + o[:, 1] ** 2 - r * r
            disc = b * b - 4 * a * cc
            with np.errstate(invalid="ignore", divide="ignore"):
                ts = (-b - np.sqrt(disc)) / (2 * a)
            z = o[:, 2] + ts * dirs[:, 2]
            side = (disc >= 0) & (ts > 0) & (z >= 0) & (z <= ob.h)
            with np.errstate(invalid="ignore", divide="ignore"):
                ttop = (ob.h - o[:, 2]) / dirs[:, 2]
            ptop = o[:, :2] + ttop[:, None] * dirs[:, :2]
            top = (ttop > 0) & ((ptop ** 2).sum(axis=1) <= r * r)
            t = np.where(side, ts, np.inf)
            t = np.where(top & (ttop < t), ttop, t)
            hit = np.isfinite(t)
        closer = hit & (t < best_t)
        best_t[closer] = t[closer]
        best_id[closer] = k
    return best_t, best_id


def render(cam: PinholeCamera, objects, max_depth: float = 8.0):
    """(z-depth image in meters (0 = no return), object-id image) for an OpenCV camera."""
    vs, us = np.mgrid[0:cam.h, 0:cam.w]
    uv = np.stack([us.ravel() + 0.5, vs.ravel() + 0.5], axis=1)
    dirs = cam.rays(uv)
    origins = np.repeat(cam.center[None], len(dirs), axis=0)
    t, ids = ray_hits(origins, dirs, objects)
    z = t * (dirs @ cam.forward)
    z[~np.isfinite(z) | (z > max_depth)] = 0.0
    ids[z == 0] = -2
    return z.reshape(cam.h, cam.w).astype(np.float32), ids.reshape(cam.h, cam.w)


def orbit_poses(center, radius: float, n: int, height: float = 1.3, target_z: float = 0.4,
                K=(160.0, 160.0, 80.0, 60.0), w: int = 160, h: int = 120, t0: float = 0.0, arc=(0, 2 * math.pi)):
    poses = []
    for i in range(n):
        a = arc[0] + (arc[1] - arc[0]) * i / max(n - 1, 1) if arc != (0, 2 * math.pi) else 2 * math.pi * i / n
        eye = (center[0] + radius * math.cos(a), center[1] + radius * math.sin(a), height)
        T = look_at_arkit(eye, (center[0], center[1], target_z))
        poses.append(PoseRecord(t=t0 + i * 0.5, frame=f"{i:06d}.jpg", T_world_cam=T.ravel().tolist(), K=list(K), w=w, h=h))
    return poses


def write_capture(out: Path, poses, objects, *, with_depth: bool = True, site_id: str = "site_ref") -> Path:
    out.mkdir(parents=True, exist_ok=True)
    (out / "frames").mkdir(exist_ok=True)
    if with_depth:
        (out / "depth").mkdir(exist_ok=True)
    shades = [90, 150, 200, 60, 120, 180]
    for p in poses:
        cam = PinholeCamera.from_pose_record(p)
        z, ids = render(cam, objects)
        img = np.full((cam.h, cam.w), 230, np.uint8)
        img[ids == -1] = 170
        for k in range(len(objects)):
            img[ids == k] = shades[k % len(shades)]
        cv2.imwrite(str(out / "frames" / p.frame), cv2.cvtColor(img, cv2.COLOR_GRAY2BGR))
        if with_depth:
            cv2.imwrite(str(out / "depth" / (Path(p.frame).stem + ".png")), (z * 1000).astype(np.uint16))
    (out / "poses.jsonl").write_text("\n".join(p.model_dump_json() for p in poses) + "\n", encoding="utf-8")
    cap = Capture(capture_id=out.name, site_id=site_id, device=Device(model="reference-renderer", tier="T1_LIDAR"),
                  role="scanner", files=CaptureFiles(frames="frames", depth="depth" if with_depth else None))
    (out / "capture.json").write_text(cap.model_dump_json(indent=1), encoding="utf-8")
    return out


def object_points(ob, density: int = 4000, rng=None) -> np.ndarray:
    """Points on the visible-from-anywhere surfaces (top + sides) of a reference object."""
    rng = rng or np.random.default_rng(0)
    if isinstance(ob, RefBox):
        u = rng.uniform(-0.5, 0.5, (density, 3)) * np.array([ob.l, ob.w, ob.h])
        face = rng.integers(0, 3, density)
        u[face == 0, 2] = ob.h / 2                                    # top
        u[face == 1, 0] = np.sign(u[face == 1, 0] + 1e-9) * ob.l / 2  # end faces
        u[face == 2, 1] = np.sign(u[face == 2, 1] + 1e-9) * ob.w / 2  # long faces
        c, s = math.cos(ob.yaw), math.sin(ob.yaw)
        xy = u[:, :2] @ np.array([[c, s], [-s, c]])
        return np.c_[xy + [ob.cx, ob.cy], u[:, 2] + ob.h / 2]
    a = rng.uniform(0, 2 * math.pi, density)
    r = np.where(rng.uniform(size=density) < 0.3, np.sqrt(rng.uniform(size=density)) * ob.d / 2, ob.d / 2)
    z = np.where(r < ob.d / 2, ob.h, rng.uniform(0, ob.h, density))
    return np.c_[ob.cx + r * np.cos(a), ob.cy + r * np.sin(a), z]


def rasterize_scene(objects, bounds=(-3.0, 3.0, -3.0, 3.0), res: float = 0.025, wall_m: float = 0.05):
    """Occupancy grid (clearance_core.grid.Grid) of the reference objects inside walls."""
    from clearance_core.grid import FREE, OCC, Grid

    x0, x1, y0, y1 = bounds
    W, H = int(round((x1 - x0) / res)), int(round((y1 - y0) / res))
    g = Grid(np.full((H, W), FREE, np.uint8), res, (x0, y0))
    t = max(1, int(round(wall_m / res)))
    g.data[:t, :] = OCC
    g.data[-t:, :] = OCC
    g.data[:, :t] = OCC
    g.data[:, -t:] = OCC
    for ob in objects:
        if isinstance(ob, RefBox):
            c, s = math.cos(ob.yaw), math.sin(ob.yaw)
            corners = np.array([[ob.l / 2, ob.w / 2], [-ob.l / 2, ob.w / 2], [-ob.l / 2, -ob.w / 2], [ob.l / 2, -ob.w / 2]])
            xy = corners @ np.array([[c, s], [-s, c]]) + [ob.cx, ob.cy]
            r, cc = g.world_to_cell(xy[:, 0], xy[:, 1])
            cv2.fillPoly(g.data, [np.stack([cc, r], axis=1).astype(np.int32)], OCC)
        else:
            r, cc = g.world_to_cell(ob.cx, ob.cy)
            cv2.circle(g.data, (int(cc), int(r)), int(round(ob.d / 2 / res)), OCC, -1)
    return g
