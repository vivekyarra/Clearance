"""Twin -> occupancy layers: primitives, operator-confirmed clear zones, keep-out zones, overrides, thresholds
and geometry bounds."""
from __future__ import annotations

import base64
import hashlib
import json
import math
from dataclasses import dataclass, field

import cv2
import numpy as np

from clearance_core import grid as cgrid
from clearance_core import storage
from clearance_core.grid import FREE, OCC, UNKNOWN, Grid
from clearance_core.schemas import ClearZone, Primitive, Twin

from . import params as P
from .geometry import fill_polygons, offset_polygon, primitive_polygon

_GRID_CACHE: dict = {}


def load_base_grid(twin: Twin, occ_png_b64: str | None = None) -> Grid:
    """Raw twin occupancy (cached per process). `occ_png_b64` lets a shard carry the PNG inline."""
    occ = twin.occupancy
    if occ_png_b64:
        key = ("b64", hashlib.sha1(occ_png_b64.encode()).hexdigest())
    else:
        p = storage.DATA / occ.uri
        try:
            st = p.stat()
        except OSError as e:
            raise FileNotFoundError(f"occupancy not found: {occ.uri} (looked in {storage.DATA})") from e
        key = (str(p), st.st_mtime_ns, st.st_size)
    g = _GRID_CACHE.get(key)
    if g is None:
        if occ_png_b64:
            buf = np.frombuffer(base64.b64decode(occ_png_b64), np.uint8)
            img = cv2.imdecode(buf, cv2.IMREAD_GRAYSCALE)
            if img is None:
                raise ValueError("occ_png_b64 is not a decodable PNG")
            g = Grid(img, occ.resolution_m, tuple(occ.origin_xy))
        else:
            g = cgrid.load(occ)
        # snap any intermediate grey levels to the three-valued convention
        d = g.data
        snapped = np.full_like(d, UNKNOWN)
        snapped[d >= 192] = FREE
        snapped[d < 64] = OCC
        g = Grid(snapped, float(g.res), (float(g.origin[0]), float(g.origin[1])))
        if len(_GRID_CACHE) > 8:
            _GRID_CACHE.clear()
        _GRID_CACHE[key] = g
    return g


def clear_zone_mask(zones: list[ClearZone], shape: tuple[int, int], res: float, origin) -> np.ndarray:
    """Cells whose centers lie strictly inside any clear zone with at least three points (exact even-odd test
    on cell centers, so a zone never opens a cell beyond its outline)."""
    h, w = shape
    m = np.zeros(shape, bool)
    for z in zones:
        if len(z.polygon) < 3:
            continue
        poly = np.asarray(z.polygon, dtype=np.float64)
        c0 = max(int(math.floor((poly[:, 0].min() - origin[0]) / res)), 0)
        c1 = min(int(math.ceil((poly[:, 0].max() - origin[0]) / res)), w - 1)
        r0 = max(h - 1 - int(math.ceil((poly[:, 1].max() - origin[1]) / res)), 0)
        r1 = min(h - 1 - int(math.floor((poly[:, 1].min() - origin[1]) / res)), h - 1)
        if c1 < c0 or r1 < r0:
            continue
        xs = origin[0] + (np.arange(c0, c1 + 1) + 0.5) * res
        ys = origin[1] + (h - np.arange(r0, r1 + 1) - 0.5) * res
        X, Y = np.meshgrid(xs, ys)
        inside = np.zeros(X.shape, bool)
        xj, yj = poly[-1]
        for xi, yi in poly:
            if yi != yj:
                cross = ((yi > Y) != (yj > Y)) & (X < (xj - xi) * (Y - yi) / (yj - yi) + xi)
                inside ^= cross
            xj, yj = xi, yi
        m[r0:r1 + 1, c0:c1 + 1] |= inside
    return m


def apply_clear_zones(cells: np.ndarray, zones: list[ClearZone], res: float, origin) -> tuple[np.ndarray, int]:
    """UNKNOWN -> FREE inside the operator's clear zones; OCC and FREE cells are left exactly as they are.
    -> (new cells, number of cells opened). Idempotent."""
    if not zones:
        return cells, 0
    inside = clear_zone_mask(zones, cells.shape, res, origin) & (cells == UNKNOWN)
    if not inside.any():
        return cells, 0
    out = cells.copy()
    out[inside] = FREE
    return out, int(inside.sum())


def polygon_area_m2(poly) -> float:
    """Shoelace area of a simple polygon in square metres."""
    if len(poly) < 3:
        return 0.0
    a = 0.0
    for (x1, y1), (x2, y2) in zip(poly, list(poly[1:]) + [poly[0]]):
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


