"""Geometry helpers for the layout optimizer: primitive footprints (shapely), polygon rasterization
onto the occupancy grid convention of clearance_core.grid, and the human-egress gap check.

Overrides follow docs/CONTRACTS.md: {"move": {obj_id: {"dx","dy","dyaw"}}, "remove": [obj_id],
"add_boxes": [Primitive...]}. dx/dy are in the site frame (metres, +x east, +y north), dyaw is in
radians, counter-clockwise positive, applied about the primitive's own centre.
"""
from __future__ import annotations

import math
from collections import deque
from typing import Iterable

import numpy as np
import shapely
from scipy import ndimage
from shapely import affinity
from shapely.geometry import Point, box
from shapely.geometry.base import BaseGeometry

from clearance_core import grid as core_grid
from clearance_core.grid import FREE, OCC, Grid
from clearance_core.schemas import Primitive, Twin


def primitive_polygon(p: Primitive, dx: float = 0.0, dy: float = 0.0, dyaw: float = 0.0) -> BaseGeometry:
    """2D footprint of a primitive after an optional rigid move."""
    x, y = p.pose.xyz[0] + dx, p.pose.xyz[1] + dy
    if p.type == "cylinder":
        return Point(x, y).buffer(p.size[0] / 2.0, quad_segs=16)
    l, w = p.size[0], p.size[1]
    poly = affinity.rotate(box(-l / 2, -w / 2, l / 2, w / 2), p.pose.yaw + dyaw, origin=(0, 0), use_radians=True)
    return affinity.translate(poly, x, y)


def move_of(overrides: dict | None, obj_id: str) -> tuple[float, float, float]:
    mv = ((overrides or {}).get("move") or {}).get(obj_id)
    if not mv:
        return 0.0, 0.0, 0.0
    return float(mv.get("dx", 0.0)), float(mv.get("dy", 0.0)), float(mv.get("dyaw", 0.0))


def polygon_cells(poly: BaseGeometry, shape: tuple[int, int], res: float, origin) -> tuple[np.ndarray, np.ndarray]:
    """(rows, cols) of grid cells whose centres lie inside `poly` (clearance_core.grid pixel convention)."""
    if poly.is_empty:
        return np.empty(0, int), np.empty(0, int)
    h, w = shape
    minx, miny, maxx, maxy = poly.bounds
    c0 = max(int(math.floor((minx - origin[0]) / res)), 0)
    c1 = min(int(math.ceil((maxx - origin[0]) / res)), w - 1)
    r0 = max(h - 1 - int(math.ceil((maxy - origin[1]) / res)), 0)
    r1 = min(h - 1 - int(math.floor((miny - origin[1]) / res)), h - 1)
    if c1 < c0 or r1 < r0:
        return np.empty(0, int), np.empty(0, int)
    rr, cc = np.mgrid[r0:r1 + 1, c0:c1 + 1]
    xs = origin[0] + (cc + 0.5) * res
    ys = origin[1] + (h - rr - 0.5) * res
    inside = shapely.contains_xy(poly, xs, ys)
    return rr[inside], cc[inside]


def paint(mask: np.ndarray, polys: Iterable[BaseGeometry], res: float, origin, value=True) -> np.ndarray:
    for poly in polys:
        r, c = polygon_cells(poly, mask.shape, res, origin)
        mask[r, c] = value
    return mask


def fallback_rasterize(twin: Twin, bound: str = "nominal", p90_err_m: float | None = None,
                       overrides: dict | None = None) -> Grid:
    """Self-contained rasterizer used when jobs.sim.api is not importable (same overrides format).
    Loads the twin occupancy PNG, clears the original footprints of moved/removed
    primitives, then paints every remaining primitive at its (possibly moved) pose plus add_boxes."""
    base = core_grid.load(twin.occupancy)
    data = base.data.copy()
    res, origin = base.res, base.origin
    ov = overrides or {}
    removed = set(ov.get("remove") or [])
    moved = set((ov.get("move") or {}).keys())
    for p in twin.primitives:
        if p.id in removed or p.id in moved:
            paint(data, [primitive_polygon(p).buffer(0.5 * res)], res, origin, FREE)
    for p in twin.primitives:
        if p.id in removed:
            continue
        paint(data, [primitive_polygon(p, *move_of(ov, p.id))], res, origin, OCC)
    for b in ov.get("add_boxes") or []:
        prim = b if isinstance(b, Primitive) else Primitive.model_validate(b)
        paint(data, [primitive_polygon(prim)], res, origin, OCC)
    err = p90_err_m if p90_err_m is not None else (twin.accuracy.p90_abs_err_m or 0.0)
    k = int(round(err / res))
    if bound != "nominal" and k > 0:
        blocked = data != FREE
        st = ndimage.generate_binary_structure(2, 1)
        if bound == "conservative":
            blocked = ndimage.binary_dilation(blocked, st, iterations=k)
        else:
            blocked = ndimage.binary_erosion(blocked, st, iterations=k, border_value=1)
        data = np.where(blocked, OCC, FREE).astype(np.uint8)
    return Grid(data, res, origin)


