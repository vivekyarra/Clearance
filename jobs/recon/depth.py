"""J2 depth sources.

- LidarDepth: ARKit sceneDepth PNGs (uint16 mm), keeping pixels with confidence >= medium.
- MonoAlignedDepth: a relative-depth backend (output affine in inverse depth) aligned per frame to
  ARKit's sparse metric feature points with a RANSAC scale/shift fit; frames whose inlier ratio is
  below 0.5 are dropped. The alignment is backend-agnostic: any object with
  predict(image_bgr, frame_name) -> HxW float array works.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np

from jobs.common.camera import K_scaled, project
from jobs.common.imageio import MAX_DEPTH_WH, FrameFiles, imread

from .ingest import CaptureData

MODEL_CACHE = Path(os.environ.get("CLEARANCE_MODEL_CACHE", Path.home() / ".cache" / "clearance" / "models"))


@dataclass
class DepthFrame:
    index: int
    frame: str
    depth: np.ndarray  # HxW float32 metres, 0 = invalid
    K: tuple           # intrinsics at depth resolution
    T_world_cam: np.ndarray
    info: dict


# ---------------------------------------------------------------------------------------------
def drop_depth_edges(depth: np.ndarray, rel_jump: float = 0.04) -> np.ndarray:
    """Zero pixels at depth discontinuities (mixed / flying pixels): max relative difference to the
    3x3 neighbourhood above rel_jump, or a missing neighbour."""
    d = depth.astype(np.float32)
    big = np.where(d > 0, d, 1e4).astype(np.float32)
    k = np.ones((3, 3), np.uint8)
    dmax = cv2.dilate(np.where(d > 0, d, 0).astype(np.float32), k)
    dmin = cv2.erode(big, k)
    bad = (d > 0) & (((dmax - d) > rel_jump * d) | ((d - dmin) > rel_jump * d) | (dmin > 1e3))
    out = depth.copy()
    out[bad] = 0
    return out


MIN_VALID_FRAC = 0.01   # frames with less valid depth than this carry no usable geometry
MAX_ASPECT_DIFF = 0.03  # depth map vs pose image aspect ratio (rotated / portrait mismatch)


def _aspect_matches(w: int, h: int, pw: int, ph: int) -> bool:
    return abs((w / h) / (pw / ph) - 1.0) <= MAX_ASPECT_DIFF


class LidarDepth:
    """Depth PNGs must be single-channel uint16 (mm) with the aspect ratio of the pose image; other
    frames are dropped and counted in .stats."""

    def __init__(self, cap: CaptureData, min_confidence: int = 1, max_depth_m: float = 5.0):
        self.cap, self.min_conf, self.max_depth = cap, min_confidence, max_depth_m
        self.ddir = cap.dir_of("depth")
        self.cdir = cap.dir_of("confidence")
        self.dfiles = FrameFiles(self.ddir)
        self.cfiles = FrameFiles(self.cdir) if self.cdir is not None else None
        self.stats = {"frames": 0, "dropped_missing": 0, "dropped_format": 0, "dropped_aspect": 0,
                      "dropped_empty": 0, "confidence_missing": 0, "confidence_size_mismatch": 0}

    def get(self, i: int) -> DepthFrame | None:
        p = self.cap.poses[i]
        self.stats["frames"] += 1
        f = self.dfiles.find(p.frame, (".png",))
        d = imread(f, max_wh=MAX_DEPTH_WH) if f is not None else None
        if d is None:
            self.stats["dropped_missing"] += 1
            return None
        if d.ndim != 2 or d.dtype != np.uint16:
            self.stats["dropped_format"] += 1  # e.g. an 8-bit or colour PNG: not millimetres
            return None
        h, w = d.shape
        if not _aspect_matches(w, h, p.w, p.h):
            self.stats["dropped_aspect"] += 1
            return None
        depth = d.astype(np.float32) / 1000.0
        if self.cfiles is not None:
            cf = self.cfiles.find(p.frame, (".png",))
            c = imread(cf, max_wh=MAX_DEPTH_WH) if cf is not None else None
            if c is None:
                self.stats["confidence_missing"] += 1
            else:
                if c.ndim == 3:
                    c = c[:, :, 0]
                if c.shape != depth.shape:
                    self.stats["confidence_size_mismatch"] += 1
                    c = cv2.resize(c, (w, h), interpolation=cv2.INTER_NEAREST)
                depth[c < self.min_conf] = 0
        depth[~np.isfinite(depth) | (depth > self.max_depth)] = 0
        depth = drop_depth_edges(depth)
        if (depth > 0).mean() < MIN_VALID_FRAC:
            self.stats["dropped_empty"] += 1
            return None
        K = K_scaled(p.K, w / p.w, h / p.h)
        return DepthFrame(i, p.frame, depth, K, self.cap.T(i), {"source": "lidar"})


# ---------------------------------------------------------------------------------------------
class RelativeDepthBackend(Protocol):
    name: str

    def predict(self, image_bgr: np.ndarray | None, frame_name: str) -> np.ndarray | None:
        """Relative depth, larger = closer, affine in inverse depth."""


class PrecomputedRelativeDepth:
    """Relative depth maps already on disk (uint16 / float PNG or .npy per frame)."""

    name = "precomputed"

    def __init__(self, directory: str | Path):
        self.dir = Path(directory)
        self.files = FrameFiles(self.dir)

    def predict(self, image_bgr, frame_name):
        npy = self.files.find(frame_name, (".npy",))
        if npy is not None:
            try:
                arr = np.load(npy, mmap_mode="r", allow_pickle=False)
            except (OSError, ValueError):
                return None
            if arr.ndim != 2 or arr.shape[0] > MAX_DEPTH_WH[1] * 4 or arr.shape[1] > MAX_DEPTH_WH[0] * 4:
                return None
            return np.asarray(arr, dtype=np.float64)
        png = self.files.find(frame_name, (".png",))
        img = imread(png, max_wh=(MAX_DEPTH_WH[0] * 4, MAX_DEPTH_WH[1] * 4)) if png is not None else None
        if img is None or img.ndim != 2:
            return None
        return img.astype(np.float64)


class DepthAnythingSmall:
    """Depth Anything V2 Small (Apache-2.0; VERIFY license file before shipping weights) via
    Hugging Face transformers. Loaded lazily on first predict; weights cached on C:.
    Output: the model's relative inverse depth ("disparity-like"; VERIFY on the model card)."""

    name = "depth_anything_v2_small"
    MODEL_ID = "depth-anything/Depth-Anything-V2-Small-hf"

    def __init__(self, model_id: str | None = None, device: str = "cpu"):
        self.model_id = model_id or self.MODEL_ID
        self.device = device
        self._proc = self._model = None

    def _load(self):
        try:
            import torch  # noqa: F401
            from transformers import AutoImageProcessor, AutoModelForDepthEstimation
        except ImportError as e:  # optional dependency
            raise RuntimeError("DepthAnythingSmall needs `torch` and `transformers` "
                               "(pip install torch transformers); or provide a reldepth/ directory") from e
        MODEL_CACHE.mkdir(parents=True, exist_ok=True)
        self._proc = AutoImageProcessor.from_pretrained(self.model_id, cache_dir=str(MODEL_CACHE))
        self._model = AutoModelForDepthEstimation.from_pretrained(self.model_id, cache_dir=str(MODEL_CACHE))
        self._model.to(self.device).eval()

    def predict(self, image_bgr, frame_name):
        if image_bgr is None:
            return None
        if self._model is None:
            self._load()
        import torch
        rgb = cv2.cvtColor(image_bgr, cv2.COLOR_BGR2RGB)
        inputs = self._proc(images=rgb, return_tensors="pt").to(self.device)
        with torch.no_grad():
            pred = self._model(**inputs).predicted_depth
        pred = torch.nn.functional.interpolate(pred[:, None], size=rgb.shape[:2], mode="bicubic",
                                               align_corners=False)[0, 0]
        return pred.cpu().numpy().astype(np.float64)