def clear_zone_summary(twin: Twin, grid: Grid | None = None) -> dict | None:
    """{"count", "area_m2", "confirmed_by": [..], "ids": [..], "cells_opened"?} for report notes; None without zones."""
    zones = [z for z in twin.clear_zones if len(z.polygon) >= 3]
    if not zones:
        return None
    out = {"count": len(zones), "area_m2": round(sum(polygon_area_m2(z.polygon) for z in zones), 3),
           "confirmed_by": sorted({z.confirmed_by for z in zones if z.confirmed_by}), "ids": [z.id for z in zones]}
    if grid is not None:
        n = int((clear_zone_mask(zones, grid.data.shape, grid.res, grid.origin) & (grid.data == UNKNOWN)).sum())
        out["unseen_area_m2"] = round(n * grid.res * grid.res, 3)
    return out


def bound_radius(twin: Twin, bound: str, p90_err_m: float | None) -> float:
    """Signed offset applied to obstacles: + dilate (conservative), - erode (optimistic), 0 nominal."""
    if bound == "nominal":
        return 0.0
    r = p90_err_m if p90_err_m is not None else twin.accuracy.p90_abs_err_m
    r = float(r) if r is not None else P.DEFAULT_P90_M
    if bound == "conservative":
        return r
    if bound == "optimistic":
        return -r
    raise ValueError(f"unknown geometry bound {bound!r}")


def apply_overrides(prims: list[Primitive], overrides: dict | None) -> list[Primitive]:
    """overrides = {"move": {id: {"dx","dy","dyaw"}}, "remove": [id], "add_boxes": [Primitive|dict]}.
    dyaw is in radians. Unknown object ids raise ValueError."""
    if not overrides:
        return list(prims)
    ids = {p.id for p in prims}
    move = overrides.get("move") or {}
    remove = set(overrides.get("remove") or [])
    missing = (set(move) | remove) - ids
    if missing:
        raise ValueError(f"overrides reference unknown object ids: {sorted(missing)}")
    out = []
    for p in prims:
        if p.id in remove:
            continue
        if p.id in move:
            m = move[p.id] or {}
            x, y, z = p.pose.xyz
            p = p.model_copy(deep=True)
            p.pose.xyz = (x + float(m.get("dx", 0.0)), y + float(m.get("dy", 0.0)), z)
            p.pose.yaw = p.pose.yaw + float(m.get("dyaw", 0.0))
        out.append(p)
    for i, b in enumerate(overrides.get("add_boxes") or []):
        if isinstance(b, Primitive):
            out.append(b)
            continue
        d = dict(b)
        d.setdefault("id", f"added_{i}")
        d.setdefault("type", "box")
        d.setdefault("label", "added box")
        d.setdefault("source", "user")
        if "pose" not in d and "xy" in d:
            d["pose"] = {"xyz": (d["xy"][0], d["xy"][1], 0.0), "yaw": d.get("yaw", 0.0)}
        if len(d.get("size", ())) == 2:
            d["size"] = (d["size"][0], d["size"][1], 1.0)
        out.append(Primitive.model_validate(d))
    return out


