"""Independent re-check of optimizer changes (does not import jobs.optimizer).

Footprints are rebuilt from corner math, walls are the union of the blocked cell squares of the twin
rasterized without primitives, and egress uses a disc dilation over cell squares (disc centres on a
2x2 lattice per cell) instead of a distance transform. A change passes when every moved object stays
inside the floor, overlaps no wall, object or keep-out more than in the captured layout, keeps the
waypoint gap, and every egress source keeps its widest path (capped at 0.9 m).
"""
from __future__ import annotations

import math

import numpy as np
from scipy import ndimage
from shapely.geometry import Point, Polygon, box
from shapely.ops import unary_union

from clearance_core.grid import FREE

AREA_TOL = 1e-4  # 1 cm^2


def footprint(p, dx=0.0, dy=0.0, dyaw=0.0):
    x, y = p.pose.xyz[0] + dx, p.pose.xyz[1] + dy
    if p.type == "cylinder":
        return Point(x, y).buffer(p.size[0] / 2.0, quad_segs=32)
    a = p.pose.yaw + dyaw
    ca, sa = math.cos(a), math.sin(a)
    hl, hw = p.size[0] / 2.0, p.size[1] / 2.0
    pts = [(x + ca * u - sa * v, y + sa * u + ca * v) for u, v in ((hl, hw), (-hl, hw), (-hl, -hw), (hl, -hw))]
    return Polygon(pts)


def cell_squares(blocked: np.ndarray, res: float, origin, region) -> object:
    """Union of blocked cell squares intersecting `region` bounds."""
    h, w = blocked.shape
    minx, miny, maxx, maxy = region.bounds
    c0, c1 = max(int((minx - origin[0]) / res) - 1, 0), min(int((maxx - origin[0]) / res) + 1, w - 1)
    r0, r1 = max(h - 2 - int((maxy - origin[1]) / res), 0), min(h - int((miny - origin[1]) / res), h - 1)
    sq = []
    for r in range(r0, r1 + 1):
        for c in range(c0, c1 + 1):
            if blocked[r, c]:
                x0 = origin[0] + c * res
                y0 = origin[1] + (h - 1 - r) * res
                sq.append(box(x0, y0, x0 + res, y0 + res))
    return unary_union(sq) if sq else Polygon()


def disc_clear(blocked: np.ndarray, res: float, width_m: float, sub: int = 2) -> np.ndarray:
    """Disc centres on a sub x sub sample lattice per cell: True where a disc of diameter width_m
    touches no blocked cell square (grid border blocked). Returned on the sample lattice."""
    fb = np.repeat(np.repeat(blocked, sub, axis=0), sub, axis=1)
    fres = res / sub
    rad = width_m / 2.0
    k = int(math.ceil(rad / fres)) + 1
    ii, jj = np.mgrid[-k:k + 1, -k:k + 1]
    gx = np.maximum(np.abs(jj) * fres - fres / 2.0, 0.0)
    gy = np.maximum(np.abs(ii) * fres - fres / 2.0, 0.0)
    st = np.hypot(gx, gy) < rad - 1e-9
    padded = np.pad(fb, k, constant_values=True)
    bad = ndimage.binary_dilation(padded, structure=st)[k:-k, k:-k]
    return ~fb & ~bad


def egress_sets(twin):
    """(sources, targets): entrances -> exits; without exits, every other waypoint -> entrances."""
    ent = [w for w in twin.waypoints if w.kind == "entrance"]
    ex = [w for w in twin.waypoints if w.kind == "exit"]
    if ex:
        return ent, ex
    return [w for w in twin.waypoints if w.kind != "entrance"], ent


def egress_ok(blocked, res, origin, twin, width_m=0.9, snap_m=0.5, sub=2) -> dict[str, bool]:
    src, dst = egress_sets(twin)
    if not src or not dst:
        return {}
    mask = disc_clear(blocked, res, width_m, sub)
    lab, _ = ndimage.label(mask)
    h, w = mask.shape
    fres = res / sub
    rr, cc = np.mgrid[0:h, 0:w]
    xs = origin[0] + (cc + 0.5) * fres
    ys = origin[1] + (h - rr - 0.5) * fres

    def labels_near(xy):
        near = np.hypot(xs - xy[0], ys - xy[1]) < snap_m
        return set(np.unique(lab[near & mask]).tolist()) - {0}

    tl = set().union(*[labels_near(t.xy) for t in dst])
    return {s.id: bool(labels_near(s.xy) & tl) for s in src}