# ---------------------------------------------------------------------------------------------
def fit_scale_shift_ransac(rel: np.ndarray, depth: np.ndarray, *, rel_thresh: float = 0.06, iters: int = 300,
                           rng: np.random.Generator | None = None, min_inliers: int = 8):
    """Fit 1/depth = s * rel + t robustly. Residual = relative depth error |d_pred - d| / d.
    Returns dict(s, t, inlier_ratio, inliers mask, n) or None when no valid model exists."""
    rel = np.asarray(rel, float)
    depth = np.asarray(depth, float)
    ok = np.isfinite(rel) & np.isfinite(depth) & (depth > 0)
    rel, depth = rel[ok], depth[ok]
    n = rel.size
    if n < max(min_inliers, 2):
        return None
    inv = 1.0 / depth
    rng = rng or np.random.default_rng(0)

    def residual(s, t):
        q = s * rel + t
        with np.errstate(divide="ignore", invalid="ignore"):
            r = np.abs(1.0 / q - depth) / depth
        r[~(q > 0)] = np.inf
        return r

    best, best_cnt = None, -1
    pairs = rng.integers(0, n, size=(iters, 2))
    for i, j in pairs:
        if abs(rel[i] - rel[j]) < 1e-9:
            continue
        s = (inv[i] - inv[j]) / (rel[i] - rel[j])
        if s <= 0:
            continue
        t = inv[i] - s * rel[i]
        cnt = int((residual(s, t) < rel_thresh).sum())
        if cnt > best_cnt:
            best, best_cnt = (s, t), cnt
    if best is None:
        return None
    s, t = best
    for _ in range(3):  # refine: weighted LS in inverse depth (weight ~ depth^2 -> depth-space error)
        m = residual(s, t) < rel_thresh
        if m.sum() < 2:
            break
        wgt = depth[m] ** 2
        A = np.stack([rel[m], np.ones(m.sum())], axis=1) * np.sqrt(wgt)[:, None]
        sol, *_ = np.linalg.lstsq(A, inv[m] * np.sqrt(wgt), rcond=None)
        if sol[0] <= 0:
            break
        s, t = sol
    m = residual(s, t) < rel_thresh
    full = np.zeros(ok.shape, bool)
    full[np.flatnonzero(ok)[m]] = True
    if m.sum() < min_inliers:
        return {"s": float(s), "t": float(t), "inlier_ratio": float(m.mean()), "inliers": full, "n": n,
                "ok": False}
    return {"s": float(s), "t": float(t), "inlier_ratio": float(m.mean()), "inliers": full, "n": n, "ok": True}