def _bound_cells(cells: np.ndarray, r: float, res: float) -> np.ndarray:
    out = cells.copy()
    if abs(r) < 1e-9:
        return out
    blocked = (cells != FREE).astype(np.uint8)
    rc = abs(r) / res + 1e-6
    if r > 0:
        d = cv2.distanceTransform(1 - blocked, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
        out[(blocked == 0) & (d <= rc)] = OCC
    else:
        d = cv2.distanceTransform(blocked, cv2.DIST_L2, cv2.DIST_MASK_PRECISE)
        ridge = d >= cv2.dilate(d, np.ones((3, 3), np.uint8)) - 1e-6  # keeps thin walls closed
        out[(blocked == 1) & (d <= rc) & ~ridge] = FREE
    return out


@dataclass
class ThresholdInfo:
    id: str
    height_m: float | None
    status: str
    mask: np.ndarray  # bool HxW, robot center may not cross when the threshold blocks


@dataclass
class Layers:
    res: float
    origin: tuple[float, float]
    shape: tuple[int, int]
    static_cells: np.ndarray        # uint8 OCC/UNKNOWN/FREE without primitives, bound applied
    keepout: np.ndarray             # bool
    fixed_polys: list[np.ndarray]   # bound applied
    movables: list[Primitive]
    bound_r: float
    thresholds: list[ThresholdInfo] = field(default_factory=list)
    prims: list[Primitive] = field(default_factory=list)

    def bounded(self, poly: np.ndarray) -> np.ndarray:
        return offset_polygon(poly, self.bound_r)

    def movable_polys(self, jitter: dict[str, tuple[float, float, float]] | None = None) -> list[np.ndarray]:
        out = []
        for p in self.movables:
            dx, dy, dyaw = (jitter or {}).get(p.id, (0.0, 0.0, 0.0))
            out.append(self.bounded(primitive_polygon(p, dx, dy, dyaw)))
        return out

    def compose(self, polys: list[np.ndarray], keepouts: bool = True) -> np.ndarray:
        """Three-valued grid with the given (already bounded) polygons burned in."""
        cells = self.static_cells.copy()
        fill_polygons(cells, self.fixed_polys + list(polys), self.res, self.origin, OCC)
        if keepouts:
            cells[self.keepout] = OCC
        return cells

    def world_to_cell(self, x, y):
        h = self.shape[0]
        c = np.floor((np.asarray(x) - self.origin[0]) / self.res).astype(int)
        r = h - 1 - np.floor((np.asarray(y) - self.origin[1]) / self.res).astype(int)
        return r, c


def build_layers(twin: Twin, bound: str = "nominal", p90_err_m: float | None = None,
                 overrides: dict | None = None, occ_png_b64: str | None = None) -> Layers:
    base = load_base_grid(twin, occ_png_b64)
    res, origin = base.res, base.origin
    h, w = base.data.shape
    r = bound_radius(twin, bound, p90_err_m)
    prims = apply_overrides(twin.primitives, overrides)

    # operator-confirmed clear floor: unseen cells become free, measured obstacles stay (before the bound,
    # so conservative dilation still grows every real obstacle)
    cleared, _ = apply_clear_zones(base.data, twin.clear_zones, res, origin)
    # user/primitives are authoritative inside their footprints: clear the scan there first
    cleared = cleared.copy()
    clear_mask = np.zeros((h, w), np.uint8)
    fill_polygons(clear_mask, [primitive_polygon(p) for p in twin.primitives], res, origin, 1)
    cleared[clear_mask.astype(bool)] = FREE
    static_cells = _bound_cells(cleared, r, res)

    keep = np.zeros((h, w), np.uint8)
    fill_polygons(keep, [np.asarray(z.polygon, dtype=np.float64) for z in twin.keepout_zones if len(z.polygon) >= 3],
                  res, origin, 1)
    fixed = [offset_polygon(primitive_polygon(p), r) for p in prims if not p.movable]
    movables = [p for p in prims if p.movable]

    thr = []
    for t in twin.thresholds:
        m = np.zeros((h, w), np.uint8)
        (x1, y1), (x2, y2) = t.segment
        L = math.hypot(x2 - x1, y2 - y1)
        ext = 2.0 * res
        if L > 1e-9:
            ux, uy = (x2 - x1) / L, (y2 - y1) / L
            x1, y1, x2, y2 = x1 - ux * ext, y1 - uy * ext, x2 + ux * ext, y2 + uy * ext
        pts = []
        for x, y in ((x1, y1), (x2, y2)):
            pts.append((int(round((x - origin[0]) / res - 0.5)), int(round(h - (y - origin[1]) / res - 0.5))))
        cv2.line(m, pts[0], pts[1], 1, thickness=3, lineType=cv2.LINE_8)
        thr.append(ThresholdInfo(t.id, t.height_m, t.status, m.astype(bool)))
    return Layers(res, origin, (h, w), static_cells, keep.astype(bool), fixed, movables, r, thr, prims)


def rasterize_twin(twin: Twin, bound: str = "nominal", p90_err_m: float | None = None,
                   overrides: dict | None = None) -> Grid:
    """Robot-view occupancy: scan + primitives (authoritative in their footprints) + keep-out zones,
    with the geometry bound applied (conservative dilates, optimistic erodes by the p90 scan error).
    Unknown cells stay UNKNOWN (the simulator treats them as blocked), except inside operator-confirmed clear
    zones, where they count as free floor."""
    L = build_layers(twin, bound, p90_err_m, overrides)
    cells = L.compose(L.movable_polys())
    return Grid(cells, L.res, L.origin)


def classify_thresholds(layers: Layers, max_threshold_m: float | None):
    """-> (block_mask, unknown_mask, {id: "pass"|"block"|"unknown"})"""
    h, w = layers.shape
    block = np.zeros((h, w), bool)
    unk = np.zeros((h, w), bool)
    cls = {}
    for t in layers.thresholds:
        if t.status == "flagged_manual" or t.height_m is None:
            c = "unknown"
        elif t.height_m <= 1e-9:
            c = "pass"
        elif max_threshold_m is None:
            c = "unknown"
        elif t.height_m > max_threshold_m + 1e-9:
            c = "block"
        else:
            c = "pass"
        cls[t.id] = c
        if c == "block":
            block |= t.mask
        elif c == "unknown":
            unk |= t.mask
    return block, unk, cls


def layers_key(twin: Twin, bound: str, p90: float | None, overrides: dict | None, extra: str = "") -> str:
    blob = json.dumps([twin.model_dump(mode="json"), bound, p90, _geom_overrides(overrides), extra],
                      sort_keys=True, default=str)
    return hashlib.sha1(blob.encode()).hexdigest()


def _geom_overrides(overrides: dict | None) -> dict:
    if not overrides:
        return {}
    out = {}
    for k in ("move", "remove", "add_boxes"):
        if overrides.get(k):
            v = overrides[k]
            if k == "add_boxes":
                v = [b.model_dump(mode="json") if isinstance(b, Primitive) else b for b in v]
            out[k] = v
    return out
