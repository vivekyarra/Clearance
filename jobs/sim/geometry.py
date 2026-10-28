"""Robot footprints, primitive polygons and polygon rasterization onto the occupancy grid."""
from __future__ import annotations

import math
from dataclasses import dataclass

import cv2
import numpy as np
from shapely.geometry import Polygon

from clearance_core.schemas import Primitive, RobotSpec

from . import params as P


@dataclass(frozen=True)
class RobotModel:
    """Everything the simulator needs from a RobotSpec, with documented fallbacks applied."""
    robot_id: str
    footprint: np.ndarray        # (N,2) robot frame, +x forward, centered on rotation center
    footprint_approx: bool
    length: float                # x extent
    width: float                 # y extent (width needed to drive straight)
    radius: float                # circumradius about the rotation center
    in_place: bool               # plan with in-place rotation (A* over x,y,heading)
    turn_radius: float           # used by the lattice planner when in_place is False
    nominal_speed: float
    max_threshold_m: float | None
    safety_stop_m: float
    notes: tuple[str, ...] = ()


def robot_model(spec: RobotSpec) -> RobotModel:
    notes: list[str] = []
    approx = bool(spec.footprint_approx)
    if spec.footprint_polygon_m and len(spec.footprint_polygon_m) >= 3:
        fp = np.asarray(spec.footprint_polygon_m, dtype=np.float64)
    elif spec.min_aisle_width_vendor_m:
        # round body that passes the vendor's minimum aisle width with a small margin
        d = 0.95 * float(spec.min_aisle_width_vendor_m)
        ang = np.linspace(0, 2 * np.pi, 24, endpoint=False)
        fp = np.stack([0.5 * d * np.cos(ang), 0.5 * d * np.sin(ang)], axis=1)
        approx = True
        notes.append("footprint approximated as a disc of 0.95 x vendor minimum aisle width")
    else:
        raise ValueError(f"robot {spec.robot_id}: needs footprint_polygon_m or min_aisle_width_vendor_m")
    poly = Polygon(fp)
    if not poly.is_valid or poly.area <= 0:
        raise ValueError(f"robot {spec.robot_id}: footprint polygon is not a valid simple polygon")
    if poly.exterior.is_ccw is False:
        fp = fp[::-1].copy()
    length = float(fp[:, 0].max() - fp[:, 0].min())
    width = float(fp[:, 1].max() - fp[:, 1].min())
    radius = float(np.hypot(fp[:, 0], fp[:, 1]).max())

    in_place = spec.drive in ("diff_drive", "omni") and bool(spec.in_place_rotation) and spec.min_turn_radius_m <= 0
    if spec.drive == "omni" and in_place:
        notes.append("omni drive planned as forward driving with in-place rotation")
    turn_r = float(spec.min_turn_radius_m or 0.0)
    if not in_place and turn_r <= 0:
        # no spin in place: diff-drive pivots about one wheel; car-like uses half its length
        turn_r = 0.5 * (length if spec.drive == "ackermann" else width)
        notes.append(f"turn radius taken as {turn_r:.2f} m (no min_turn_radius_m given)")

    if spec.nominal_speed_mps:
        v = float(spec.nominal_speed_mps)
    elif spec.max_speed_mps:
        v = 0.8 * float(spec.max_speed_mps)
        notes.append("nominal speed taken as 0.8 x max speed")
    else:
        v = P.FALLBACK_NOMINAL_SPEED
        notes.append(f"nominal speed taken as {v} m/s (no speed in spec)")
    stop = float(spec.safety_stop_distance_m) if spec.safety_stop_distance_m else P.SAFETY_STOP_DEFAULT_M
    return RobotModel(spec.robot_id, fp, approx, length, width, radius, in_place, turn_r, v,
                      spec.max_threshold_m, stop, tuple(notes))


def transform(poly: np.ndarray, x: float, y: float, yaw: float) -> np.ndarray:
    c, s = math.cos(yaw), math.sin(yaw)
    out = np.empty_like(poly, dtype=np.float64)
    out[:, 0] = x + c * poly[:, 0] - s * poly[:, 1]
    out[:, 1] = y + s * poly[:, 0] + c * poly[:, 1]
    return out


