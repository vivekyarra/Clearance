"""Primitive fitting: oriented box from the footprint + height percentile, or an
upright cylinder for round objects. Pure numpy/cv2 so it runs anywhere."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Iterable, Optional

import cv2
import numpy as np

from jobs.semantics.cluster import remove_statistical_outliers
from clearance_core.schemas import Pose2, Primitive

PROMPT_LABELS = ["table", "chair", "stool", "shelf", "counter", "door", "cart", "box", "trash bin",
                 "plant", "cable", "glass", "step"]
CYLINDER_LABELS = {"trash bin", "plant", "stool", "pillar"}
MOVABLE_LABELS = {"chair", "stool", "cart", "table", "box", "trash bin", "plant"}
FIXED_LABELS = {"shelf", "counter", "pillar", "glass", "object"}
FRAGILE_LABELS = {"glass"}
# Detected but not obstacles we fit: they become hazards (thresholds/doors are recon's/editor's job).
HAZARD_ONLY_LABELS = {"cable": "cable on floor", "door": "door swings into aisle", "step": "step / level change"}
ID_PREFIX = {"table": "T", "chair": "C", "stool": "S", "shelf": "SH", "counter": "CO", "cart": "CA",
             "box": "B", "trash bin": "TB", "plant": "P", "glass": "G", "pillar": "PI"}

LABEL_ALIASES = {"bin": "trash bin", "trash can": "trash bin", "garbage bin": "trash bin", "waste bin": "trash bin",
                 "trashcan": "trash bin", "desk": "table", "bench": "table", "shelving": "shelf", "rack": "shelf",
                 "shelves": "shelf", "cabinet": "shelf", "trolley": "cart", "crate": "box", "carton": "box",
                 "potted plant": "plant", "glass partition": "glass", "glass door": "glass", "wire": "cable",
                 "cord": "cable", "column": "pillar", "bar stool": "stool", "armchair": "chair"}


def normalize_label(label: str) -> str:
    s = " ".join(str(label).lower().replace("_", " ").replace(".", " ").split())
    if s in LABEL_ALIASES:
        return LABEL_ALIASES[s]
    if s.endswith("s") and s[:-1] in ID_PREFIX:
        return s[:-1]
    return s


def default_movable(label: str) -> bool:
    label = normalize_label(label)
    return label in MOVABLE_LABELS and label not in FIXED_LABELS


def wrap_yaw(yaw: float) -> float:
    """Box yaw is symmetric under pi: wrap to [-pi/2, pi/2)."""
    return (yaw + math.pi / 2) % math.pi - math.pi / 2


@dataclass
class BoxFit:
    center_xy: tuple[float, float]
    yaw: float
    length: float  # along yaw
    width: float


@dataclass
class CircleFit:
    center_xy: tuple[float, float]
    diameter: float
    rms: float


def _trimmed_extent(u: np.ndarray, trim: float) -> tuple[float, float]:
    if trim <= 0:
        return float(u.min()), float(u.max())
    lo, hi = np.percentile(u, [trim, 100 - trim])
    return float(lo), float(hi)


def robust_yaw(xy: np.ndarray, trim: float = 1.0, coarse_deg: float = 1.0) -> float:
    """Yaw of the minimum-area rectangle of the trimmed point set (angle search over [0, 90) deg,
    then a fine pass). Less sensitive to noise and stray points than cv2.minAreaRect alone."""
    rel = xy - xy.mean(axis=0)

    def area(a):
        c, s = math.cos(a), math.sin(a)
        u0, u1 = _trimmed_extent(rel @ np.array([c, s]), trim)
        v0, v1 = _trimmed_extent(rel @ np.array([-s, c]), trim)
        return (u1 - u0) * (v1 - v0)

    _, _, ang = cv2.minAreaRect(xy.astype(np.float32))
    cands = list(np.deg2rad(np.arange(0.0, 90.0, coarse_deg))) + [math.radians(ang) % (math.pi / 2)]
    best = min(cands, key=area)
    step = math.radians(coarse_deg)
    return float(min(best + np.linspace(-step, step, 21), key=area))


def fit_footprint_box(xy: np.ndarray, trim: float = 1.0) -> BoxFit:
    """Oriented rectangle of a 2D point set. trim > 0 (percent per side) ignores sparse noise and
    stray points; use trim=0 for exact footprints such as occupancy cells."""
    xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
    if len(xy) == 0:
        raise ValueError("empty footprint")
    if len(xy) < 3:
        c = xy.mean(axis=0)
        return BoxFit((float(c[0]), float(c[1])), 0.0, 0.05, 0.05)
    t = trim if len(xy) > 200 else 0.0
    yaw = robust_yaw(xy, trim=t)
    c, s = math.cos(yaw), math.sin(yaw)
    u0, u1 = _trimmed_extent(xy @ np.array([c, s]), t)
    v0, v1 = _trimmed_extent(xy @ np.array([-s, c]), t)
    length, width = u1 - u0, v1 - v0
    um, vm = (u0 + u1) / 2, (v0 + v1) / 2
    center = (float(um * c - vm * s), float(um * s + vm * c))
    if width > length:
        length, width, yaw = width, length, yaw + math.pi / 2
    return BoxFit(center, wrap_yaw(yaw), max(float(length), 0.02), max(float(width), 0.02))


def fit_circle(xy: np.ndarray) -> CircleFit:
    """Upright-cylinder footprint. Centre: Kasa least-squares circle on the convex hull (works for
    filled discs and for partial arcs seen from one side). Radius: if most points lie near the rim
    (wall returns), the median rim distance, which ignores sensor noise; otherwise (filled
    footprints) the 99.5th percentile radial distance."""
    xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
    if len(xy) < 3:
        c = xy.mean(axis=0)
        return CircleFit((float(c[0]), float(c[1])), 0.05, 0.0)
    hull = cv2.convexHull(xy.astype(np.float32)).reshape(-1, 2).astype(np.float64)
    pts = hull if len(hull) >= 5 else xy
    A = np.c_[2 * pts, np.ones(len(pts))]
    b = (pts ** 2).sum(axis=1)
    sol, *_ = np.linalg.lstsq(A, b, rcond=None)
    cx, cy = sol[0], sol[1]
    r2 = sol[2] + cx * cx + cy * cy
    (ex, ey), er = cv2.minEnclosingCircle(xy.astype(np.float32))
    if not np.isfinite(r2) or r2 <= 0 or math.sqrt(r2) > 2.0 * max(er, 1e-3):
        cx, cy = ex, ey
    rad = np.hypot(xy[:, 0] - cx, xy[:, 1] - cy)
    r_hi = float(np.percentile(rad, 99.5)) if len(rad) > 200 else float(rad.max())
    rim = rad > 0.85 * r_hi
    r = float(np.median(rad[rim])) if rim.mean() > 0.5 else r_hi
    rms = float(np.sqrt(np.mean((rad[rim] - r) ** 2))) if rim.any() else 0.0
    return CircleFit((float(cx), float(cy)), float(2 * r), rms)


def shape_stats(xy: np.ndarray, area: Optional[float] = None) -> dict:
    """Footprint shape descriptors. rect_circle = (trimmed box area) / (fitted circle area): about
    4/pi = 1.27 for a disc and 2/pi = 0.64 for a square, independent of sampling density."""
    xy = np.asarray(xy, dtype=np.float64).reshape(-1, 2)
    box = fit_footprint_box(xy)
    circ = fit_circle(xy)
    rect_area = box.length * box.width
    out = {"aspect": float(box.length / max(box.width, 1e-9)),
           "rect_circle": float(rect_area / max(math.pi * (circ.diameter / 2) ** 2, 1e-9)),
           "length": box.length, "width": box.width, "diameter": circ.diameter}
    if area is not None:
        out["area"] = float(area)
    return out


def footprint_shape_stats(xy: np.ndarray) -> dict:
    return shape_stats(xy)


def is_round(stats: dict) -> bool:
    return stats["rect_circle"] > 1.0 and stats["aspect"] < 1.2


def height_from_points(z: np.ndarray, floor_z: float = 0.0, pct: float = 97.0) -> float:
    z = np.asarray(z, dtype=np.float64)
    if len(z) == 0:
        return 0.0
    return float(max(np.percentile(z, pct) - floor_z, 0.02))


def fit_primitive(points: np.ndarray, label: str, *, prim_id: str, confidence: float,
                  shape: Optional[str] = None, height: Optional[float] = None,
                  floor_z: float = 0.0, source: str = "auto",
                  denoise: bool = True) -> Primitive:
    """Fit a box/cylinder primitive to a 3D point cluster (floor at floor_z, Z up).
    `height` overrides the percentile height (e.g. geometric detector heuristics)."""
    pts = np.asarray(points, dtype=np.float64).reshape(-1, 3)
    if denoise and len(pts) > 30:
        pts = pts[remove_statistical_outliers(pts)]
    label = normalize_label(label)
    xy = pts[:, :2]
    h = height if height is not None else height_from_points(pts[:, 2], floor_z)
    if shape is None:
        if label in CYLINDER_LABELS:
            shape = "cylinder"
        elif label in ID_PREFIX:
            shape = "box"
        else:
            shape = "cylinder" if is_round(footprint_shape_stats(xy)) else "box"
    if shape == "cylinder":
        c = fit_circle(xy)
        return Primitive(id=prim_id, type="cylinder", label=label,
                         pose=Pose2(xyz=(c.center_xy[0], c.center_xy[1], floor_z + h / 2), yaw=0.0),
                         size=(c.diameter, c.diameter, h), movable=default_movable(label),
                         fragile=label in FRAGILE_LABELS, confidence=float(confidence), source=source)
    b = fit_footprint_box(xy, trim=1.0 if denoise else 0.0)
    return Primitive(id=prim_id, type="box", label=label,
                     pose=Pose2(xyz=(b.center_xy[0], b.center_xy[1], floor_z + h / 2), yaw=b.yaw),
                     size=(b.length, b.width, h), movable=default_movable(label),
                     fragile=label in FRAGILE_LABELS, confidence=float(confidence), source=source)


def primitive_footprint(p: Primitive, n_circle: int = 24) -> np.ndarray:
    """Footprint polygon (N,2) of a primitive in the site frame."""
    cx, cy = p.pose.xyz[0], p.pose.xyz[1]
    if p.type == "cylinder":
        r = p.size[0] / 2
        a = np.linspace(0, 2 * np.pi, n_circle, endpoint=False)
        return np.stack([cx + r * np.cos(a), cy + r * np.sin(a)], axis=1)
    l, w = p.size[0] / 2, p.size[1] / 2
    c, s = math.cos(p.pose.yaw), math.sin(p.pose.yaw)
    corners = np.array([[l, w], [-l, w], [-l, -w], [l, -w]])
    R = np.array([[c, -s], [s, c]])
    return corners @ R.T + np.array([cx, cy])


def footprint_iou(a: Primitive, b: Primitive) -> float:
    from shapely.geometry import Polygon

    pa, pb = Polygon(primitive_footprint(a)), Polygon(primitive_footprint(b))
    if not pa.is_valid or not pb.is_valid:
        return 0.0
    inter = pa.intersection(pb).area
    union = pa.union(pb).area
    return float(inter / union) if union > 0 else 0.0


def next_ids(labels: Iterable[str], taken: Iterable[str]) -> list[str]:
    """Readable ids (T1, C2, ...) that do not collide with ids already in the twin."""
    taken = set(taken)
    counters: dict[str, int] = {}
    out = []
    for label in labels:
        prefix = ID_PREFIX.get(normalize_label(label), "O")
        n = counters.get(prefix, 0)
        while True:
            n += 1
            cand = f"{prefix}{n}"
            if cand not in taken:
                break
        counters[prefix] = n
        taken.add(cand)
        out.append(cand)
    return out
