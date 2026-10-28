"""3D solids for reference rooms: footprint tests, 2D ray casts (analytic ground-truth distances)
and 3D ray casts (rendering). Boxes are yaw-rotated about Z; cylinders are vertical."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np


@dataclass
class Solid:
    id: str
    kind: str  # "box" | "cyl"
    label: str
    cx: float
    cy: float
    l: float  # box: length along yaw; cyl: diameter
    w: float  # box: width; cyl: diameter
    z0: float
    z1: float
    yaw: float = 0.0
    movable: bool = False
    fragile: bool = False
    primitive: bool = True  # exported as a Twin primitive (walls / lips are not)

    def in_band(self, lo: float, hi: float) -> bool:
        return self.z1 > lo + 1e-9 and self.z0 < hi

    def contains_xy(self, x, y) -> np.ndarray:
        x = np.asarray(x, float) - self.cx
        y = np.asarray(y, float) - self.cy
        if self.kind == "cyl":
            return x * x + y * y <= (self.l / 2) ** 2
        c, s = np.cos(self.yaw), np.sin(self.yaw)
        lx = c * x + s * y
        ly = -s * x + c * y
        return (np.abs(lx) <= self.l / 2) & (np.abs(ly) <= self.w / 2)

    # ---- 2D ray (origin p, unit dir d) -> entry distance t >= 0 or inf ----
    def ray2d(self, p, d) -> float:
        p = np.asarray(p, float)
        d = np.asarray(d, float)
        o = p - (self.cx, self.cy)
        if self.kind == "cyl":
            r = self.l / 2
            b = o @ d
            c = o @ o - r * r
            disc = b * b - c
            if disc < 0:
                return np.inf
            t0 = -b - np.sqrt(disc)
            t1 = -b + np.sqrt(disc)
            if t1 < 0:
                return np.inf
            return max(t0, 0.0)
        c, s = np.cos(self.yaw), np.sin(self.yaw)
        R = np.array([[c, s], [-s, c]])
        ol, dl = R @ o, R @ d
        half = np.array([self.l / 2, self.w / 2])
        tmin, tmax = -np.inf, np.inf
        for k in range(2):
            if abs(dl[k]) < 1e-12:
                if abs(ol[k]) > half[k]:
                    return np.inf
                continue
            ta = (-half[k] - ol[k]) / dl[k]
            tb = (half[k] - ol[k]) / dl[k]
            tmin = max(tmin, min(ta, tb))
            tmax = min(tmax, max(ta, tb))
        if tmax < max(tmin, 0.0):
            return np.inf
        return max(tmin, 0.0)


def raycast_solids(origin: np.ndarray, dirs: np.ndarray, solids: list[Solid], floor: bool = True,
                   ceiling_z: float | None = None, ceiling_box=None, max_t: float = 30.0):
    """Vectorized 3D ray cast. origin [3], dirs [N,3] (any length; t is in units of |dir|).
    Returns t [N] (inf = no hit) and hit id [N] (-1 none, -2 floor, -3 ceiling, else solid index)."""
    n = dirs.shape[0]
    best = np.full(n, np.inf)
    hid = np.full(n, -1, dtype=np.int32)
    ox, oy, oz = origin
    dx, dy, dz = dirs[:, 0], dirs[:, 1], dirs[:, 2]
    with np.errstate(divide="ignore", invalid="ignore"):
        if floor:
            t = np.where(dz < -1e-9, -oz / dz, np.inf)
            m = (t > 0) & (t < best)
            best[m], hid[m] = t[m], -2
        if ceiling_z is not None:
            t = np.where(dz > 1e-9, (ceiling_z - oz) / dz, np.inf)
            if ceiling_box is not None:
                x0, y0, x1, y1 = ceiling_box
                hx, hy = ox + t * dx, oy + t * dy
                t = np.where((hx >= x0) & (hx <= x1) & (hy >= y0) & (hy <= y1), t, np.inf)
            m = (t > 0) & (t < best)
            best[m], hid[m] = t[m], -3
        for i, s in enumerate(solids):
            if s.kind == "box":
                c, sn = np.cos(s.yaw), np.sin(s.yaw)
                lox = c * (ox - s.cx) + sn * (oy - s.cy)
                loy = -sn * (ox - s.cx) + c * (oy - s.cy)
                ldx = c * dx + sn * dy
                ldy = -sn * dx + c * dy
                tmin = np.full(n, -np.inf)
                tmax = np.full(n, np.inf)
                for o_, d_, h_lo, h_hi in ((lox, ldx, -s.l / 2, s.l / 2), (loy, ldy, -s.w / 2, s.w / 2),
                                           (oz, dz, s.z0, s.z1)):
                    if np.isscalar(o_) or np.ndim(o_) == 0:
                        o_ = float(o_)
                    ta = (h_lo - o_) / d_
                    tb = (h_hi - o_) / d_
                    par = np.abs(d_) < 1e-12
                    inside = (o_ >= h_lo) & (o_ <= h_hi)
                    lo_ = np.where(par, np.where(inside, -np.inf, np.inf), np.minimum(ta, tb))
                    hi_ = np.where(par, np.where(inside, np.inf, -np.inf), np.maximum(ta, tb))
                    tmin = np.maximum(tmin, lo_)
                    tmax = np.minimum(tmax, hi_)
                t = np.where((tmax >= tmin) & (tmax > 0), np.maximum(tmin, 0), np.inf)
            else:
                r = s.l / 2
                px, py = ox - s.cx, oy - s.cy
                a = dx * dx + dy * dy
                b = px * dx + py * dy
                cc = px * px + py * py - r * r
                disc = b * b - a * cc
                sq = np.sqrt(np.maximum(disc, 0))
                t_side = np.where((disc >= 0) & (a > 1e-12), (-b - sq) / a, np.inf)
                z_side = oz + t_side * dz
                t_side = np.where((t_side > 0) & (z_side >= s.z0) & (z_side <= s.z1), t_side, np.inf)
                # caps
                tcap = np.full(n, np.inf)
                for zc in (s.z0, s.z1):
                    tc = (zc - oz) / dz
                    hx, hy = px + tc * dx, py + tc * dy
                    okc = (tc > 0) & (hx * hx + hy * hy <= r * r)
                    tcap = np.where(okc & (tc < tcap), tc, tcap)
                t = np.minimum(t_side, tcap)
            m = t < best
            best[m], hid[m] = t[m], i
    best[best > max_t] = np.inf
    hid[~np.isfinite(best)] = -1
    return best, hid


def free_gap_2d(solids: list[Solid], mid_xy, dir_xy, band=(0.03, 2.0)):
    """Exact free width through mid_xy along dir_xy between the first band solids on each side.
    Returns (a_xy, b_xy, width)."""
    d = np.asarray(dir_xy, float)
    d = d / np.linalg.norm(d)
    p = np.asarray(mid_xy, float)
    for s in solids:
        if s.in_band(*band) and s.contains_xy(*p):
            raise ValueError(f"measurement midpoint {tuple(p)} inside solid {s.id}")
    tf = min((s.ray2d(p, d) for s in solids if s.in_band(*band)), default=np.inf)
    tb = min((s.ray2d(p, -d) for s in solids if s.in_band(*band)), default=np.inf)
    if not (np.isfinite(tf) and np.isfinite(tb)):
        raise ValueError(f"measurement through {tuple(p)} is not bounded on both sides")
    a = p - tb * d
    b = p + tf * d
    return (float(a[0]), float(a[1])), (float(b[0]), float(b[1])), float(tf + tb)