def egress_width(blocked, res, origin, twin, cap=0.9, sub=2) -> dict[str, float]:
    """Widest passing width per source on the sub-cell step lattice, capped at `cap` (0 = no path)."""
    step = res / sub
    n_max = int(cap / step + 1e-9)
    memo: dict[int, dict[str, bool]] = {}

    def ok(n, sid):
        if n not in memo:
            memo[n] = egress_ok(blocked, res, origin, twin, cap if n == n_max else n * step, sub=sub)
        return memo[n].get(sid, False)

    out = {}
    for s in egress_sets(twin)[0]:
        if ok(n_max, s.id):
            out[s.id] = cap
        elif not ok(1, s.id):
            out[s.id] = 0.0
        else:
            lo, hi = 1, n_max
            while hi - lo > 1:
                mid = (lo + hi) // 2
                lo, hi = (mid, hi) if ok(mid, s.id) else (lo, mid)
            out[s.id] = round(lo * step, 6)
    return out


def robot_radius(robot) -> float:
    if robot.footprint_polygon_m:
        return max(math.hypot(x, y) for x, y in robot.footprint_polygon_m)
    if robot.min_aisle_width_vendor_m:
        return robot.min_aisle_width_vendor_m / 2.0
    return 0.3


class Validator:
    def __init__(self, twin, robot, rasterizer, bound="nominal", egress_m=0.9):
        self.twin, self.rasterizer, self.bound, self.egress_m = twin, rasterizer, bound, egress_m
        self.prims = {p.id: p for p in twin.primitives}
        g0 = rasterizer(twin, bound)
        self.res, self.origin = g0.res, tuple(g0.origin)
        self.walls = rasterizer(twin, bound, overrides={"remove": sorted(self.prims)}).data != FREE
        bp = twin.floor.bounds_polygon
        h, w = g0.data.shape
        ext = box(self.origin[0], self.origin[1], self.origin[0] + w * self.res, self.origin[1] + h * self.res)
        self.floor = Polygon(bp).buffer(0) if len(bp) >= 3 else ext
        self.keepouts = [Polygon(k.polygon).buffer(0) for k in twin.keepout_zones if len(k.polygon) >= 3]
        self.wp_gap = robot_radius(robot)
        self.egress0 = egress_width(g0.data != FREE, self.res, self.origin, twin, egress_m)

    def wall_area(self, poly):
        return poly.intersection(cell_squares(self.walls, self.res, self.origin, poly.buffer(self.res))).area

    def violations(self, change: dict, *, wall_tol_m2: float = AREA_TOL) -> list[str]:
        mv = change.get("move") or {}
        moved = {oid: footprint(self.prims[oid], m.get("dx", 0.0), m.get("dy", 0.0), m.get("dyaw", 0.0))
                 for oid, m in mv.items()}
        base = {oid: footprint(p) for oid, p in self.prims.items()}
        out = []
        for oid, poly in moved.items():
            b = base[oid]
            if not self.prims[oid].movable:
                out.append(f"{oid}: not movable")
            if poly.difference(self.floor).area > AREA_TOL:
                out.append(f"{oid}: outside floor by {poly.difference(self.floor).area:.5f} m2")
            wa, wb = self.wall_area(poly), self.wall_area(b)
            if wa > wb + wall_tol_m2:
                out.append(f"{oid}: into wall {wa - wb:.5f} m2")
            for pid, other0 in base.items():
                if pid == oid:
                    continue
                other = moved.get(pid, other0)
                if poly.intersection(other).area > b.intersection(other0).area + AREA_TOL:
                    out.append(f"{oid}: overlaps {pid}")
            for kz in self.keepouts:
                if poly.intersection(kz).area > b.intersection(kz).area + AREA_TOL:
                    out.append(f"{oid}: keep-out")
            for wp in self.twin.waypoints:
                d, d0 = poly.distance(Point(wp.xy)), b.distance(Point(wp.xy))
                if d < self.wp_gap - 1e-6 and d < d0 - 1e-6:
                    out.append(f"{oid}: waypoint {wp.id} gap {d:.3f} m")
        need = {k: v for k, v in self.egress0.items() if v > 0}
        if need:
            g = self.rasterizer(self.twin, self.bound, overrides=change)
            for wd in sorted(set(need.values())):
                # disc centres on the 2x2 lattice sit up to a quarter cell off the corridor axis, and
                # rotated edges are jagged by up to a quarter cell more: half a cell of slack overall
                st = egress_ok(g.data != FREE, self.res, self.origin, self.twin, wd - self.res / 2)
                out += [f"egress lost: {k} at {wd:.3f} m" for k, v in need.items() if v == wd and not st.get(k)]
        return out