def primitive_polygon(p: Primitive | dict, dx: float = 0.0, dy: float = 0.0, dyaw: float = 0.0) -> np.ndarray:
    """Footprint of a box/cylinder primitive in world coordinates (N,2)."""
    if isinstance(p, dict):
        p = Primitive.model_validate(p)
    x, y = p.pose.xyz[0] + dx, p.pose.xyz[1] + dy
    yaw = p.pose.yaw + dyaw
    if p.type == "cylinder":
        r = 0.5 * float(p.size[0])
        ang = np.linspace(0, 2 * np.pi, 24, endpoint=False)
        return np.stack([x + r * np.cos(ang), y + r * np.sin(ang)], axis=1)
    hl, hw = 0.5 * float(p.size[0]), 0.5 * float(p.size[1])
    rect = np.array([[hl, hw], [-hl, hw], [-hl, -hw], [hl, -hw]], dtype=np.float64)
    return transform(rect, x, y, yaw)


def offset_polygon(poly: np.ndarray, d: float) -> np.ndarray | None:
    """Grow (d>0) or shrink (d<0) a polygon. A shrink that removes the polygon keeps a small core."""
    if abs(d) < 1e-9:
        return poly
    g = Polygon(poly).buffer(d, join_style=2 if d > 0 else 1, mitre_limit=2.0)
    if g.is_empty or g.area < 1e-6:
        c = Polygon(poly).centroid
        g = c.buffer(0.01)
    if g.geom_type != "Polygon":
        g = max(g.geoms, key=lambda q: q.area)
    return np.asarray(g.exterior.coords[:-1], dtype=np.float64)


def world_to_px(poly: np.ndarray, res: float, origin: tuple[float, float], h: int) -> np.ndarray:
    """World polygon -> cv2 pixel coordinates (pixel centers at integers)."""
    px = (poly[:, 0] - origin[0]) / res - 0.5
    py = h - (poly[:, 1] - origin[1]) / res - 0.5
    return np.stack([px, py], axis=1)


def fill_polygons(mask: np.ndarray, polys: list[np.ndarray], res: float, origin, value: int = 1,
                  offset_rc: tuple[int, int] = (0, 0), full_h: int | None = None) -> None:
    """Fill polygons (world coords) into mask; cells whose centers fall inside are set.
    offset_rc/full_h let the mask be a sub-window of the full grid."""
    if not polys:
        return
    h = full_h if full_h is not None else mask.shape[0]
    shift = 4
    pts = []
    for poly in polys:
        q = world_to_px(poly, res, origin, h)
        q[:, 0] -= offset_rc[1]
        q[:, 1] -= offset_rc[0]
        pts.append(np.round(q * (1 << shift)).astype(np.int32))
    cv2.fillPoly(mask, pts, int(value), lineType=cv2.LINE_8, shift=shift)


def polygon_bbox_rc(poly: np.ndarray, res: float, origin, h: int, pad: int) -> tuple[int, int, int, int]:
    q = world_to_px(poly, res, origin, h)
    c0 = int(math.floor(q[:, 0].min())) - pad
    c1 = int(math.ceil(q[:, 0].max())) + pad + 1
    r0 = int(math.floor(q[:, 1].min())) - pad
    r1 = int(math.ceil(q[:, 1].max())) + pad + 1
    return r0, r1, c0, c1


def footprint_kernel(fp: np.ndarray, yaw: float, res: float, supersample: int = 8) -> tuple[np.ndarray, int]:
    """Cells overlapped by the footprint at heading `yaw`, as a (2h+1)^2 uint8 kernel anchored at (h,h).
    Kernel cell (a,b) corresponds to the offset (dx, dy) = ((b-h)*res, -(a-h)*res)."""
    poly = Polygon(transform(fp, 0.0, 0.0, yaw)).buffer(-1e-4)
    q = np.asarray(poly.exterior.coords[:-1], dtype=np.float64)
    rad = float(np.hypot(q[:, 0], q[:, 1]).max())
    h = int(math.ceil(rad / res + 1.0))
    n = 2 * h + 1
    S = supersample
    fine = np.zeros((n * S, n * S), np.uint8)
    fx = (q[:, 0] / res + h + 0.5) * S - 0.5
    fy = (-q[:, 1] / res + h + 0.5) * S - 0.5
    shift = 4
    pts = np.round(np.stack([fx, fy], axis=1) * (1 << shift)).astype(np.int32)
    cv2.fillPoly(fine, [pts], 1, lineType=cv2.LINE_8, shift=shift)
    k = fine.reshape(n, S, n, S).max(axis=(1, 3)).astype(np.uint8)
    return k, h


def lateral_extent(fp: np.ndarray, rel_dir: float) -> float:
    """Width of the footprint measured perpendicular to a travel direction `rel_dir` (robot frame)."""
    nx, ny = -math.sin(rel_dir), math.cos(rel_dir)
    proj = fp[:, 0] * nx + fp[:, 1] * ny
    return float(proj.max() - proj.min())