# ---------- human egress ----------

EGRESS_SUBCELLS = 2


def clear_mask(blocked: np.ndarray, res: float, width_m: float) -> np.ndarray:
    """Cells where a disc of diameter `width_m` centred on the cell is collision free.
    Distance transform (cell centre to nearest blocked cell centre, grid border counts as blocked);
    the discretization tolerance is about one cell."""
    free = ~blocked
    padded = np.pad(free, 1, constant_values=False)
    edt = ndimage.distance_transform_edt(padded)[1:-1, 1:-1] * res
    return free & (edt >= width_m / 2.0 - 1e-6)


def _seed_cells(mask: np.ndarray, xy, res: float, origin, snap_m: float) -> np.ndarray:
    """Flat indices of clear cells within snap_m of a waypoint (doors are rarely centred on a cell)."""
    r, c = polygon_cells(Point(xy).buffer(snap_m), mask.shape, res, origin)
    keep = mask[r, c]
    return r[keep] * mask.shape[1] + c[keep]


def bfs_reachable(mask: np.ndarray, seeds: np.ndarray) -> np.ndarray:
    """4-connected breadth-first flood fill over `mask` from flat seed indices."""
    h, w = mask.shape
    seen = np.zeros(h * w, bool)
    flat = mask.ravel()
    q = deque()
    for s in seeds:
        if flat[s] and not seen[s]:
            seen[s] = True
            q.append(int(s))
    while q:
        i = q.popleft()
        r, c = divmod(i, w)
        for j, ok in ((i - w, r > 0), (i + w, r < h - 1), (i - 1, c > 0), (i + 1, c < w - 1)):
            if ok and flat[j] and not seen[j]:
                seen[j] = True
                q.append(j)
    return seen.reshape(h, w)


def _fine(blocked: np.ndarray, res: float, factor: int) -> tuple[np.ndarray, float]:
    """Each cell split into factor x factor sub-cells (same origin)."""
    if factor <= 1:
        return blocked, res
    return np.repeat(np.repeat(blocked, factor, axis=0), factor, axis=1), res / factor


def _labels(blocked: np.ndarray, res: float, width_m: float, factor: int):
    fb, fres = _fine(blocked, res, factor)
    mask = clear_mask(fb, fres, width_m)
    labels, _ = ndimage.label(mask)  # 4-connectivity by default
    return mask, labels, fres


def egress_status(blocked: np.ndarray, res: float, origin, sources: list[tuple[str, tuple]],
                  targets: list[tuple[str, tuple]], width_m: float, snap_m: float = 0.5,
                  factor: int = EGRESS_SUBCELLS) -> dict[str, bool]:
    """For each source waypoint id: is there a path of clear width >= width_m to any target?
    The clear test runs on a grid split into factor x factor sub-cells: on the original grid the
    disc centres sit on cell centres, which accepts a corridor one cell narrower than width_m when
    its cell count is odd; on the 2x grid a corridor of k cells passes exactly when k * res >= width_m.
    Connected-component labelling is equivalent to one BFS per component (`bfs_reachable` is the
    reference implementation the tests compare against)."""
    if not sources or not targets:
        return {}
    mask, labels, fres = _labels(blocked, res, width_m, factor)
    flat = labels.ravel()
    target_labels: set[int] = set()
    for _, xy in targets:
        target_labels.update(int(v) for v in flat[_seed_cells(mask, xy, fres, origin, snap_m)])
    target_labels.discard(0)
    out = {}
    for sid, xy in sources:
        lab = {int(v) for v in flat[_seed_cells(mask, xy, fres, origin, snap_m)]}
        out[sid] = bool(lab & target_labels)
    return out


def egress_widths(blocked: np.ndarray, res: float, origin, sources: list[tuple[str, tuple]],
                  targets: list[tuple[str, tuple]], max_width_m: float, snap_m: float = 0.5,
                  factor: int = EGRESS_SUBCELLS) -> dict[str, float]:
    """Widest clear path (multiple of one sub-cell, capped at max_width_m) from each source to any
    target; 0.0 when no path exists at all. Bisection over the sub-cell width steps."""
    if not sources or not targets:
        return {}
    step = res / max(factor, 1)
    n_max = int(math.floor(max_width_m / step + 1e-9))
    memo: dict[int, dict[str, bool]] = {}

    def ok(n: int, sid: str) -> bool:
        if n not in memo:
            memo[n] = egress_status(blocked, res, origin, sources, targets, n * step, snap_m, factor)
        return memo[n][sid]

    out = {}
    for sid, _ in sources:
        if n_max >= 1 and ok(n_max, sid):
            out[sid] = float(max_width_m)
            continue
        if n_max < 1 or not ok(1, sid):
            out[sid] = 0.0
            continue
        lo, hi = 1, n_max  # width lo*step passes, hi*step fails
        while hi - lo > 1:
            mid = (lo + hi) // 2
            if ok(mid, sid):
                lo = mid
            else:
                hi = mid
        out[sid] = round(lo * step, 6)
    return out
