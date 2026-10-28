"""Tape-style reference distances on an occupancy grid.

A reference measurement {name, a_xy, b_xy, value_m} spans a free gap between two obstacle faces.
On the grid we sample the line through a and b, find the contiguous free run that contains the
segment midpoint, and locate both ends with sub-cell interpolation (bilinear "free" field,
0.5 crossing). Unknown counts as not free, like the simulator.
"""
from __future__ import annotations

import numpy as np

from clearance_core import grid as cgrid
from clearance_core.grid import FREE, OCC, UNKNOWN, Grid
from clearance_core.schemas import Twin

STEP_M = 0.002


def _free_field(g: Grid, x, y):
    """Bilinear interpolation of (cell == FREE) at world points."""
    h, w = g.data.shape
    fc = (np.asarray(x) - g.origin[0]) / g.res - 0.5         # fractional column of cell centers
    fr = (h - 1) - ((np.asarray(y) - g.origin[1]) / g.res - 0.5)
    c0 = np.floor(fc).astype(int)
    r0 = np.floor(fr).astype(int)
    tc, tr = fc - c0, fr - r0
    free = (g.data == FREE).astype(np.float32)

    def at(r, c):
        ok = (r >= 0) & (r < h) & (c >= 0) & (c < w)
        out = np.zeros(r.shape, np.float32)
        out[ok] = free[r[ok], c[ok]]
        return out

    return ((1 - tr) * ((1 - tc) * at(r0, c0) + tc * at(r0, c0 + 1))
            + tr * ((1 - tc) * at(r0 + 1, c0) + tc * at(r0 + 1, c0 + 1)))


def _state_at(g: Grid, p) -> str:
    r, c = g.world_to_cell(p[0], p[1])
    h, w = g.data.shape
    if not (0 <= r < h and 0 <= c < w):
        return "outside"
    v = int(g.data[r, c])
    return {FREE: "free", OCC: "occupied", UNKNOWN: "unknown"}.get(v, "unknown")


def measure_gap(g: Grid, a_xy, b_xy, extend_m: float | None = None) -> dict:
    """Free width along line a->b through its midpoint. Returns {value_m, a_xy, b_xy, ok, limited_by}.
    value_m = 0 and ok = False when the midpoint itself is not free. limited_by names what ends the free
    run on each side ("occupied", "unknown", "outside"); a gap ended by "unknown" (floor the scan never
    saw, e.g. hidden behind a chair) is a lower bound: rescan that spot to measure it."""
    a = np.asarray(a_xy, float)
    b = np.asarray(b_xy, float)
    L = float(np.linalg.norm(b - a))
    if L < 1e-9:
        raise ValueError("degenerate measurement segment")
    d = (b - a) / L
    mid = (a + b) / 2
    ext = extend_m if extend_m is not None else max(0.5, 0.5 * L)
    half = L / 2 + ext
    ts = np.arange(-half, half + STEP_M / 2, STEP_M)
    pts = mid[None, :] + ts[:, None] * d[None, :]
    f = _free_field(g, pts[:, 0], pts[:, 1])
    i0 = int(np.argmin(np.abs(ts)))
    if f[i0] < 0.5:
        st = _state_at(g, mid)
        return {"value_m": 0.0, "a_xy": tuple(mid), "b_xy": tuple(mid), "ok": False, "limited_by": (st, st)}
    hi = i0
    while hi + 1 < len(ts) and f[hi + 1] >= 0.5:
        hi += 1
    lo = i0
    while lo - 1 >= 0 and f[lo - 1] >= 0.5:
        lo -= 1
    open_end = lo == 0 or hi == len(ts) - 1

    def cross(i_in, i_out):
        if i_out < 0 or i_out >= len(ts):
            return ts[i_in]
        fi, fo = f[i_in], f[i_out]
        u = (fi - 0.5) / max(fi - fo, 1e-9)
        return ts[i_in] + u * (ts[i_out] - ts[i_in])

    t_lo, t_hi = cross(lo, lo - 1), cross(hi, hi + 1)
    pa, pb = mid + t_lo * d, mid + t_hi * d
    beyond = 0.5 * g.res
    lim = (_state_at(g, mid + (t_lo - beyond) * d) if lo > 0 else "open",
           _state_at(g, mid + (t_hi + beyond) * d) if hi < len(ts) - 1 else "open")
    return {"value_m": float(t_hi - t_lo), "a_xy": (float(pa[0]), float(pa[1])),
            "b_xy": (float(pb[0]), float(pb[1])), "ok": not open_end, "limited_by": lim}


def evaluate_grid(g: Grid, gt_measurements: list[dict]) -> dict:
    rows = []
    for m in gt_measurements:
        r = measure_gap(g, m["a_xy"], m["b_xy"])
        rows.append({"name": m["name"], "gt_m": m["value_m"], "measured_m": round(r["value_m"], 4),
                     "err_m": round(r["value_m"] - m["value_m"], 4), "ok": r["ok"],
                     "limited_by_unknown": "unknown" in r["limited_by"]})
    errs = np.abs([r["err_m"] for r in rows]) if rows else np.array([np.nan])
    return {"median_abs_err_m": round(float(np.median(errs)), 4),
            "p90_abs_err_m": round(float(np.percentile(errs, 90)), 4),
            "max_abs_err_m": round(float(np.max(errs)), 4), "n": len(rows), "rows": rows}


def evaluate(twin: Twin, gt_measurements: list[dict], grid: Grid | None = None) -> dict:
    """Median / p90 absolute error of the twin's occupancy grid against reference distances."""
    return evaluate_grid(grid if grid is not None else cgrid.load(twin.occupancy), gt_measurements)


def aisle_passable(g: Grid, p0, p1, width_m: float, tol_m: float = 0.05, n_stations: int = 9,
                   end_margin_m: float = 0.3) -> dict:
    """Check an aisle given by its centerline p0 -> p1 and nominal width: the centerline is free end to
    end and the free width measured perpendicular to it at every station is within tol of width_m."""
    p0, p1 = np.asarray(p0, float), np.asarray(p1, float)
    L = float(np.linalg.norm(p1 - p0))
    d = (p1 - p0) / L
    nrm = np.array([-d[1], d[0]])
    widths = []
    for s in np.linspace(end_margin_m, L - end_margin_m, n_stations):
        c = p0 + s * d
        widths.append(measure_gap(g, c - nrm * width_m / 2, c + nrm * width_m / 2)["value_m"])
    ss = np.arange(0.05, L - 0.05, g.res / 2)
    pts = p0[None, :] + ss[:, None] * d[None, :]
    r, c = g.world_to_cell(pts[:, 0], pts[:, 1])
    h, w = g.data.shape
    inside = (r >= 0) & (r < h) & (c >= 0) & (c < w)
    centerline_free = bool(inside.all() and (g.data[r[inside], c[inside]] == FREE).all())
    werr = np.abs(np.array(widths) - width_m)
    return {"centerline_free": centerline_free, "widths_m": [round(v, 4) for v in widths],
            "median_width_m": round(float(np.median(widths)), 4), "max_abs_err_m": round(float(werr.max()), 4),
            "passable": centerline_free and bool(werr.max() <= tol_m)}
