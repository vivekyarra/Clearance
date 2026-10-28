"""Local control: path-constrained dynamic window.

The robot tracks the planned rail (which is collision-free against the static grid by construction).
Each step samples speeds inside the dynamic window [u - brake*dt, u + accel*dt] and keeps the fastest
admissible one: driving one step at that speed and then braking must not bring the footprint within
r_ped + margin of any pedestrian disc predicted at constant velocity over the horizon."""
from __future__ import annotations

import math

import numpy as np
from .jitcache import njit_cached

from .peds import poly_sdist


@njit_cached
def rail_pose(rs, rx, ry, rth, s):
    n = rs.shape[0]
    if s <= rs[0]:
        return rx[0], ry[0], rth[0], 1
    if s >= rs[n - 1]:
        return rx[n - 1], ry[n - 1], rth[n - 1], n - 1
    lo = 0
    hi = n - 1
    while hi - lo > 1:
        mid = (lo + hi) >> 1
        if rs[mid] <= s:
            lo = mid
        else:
            hi = mid
    t = (s - rs[lo]) / (rs[hi] - rs[lo])
    return rx[lo] + t * (rx[hi] - rx[lo]), ry[lo] + t * (ry[hi] - ry[lo]), rth[lo] + t * (rth[hi] - rth[lo]), hi


@njit_cached
def _place(fp, x, y, th, out):
    c, s = math.cos(th), math.sin(th)
    for i in range(fp.shape[0]):
        out[i, 0] = x + c * fp[i, 0] - s * fp[i, 1]
        out[i, 1] = y + s * fp[i, 0] + c * fp[i, 1]


@njit_cached
def _admissible(rs, rx, ry, rth, s, uc, fp, px, py, pvx, pvy, alive, lim, horizon, dt, brake, buf):
    t = 0.0
    v = uc
    sc = s
    first = True
    s_end = rs[rs.shape[0] - 1]
    while t < horizon - 1e-9:
        if first:
            first = False
        else:
            v = max(0.0, v - brake * dt)
        sc = min(sc + v * dt, s_end)
        t += dt
        x, y, th, _ = rail_pose(rs, rx, ry, rth, sc)
        _place(fp, x, y, th, buf)
        for i in range(px.shape[0]):
            if not alive[i]:
                continue
            d, _, _, _, _ = poly_sdist(buf, px[i] + pvx[i] * t, py[i] + pvy[i] * t)
            if d < lim:
                return False
        if v <= 0.0:
            break
    return True


@njit_cached
def choose_speed(rs, rx, ry, rth, s, u_lo, u_hi, fp, px, py, pvx, pvy, alive, lim, horizon, dt, brake, n_cand):
    buf = np.empty((fp.shape[0], 2))
    if u_hi <= u_lo:
        return u_lo
    for ci in range(n_cand):
        uc = u_hi - (u_hi - u_lo) * ci / (n_cand - 1)
        if uc <= 1e-9:
            break
        if _admissible(rs, rx, ry, rth, s, uc, fp, px, py, pvx, pvy, alive, lim, horizon, dt, brake, buf):
            return uc
    return u_lo
