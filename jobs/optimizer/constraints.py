"""Hard constraints for candidate layout changes.

A change (one or more moved primitives) is admissible when every moved footprint:
  1. stays inside the floor bounds polygon (grid extent if the twin has none),
  2. does not overlap other primitives (shapely) or static structure: the squares of the
     occupied/unknown cells of the grid rasterized with all primitives removed (walls, columns,
     keep-outs burned in by the rasterizer); the footprint/square intersection area is compared,
     so a rotated corner poking into a wall cell counts,
  3. does not enter a keep-out zone,
  4. keeps every waypoint at least `waypoint_min_gap_m` (default: robot circumscribed radius) away,
and the resulting grid still has a >= `egress_min_gap_m` clear path from every entrance waypoint to an
exit waypoint (distance transform on a 2x sub-cell grid + connected flood fill, geometry.egress_status).
A twin without exit waypoints (a cafe with one door) checks every other waypoint (tables, dock,
pickup) against the entrances instead, so the door path stays open for the people at the tables.

Pre-existing violations in the captured layout (a chair tucked under a table, a table already
touching a waypoint disc, an entrance that already lacks a 0.9 m path) are not the optimizer's
fault: for those the rule becomes "do not make it worse" so the object can still be moved away. For
egress that means the widest path a source had (`egress_width_required`, capped at the rule width)
must survive the change.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable

import numpy as np
import shapely
from shapely.geometry import Point, Polygon, box
from shapely.strtree import STRtree

from clearance_core.grid import Grid
from clearance_core.schemas import RobotSpec, Twin

from .geometry import egress_status, egress_widths, paint, primitive_polygon

Move = tuple[float, float, float]  # dx, dy, dyaw


@dataclass
class Rules:
    egress_min_gap_m: float = 0.9
    egress_from_kinds: tuple[str, ...] = ("entrance",)
    egress_to_kinds: tuple[str, ...] = ("exit",)
    egress_snap_m: float = 0.5
    egress_fallback: bool = True               # no exit waypoints: every other waypoint -> entrances
    waypoint_min_gap_m: float | None = None  # None -> robot circumscribed radius
    overlap_tol_m2: float = 1e-4               # 1 cm^2 of footprint overlap is treated as touching
    wall_tol_cells: int = 0                    # extra wall-cell areas tolerated beyond the original placement

    @classmethod
    def from_dict(cls, d: dict | None) -> "Rules":
        d = dict(d or {})
        unknown = set(d) - set(cls.__dataclass_fields__)
        if unknown:
            raise ValueError(f"unknown constraint rules: {sorted(unknown)}")
        for k in ("egress_from_kinds", "egress_to_kinds"):
            if k in d:
                d[k] = tuple(d[k])
        return cls(**d)


def robot_radius(robot: RobotSpec) -> float:
    if robot.footprint_polygon_m:
        return float(max(np.hypot(x, y) for x, y in robot.footprint_polygon_m))
    if robot.min_aisle_width_vendor_m:
        return robot.min_aisle_width_vendor_m / 2.0
    return 0.3  # no footprint in the spec: conservative small-service-robot default


@dataclass
class CheckResult:
    ok: bool
    reason: str = ""
    polys: dict = field(default_factory=dict)


class ConstraintChecker:
    def __init__(self, twin: Twin, robot: RobotSpec, rasterizer: Callable[..., Grid], rules: Rules | None = None,
                 bound: str = "nominal"):
        self.twin, self.rules, self.bound = twin, rules or Rules(), bound
        self._raster = rasterizer
        self.prims = {p.id: p for p in twin.primitives}
        self.base_polys = {pid: primitive_polygon(p) for pid, p in self.prims.items()}
        base = rasterizer(twin, bound)
        self.grid = base
        self.res, self.origin, self.shape = base.res, tuple(base.origin), base.data.shape
        self.walls = rasterizer(twin, bound, overrides={"remove": sorted(self.prims)}).blocked()
        h, w = self.shape
        ox, oy = self.origin
        extent = box(ox, oy, ox + w * self.res, oy + h * self.res)
        bp = twin.floor.bounds_polygon
        self.floor = (Polygon(bp).buffer(0) if len(bp) >= 3 else extent).buffer(1e-6)
        self.keepouts = {k.id: Polygon(k.polygon).buffer(0) for k in twin.keepout_zones if len(k.polygon) >= 3}
        self.waypoints = {wp.id: Point(wp.xy) for wp in twin.waypoints}
        self.wp_min_gap = self.rules.waypoint_min_gap_m
        if self.wp_min_gap is None:
            self.wp_min_gap = robot_radius(robot)
        r = self.rules
        self.sources = [(wp.id, wp.xy) for wp in twin.waypoints if wp.kind in r.egress_from_kinds]
        self.targets = [(wp.id, wp.xy) for wp in twin.waypoints if wp.kind in r.egress_to_kinds]
        if r.egress_fallback and self.sources and not self.targets:
            self.targets = self.sources
            self.sources = [(wp.id, wp.xy) for wp in twin.waypoints if wp.kind not in set(r.egress_from_kinds)]
        self._static: dict[frozenset, np.ndarray] = {}
        blocked0 = base.blocked()
        self.egress_baseline = self._egress(blocked0)
        self.egress_required = {k for k, v in self.egress_baseline.items() if v}
        self.egress_width_m = egress_widths(blocked0, self.res, self.origin, self.sources, self.targets,
                                            r.egress_min_gap_m, r.egress_snap_m)
        self.egress_width_required = {k: v for k, v in self.egress_width_m.items() if v > 0}
        self._wall_tol = r.wall_tol_cells * self.res * self.res + r.overlap_tol_m2
        self._tree_ids = sorted(self.base_polys)
        self._tree = STRtree([self.base_polys[i] for i in self._tree_ids]) if self._tree_ids else None
        self._wall_area0 = {pid: self.wall_area(poly) for pid, poly in self.base_polys.items()}

    # ---- helpers ----
    def wall_area(self, poly) -> float:
        """Area (m^2) of `poly` inside the squares of static blocked cells (walls, columns, keep-outs)."""
        h, w = self.shape
        res, (ox, oy) = self.res, self.origin
        minx, miny, maxx, maxy = poly.bounds
        c0, c1 = max(int(np.floor((minx - ox) / res)), 0), min(int(np.floor((maxx - ox) / res)), w - 1)
        r0 = max(h - 1 - int(np.floor((maxy - oy) / res)), 0)
        r1 = min(h - 1 - int(np.floor((miny - oy) / res)), h - 1)
        if c1 < c0 or r1 < r0:
            return 0.0
        rr, cc = np.nonzero(self.walls[r0:r1 + 1, c0:c1 + 1])
        if rr.size == 0:
            return 0.0
        x0 = ox + (cc + c0) * res
        y0 = oy + (h - 1 - (rr + r0)) * res
        cells = shapely.box(x0, y0, x0 + res, y0 + res)
        return float(shapely.area(shapely.intersection(cells, poly)).sum())

    def _egress(self, blocked: np.ndarray, width: float | None = None) -> dict[str, bool]:
        r = self.rules
        return egress_status(blocked, self.res, self.origin, self.sources, self.targets,
                             r.egress_min_gap_m if width is None else width, r.egress_snap_m)

    def egress_lost(self, blocked: np.ndarray) -> list[str]:
        """Sources whose baseline path width (capped at the rule width) no longer fits."""
        lost = []
        for wd in sorted(set(self.egress_width_required.values())):
            ids = {k for k, v in self.egress_width_required.items() if v == wd}
            status = self._egress(blocked, wd)
            lost += [k for k in ids if not status.get(k)]
        return sorted(lost)

    def static_without(self, ids) -> np.ndarray:
        key = frozenset(ids)
        if key not in self._static:
            if len(self._static) > 256:
                self._static.clear()
            self._static[key] = self._raster(self.twin, self.bound, overrides={"remove": sorted(key)}).blocked()
        return self._static[key]

    # ---- main check ----
    def check(self, moves: dict[str, Move], *, egress: bool = True) -> CheckResult:
        unknown = sorted(set(moves) - set(self.prims))
        if unknown:
            return CheckResult(False, "unknown:" + ",".join(unknown))
        new = {pid: primitive_polygon(self.prims[pid], *mv) for pid, mv in moves.items()}
        tol = self.rules.overlap_tol_m2
        for pid, poly in new.items():
            if not self.prims[pid].movable:
                return CheckResult(False, "fixed")
            if not poly.is_valid or poly.is_empty:
                return CheckResult(False, "invalid")
            if not self.floor.contains(poly):
                return CheckResult(False, "outside_floor")
            if self.wall_area(poly) > self._wall_area0[pid] + self._wall_tol:
                return CheckResult(False, "overlap:wall")
            base = self.base_polys[pid]
            near = {self._tree_ids[i] for i in self._tree.query(poly)} | set(new)
            for oid in sorted(near - {pid}):
                other0 = self.base_polys[oid]
                other = new.get(oid, other0)
                if poly.intersection(other).area > base.intersection(other0).area + tol:
                    return CheckResult(False, f"overlap:{oid}")
            for kid, kz in self.keepouts.items():
                if poly.intersection(kz).area > base.intersection(kz).area + tol:
                    return CheckResult(False, f"keepout:{kid}")
            for wid, pt in self.waypoints.items():
                d = poly.distance(pt)
                if d < self.wp_min_gap - 1e-9 and d < base.distance(pt) - 1e-9:
                    return CheckResult(False, f"waypoint:{wid}")
        if egress and self.egress_width_required:
            blocked = paint(self.static_without(new).copy(), new.values(), self.res, self.origin, True)
            lost = self.egress_lost(blocked)
            if lost:
                return CheckResult(False, "egress:" + ",".join(lost))
        return CheckResult(True, "", new)