def _bilinear(img: np.ndarray, u: np.ndarray, v: np.ndarray):
    h, w = img.shape
    u = np.clip(u, 0, w - 1.001)
    v = np.clip(v, 0, h - 1.001)
    u0, v0 = np.floor(u).astype(int), np.floor(v).astype(int)
    du, dv = u - u0, v - v0
    return ((1 - dv) * ((1 - du) * img[v0, u0] + du * img[v0, u0 + 1])
            + dv * ((1 - du) * img[v0 + 1, u0] + du * img[v0 + 1, u0 + 1]))


def _poly_basis(u, v, w, h, degree):
    x = 2 * np.asarray(u, float) / max(w - 1, 1) - 1
    y = 2 * np.asarray(v, float) / max(h - 1, 1) - 1
    cols = [x ** i * y ** j for i in range(degree + 1) for j in range(degree + 1 - i) if i + j > 0]
    return np.stack(cols, axis=-1)


def fit_local_scale(u, v, pred_inv, depth, w, h, *, degree: int = 2, ridge: float = 0.05, rel_thresh: float = 0.06,
                    max_corr: float = 0.15):
    """Smooth multiplicative correction c(u, v) with 1/depth ~= pred_inv * (1 + c(u, v)), c a polynomial
    of the given degree in normalized image coordinates (ridge-regularized, robust reweighting).
    Returns coefficient vector or None."""
    if degree <= 0 or len(u) < 6 * (degree + 1):
        return None
    y = (1.0 / depth) / pred_inv - 1.0
    B = _poly_basis(u, v, w, h, degree)
    m = np.abs(y) < rel_thresh * 2
    coef = None
    for _ in range(3):
        if m.sum() < B.shape[1] * 3:
            return coef
        A = B[m]
        coef = np.linalg.solve(A.T @ A + ridge * len(A) * 0.01 * np.eye(A.shape[1]), A.T @ y[m])
        m = np.abs(B @ coef - y) < rel_thresh
    return coef


