"""Lift 2D masks to 3D (depth + pose, or the recon point cloud), vote across views, cluster per label
."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Optional, Sequence

import cv2
import numpy as np

from jobs.semantics.camera import PinholeCamera
from jobs.semantics.cluster import dbscan
from jobs.semantics.detectors.base import Detection2D, Object3D
from jobs.semantics.primitives import normalize_label
from clearance_core.schemas import PoseRecord


@dataclass
class LiftInput:
    pose: PoseRecord
    image_hw: tuple[int, int]
    detections: list[Detection2D]
    depth: Optional[np.ndarray] = None  # meters, any resolution (same aspect as the image)


def detection_mask(det: Detection2D, hw: tuple[int, int], erode_frac: float = 0.04) -> np.ndarray:
    h, w = hw
    if det.mask is not None:
        m = det.mask.astype(np.uint8)
        if m.shape != (h, w):
            m = cv2.resize(m, (w, h), interpolation=cv2.INTER_NEAREST)
    else:
        m = np.zeros((h, w), dtype=np.uint8)
        x0, y0, x1, y1 = [int(round(v)) for v in det.box]
        m[max(0, y0):min(h, y1), max(0, x0):min(w, x1)] = 1
    ys, xs = np.nonzero(m)
    if len(xs) == 0:
        return m.astype(bool)
    size = min(xs.max() - xs.min() + 1, ys.max() - ys.min() + 1)
    r = int(round(size * erode_frac))
    if r >= 1:
        eroded = cv2.erode(m, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (2 * r + 1, 2 * r + 1)))
        if eroded.any():
            m = eroded
    return m.astype(bool)


def zbuffer_visible(cloud: np.ndarray, cam: PinholeCamera, downscale: int = 4, rel_tol: float = 0.03,
                    far: float = 8.0):
    """Indices of cloud points visible in `cam` (coarse z-buffer) and their pixel coords."""
    uv, z = cam.project(cloud)
    ok = cam.in_image(uv, z, near=0.1, far=far)
    idx = np.flatnonzero(ok)
    if len(idx) == 0:
        return idx, np.zeros((0, 2))
    W = int(np.ceil(cam.w / downscale))
    pix = (uv[idx, 1] // downscale).astype(np.int64) * W + (uv[idx, 0] // downscale).astype(np.int64)
    zb = np.full(int(np.ceil(cam.h / downscale)) * W, np.inf)
    np.minimum.at(zb, pix, z[idx])
    vis = z[idx] <= zb[pix] * (1 + rel_tol) + 0.02
    return idx[vis], uv[idx[vis]]


def _robust_depth_filter(z: np.ndarray) -> np.ndarray:
    if len(z) < 10:
        return np.ones(len(z), dtype=bool)
    med = np.median(z)
    q1, q3 = np.percentile(z, [25, 75])
    tol = max(0.5, 2.0 * (q3 - q1))
    return np.abs(z - med) <= tol


def lift_detection(det: Detection2D, cam_img: PinholeCamera, depth: Optional[np.ndarray],
                   cloud: Optional[np.ndarray] = None, vis_cache: Optional[tuple] = None,
                   max_points: int = 4000) -> np.ndarray:
    mask = detection_mask(det, (cam_img.h, cam_img.w))
    if depth is not None:
        dh, dw = depth.shape
        cam_d = cam_img.scaled(dw, dh)
        m = cv2.resize(mask.astype(np.uint8), (dw, dh), interpolation=cv2.INTER_NEAREST).astype(bool)
        m &= depth > 0.05
        vs, us = np.nonzero(m)
        if len(us) == 0:
            return np.zeros((0, 3))
        if len(us) > max_points:
            sel = np.linspace(0, len(us) - 1, max_points).astype(int)
            vs, us = vs[sel], us[sel]
        z = depth[vs, us].astype(np.float64)
        keep = _robust_depth_filter(z)
        uv = np.stack([us[keep] + 0.5, vs[keep] + 0.5], axis=1)
        return cam_d.backproject(uv, z[keep])
    if cloud is None:
        return np.zeros((0, 3))
    idx, uv = vis_cache if vis_cache is not None else zbuffer_visible(cloud, cam_img)
    if len(idx) == 0:
        return np.zeros((0, 3))
    ui = np.clip(uv[:, 0].astype(int), 0, cam_img.w - 1)
    vi = np.clip(uv[:, 1].astype(int), 0, cam_img.h - 1)
    pts = cloud[idx[mask[vi, ui]]]
    if len(pts) == 0:
        return pts
    z = cam_img.world_to_cam(pts)[:, 2]
    return pts[_robust_depth_filter(z)]


def _visible_counts(centers: np.ndarray, cams: Sequence[PinholeCamera], depths: Sequence[Optional[np.ndarray]],
                    occl_tol: float = 0.10) -> np.ndarray:
    """How many frames could have seen each voxel center (in frustum and not occluded)."""
    count = np.zeros(len(centers), dtype=np.int64)
    for cam, depth in zip(cams, depths):
        uv, z = cam.project(centers)
        ok = cam.in_image(uv, z, near=0.1, far=8.0)
        if depth is not None and ok.any():
            dh, dw = depth.shape
            ui = np.clip((uv[ok, 0] * dw / cam.w).astype(int), 0, dw - 1)
            vi = np.clip((uv[ok, 1] * dh / cam.h).astype(int), 0, dh - 1)
            d = depth[vi, ui]
            sub = (d > 0.05) & (d >= z[ok] - occl_tol)
            ok_idx = np.flatnonzero(ok)
            ok[ok_idx[~sub]] = False
        count += ok
    return count


def lift_and_cluster(inputs: Sequence[LiftInput], *, cloud: Optional[np.ndarray] = None, voxel: float = 0.04,
                     min_view_ratio: float = 0.25, eps: float = 0.10, min_cluster_voxels: int = 4,
                     height_band: tuple[float, float] = (0.03, 2.0), max_z: float = 3.0,
                     backend: str = "") -> list[Object3D]:
    cams, depths = [], []
    # per label: voxel key -> {frame_idx: best score}
    votes: dict[str, dict[tuple, dict[int, float]]] = defaultdict(lambda: defaultdict(dict))
    sums: dict[str, dict[tuple, np.ndarray]] = defaultdict(dict)
    for fi, inp in enumerate(inputs):
        h, w = inp.image_hw
        cam = PinholeCamera.from_pose_record(inp.pose).scaled(w, h)
        cams.append(cam)
        depths.append(inp.depth)
        vis_cache = zbuffer_visible(cloud, cam) if (inp.depth is None and cloud is not None and inp.detections) else None
        for det in inp.detections:
            label = normalize_label(det.label)
            pts = lift_detection(det, cam, inp.depth, cloud, vis_cache)
            if len(pts) == 0:
                continue
            pts = pts[(pts[:, 2] >= height_band[0]) & (pts[:, 2] <= max_z)]
            if len(pts) == 0:
                continue
            keys = np.floor(pts / voxel).astype(np.int64)
            uniq, inv = np.unique(keys, axis=0, return_inverse=True)
            acc = np.zeros((len(uniq), 4))
            np.add.at(acc[:, :3], inv.reshape(-1), pts)
            np.add.at(acc[:, 3], inv.reshape(-1), 1.0)
            for key, a in zip(map(tuple, uniq), acc):
                fv = votes[label][key]
                fv[fi] = max(fv.get(fi, 0.0), float(det.score))
                prev = sums[label].get(key)
                sums[label][key] = a if prev is None else prev + a

    # Cross-label vote: a voxel claimed by several labels goes to the label most views agree on.
    owner: dict[tuple, tuple[str, int, float]] = {}
    for label, vox in votes.items():
        for key, fv in vox.items():
            cand = (label, len(fv), sum(fv.values()))
            cur = owner.get(key)
            if cur is None or (cand[1], cand[2]) > (cur[1], cur[2]):
                owner[key] = cand
    for label in list(votes):
        for key in [k for k in votes[label] if owner[k][0] != label]:
            del votes[label][key]

    objects: list[Object3D] = []
    for label, vox in votes.items():
        if not vox:
            continue
        keys = list(vox.keys())
        centroids = np.array([sums[label][k][:3] / sums[label][k][3] for k in keys])
        n_votes = np.array([len(vox[k]) for k in keys])
        visible = _visible_counts(centroids, cams, depths)
        ratio = n_votes / np.maximum(np.maximum(visible, n_votes), 1)
        min_votes = 2 if len(inputs) >= 3 else 1  # single-view detections are not trusted
        keep = (n_votes >= min_votes) & (ratio >= min_view_ratio)
        if keep.sum() < min_cluster_voxels:
            continue
        kc, kv = centroids[keep], n_votes[keep]
        kkeys = [k for k, m in zip(keys, keep) if m]
        lab = dbscan(kc, eps=max(eps, 1.5 * voxel), min_samples=3)
        for c in range(lab.max() + 1):
            sel = np.flatnonzero(lab == c)
            if len(sel) < min_cluster_voxels:
                continue
            frame_scores: dict[int, float] = {}
            for i in sel:
                for f, s in vox[kkeys[i]].items():
                    frame_scores[f] = max(frame_scores.get(f, 0.0), s)
            score = float(np.mean(list(frame_scores.values())))
            objects.append(Object3D(
                label=label, points=kc[sel], score=score, n_views=len(frame_scores),
                frames=sorted(inputs[f].pose.frame for f in frame_scores), backend=backend,
                extra={"mean_votes": float(kv[sel].mean()), "n_voxels": int(len(sel))}))
    return objects