class MonoAlignedDepth:
    """Relative depth aligned to ARKit feature points. Fails safe: frames whose fit is poor are dropped,
    and pixels whose relative depth lies outside the range covered by the inlier feature points (plus
    SUPPORT_MARGIN of that range) are left without depth instead of being extrapolated."""

    SUPPORT_MARGIN = 0.25
    MIN_INLIERS = 15  # with fewer, chance agreement of random points is likely (8 of 15 happens)

    def __init__(self, cap: CaptureData, backend: RelativeDepthBackend, *, min_inlier_ratio: float = 0.5,
                 rel_thresh: float = 0.06, max_depth_m: float = 4.0, out_size: tuple[int, int] = (256, 192),
                 local_degree: int = 0):
        self.local_degree = local_degree
        self.cap, self.backend = cap, backend
        self.min_ratio, self.rel_thresh, self.max_depth = min_inlier_ratio, rel_thresh, max_depth_m
        self.out_size = out_size
        self.fdir = cap.dir_of("frames")
        self.ffiles = FrameFiles(self.fdir) if self.fdir is not None else None
        self.rng = np.random.default_rng(0)
        self.stats = {"frames": 0, "dropped_fit": 0, "dropped_no_points": 0, "dropped_unreadable": 0,
                      "dropped_empty": 0, "unsupported_px_frac": 0.0}
        self._unsupported = []

    def get(self, i: int) -> DepthFrame | None:
        p = self.cap.poses[i]
        self.stats["frames"] += 1
        fp = self.cap.feature_points.get(p.frame)
        if fp is None or len(fp) < 10:
            self.stats["dropped_no_points"] += 1
            return None
        img = None
        if self.ffiles is not None and not isinstance(self.backend, PrecomputedRelativeDepth):
            f = self.ffiles.find(p.frame, (Path(p.frame).suffix or ".jpg", ".jpg", ".jpeg", ".png"))
            img = imread(f, cv2.IMREAD_COLOR, expect_wh=(p.w, p.h)) if f is not None else None
        rel = self.backend.predict(img, p.frame)
        if rel is None:
            self.stats["dropped_unreadable"] += 1
            return None
        rel = np.asarray(rel, np.float64)
        w, h = self.out_size
        if rel.shape != (h, w):
            rel = cv2.resize(rel.astype(np.float32), (w, h), interpolation=cv2.INTER_LINEAR).astype(np.float64)
        K = K_scaled(p.K, w / p.w, h / p.h)
        T = self.cap.T(i)
        u, v, d, ok = project(fp, K, T, w, h)
        if ok.sum() < 10:
            self.stats["dropped_no_points"] += 1
            return None
        rs = _bilinear(rel, u[ok], v[ok])
        fit = fit_scale_shift_ransac(rs, d[ok], rel_thresh=self.rel_thresh, rng=self.rng,
                                     min_inliers=self.MIN_INLIERS)
        if fit is None or not fit["ok"] or fit["inlier_ratio"] < self.min_ratio:
            self.stats["dropped_fit"] += 1
            return None
        inl = fit["inliers"]
        r_lo, r_hi = np.percentile(rs[inl], [1, 99])
        margin = self.SUPPORT_MARGIN * (r_hi - r_lo)
        supported = np.isfinite(rel) & (rel >= r_lo - margin) & (rel <= r_hi + margin)
        q = fit["s"] * rel + fit["t"]
        if self.local_degree:
            uu, vv, dd = u[ok][inl], v[ok][inl], d[ok][inl]
            coef = fit_local_scale(uu, vv, fit["s"] * rs[inl] + fit["t"], dd, w, h, degree=self.local_degree,
                                   rel_thresh=self.rel_thresh)
            if coef is not None:
                V, U = np.mgrid[0:h, 0:w]
                corr = np.clip(_poly_basis(U, V, w, h, self.local_degree) @ coef, -0.15, 0.15)
                q = q * (1 + corr)
        with np.errstate(divide="ignore", invalid="ignore"):
            depth = np.where(supported & (q > 1.0 / 50), 1.0 / q, 0.0)
        depth[~np.isfinite(depth) | (depth > self.max_depth)] = 0
        depth = drop_depth_edges(depth.astype(np.float32), 0.06)
        self._unsupported.append(float(1.0 - supported.mean()))
        self.stats["unsupported_px_frac"] = round(float(np.mean(self._unsupported)), 4)
        if (depth > 0).mean() < MIN_VALID_FRAC:
            self.stats["dropped_empty"] += 1
            return None
        return DepthFrame(i, p.frame, depth, K, T,
                          {"source": self.backend.name, "s": fit["s"], "t": fit["t"],
                           "inlier_ratio": fit["inlier_ratio"], "n_points": fit["n"]})


def depth_source_for(cap: CaptureData, tier: str | None = None, backend: RelativeDepthBackend | None = None,
                     max_depth_m: float | None = None, local_degree: int = 0):
    tier = tier or cap.tier
    if tier == "T1_LIDAR":
        if cap.dir_of("depth") is None:
            raise ValueError("T1_LIDAR reconstruction needs files.depth")
        return LidarDepth(cap, max_depth_m=max_depth_m or 5.0)
    if tier == "T2_VIO_MONO":
        if backend is None:
            rd = cap.dir_of("relative_depth")
            if rd is None and (cap.root / "reldepth").is_dir():
                rd = cap.root / "reldepth"
            backend = PrecomputedRelativeDepth(rd) if rd is not None else DepthAnythingSmall()
        return MonoAlignedDepth(cap, backend, max_depth_m=max_depth_m or 4.0, local_degree=local_degree)
    raise ValueError(f"tier {tier!r} has no scanner depth source")
