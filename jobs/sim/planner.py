"""Static planners (numba): 2D Dijkstra, Hybrid-A* over (x, y, heading), shortcut smoothing, gap probes.

Two primitive sets share one search:
  in-place  - forward straight steps + in-place rotation by one heading step (diff-drive / omni)
  lattice   - forward straight + left/right arcs at the robot's turn radius, no reverse (car-like or
              no spin in place)
States keep continuous (x, y); headings stay on the K-heading lattice; the closed set is per
(cell, heading). Node storage is a growable open-addressing hash map, so memory scales with the
states visited, not with grid size x K."""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from .jitcache import njit_cached

from . import params as P

TWO_PI = 2.0 * math.pi


# ---------------------------------------------------------------- heap / hash helpers
@njit_cached
def _heap_push(hf, hi, n, f, idx):
    if n >= hf.shape[0]:
        nf = np.empty(hf.shape[0] * 2, np.float64)
        ni = np.empty(hf.shape[0] * 2, np.int64)
        nf[:n] = hf[:n]
        ni[:n] = hi[:n]
        hf, hi = nf, ni
    hf[n] = f
    hi[n] = idx
    i = n
    n += 1
    while i > 0:
        p = (i - 1) >> 1
        if hf[p] < hf[i] or (hf[p] == hf[i] and hi[p] <= hi[i]):
            break
        hf[p], hf[i] = hf[i], hf[p]
        hi[p], hi[i] = hi[i], hi[p]
        i = p
    return hf, hi, n


@njit_cached
def _heap_pop(hf, hi, n):
    f = hf[0]
    idx = hi[0]
    n -= 1
    if n > 0:
        hf[0] = hf[n]
        hi[0] = hi[n]
        i = 0
        while True:
            l = 2 * i + 1
            if l >= n:
                break
            m = l
            r = l + 1
            if r < n and (hf[r] < hf[l] or (hf[r] == hf[l] and hi[r] < hi[l])):
                m = r
            if hf[i] < hf[m] or (hf[i] == hf[m] and hi[i] <= hi[m]):
                break
            hf[m], hf[i] = hf[i], hf[m]
            hi[m], hi[i] = hi[i], hi[m]
            i = m
    return f, idx, n


@njit_cached
def _hslot(keys, key):
    mask = keys.shape[0] - 1
    h = np.uint64(key) * np.uint64(11400714819323198485)
    i = np.int64(h >> np.uint64(33)) & mask
    while True:
        k = keys[i]
        if k == -1 or k == key:
            return i
        i = (i + 1) & mask


@njit_cached
def _hgrow(keys, vals):
    nk = np.full(keys.shape[0] * 2, -1, np.int64)
    nv = np.empty(keys.shape[0] * 2, np.int64)
    for i in range(keys.shape[0]):
        if keys[i] != -1:
            j = _hslot(nk, keys[i])
            nk[j] = keys[i]
            nv[j] = vals[i]
    return nk, nv


@njit_cached
def _cell(x, y, res, ox, oy, H):
    c = int(math.floor((x - ox) / res))
    r = H - 1 - int(math.floor((y - oy) / res))
    return r, c


@njit_cached
def _kidx(th, dth, K):
    k = int(math.floor(th / dth + 0.5)) % K
    if k < 0:
        k += K
    return k


@njit_cached
def _hit(cs, th, r, c):
    """True when the footprint at heading `th` (any real value) collides at cell (r, c). A heading between two
    lattice headings is checked against both neighbours: the footprint swept between them stays inside their union
    up to R * (1 - cos(dth / 2)) (under 2 mm for a 0.5 m circumradius at K = 36), which the cell-coverage kernels
    absorb. A heading on the lattice is checked against that heading alone."""
    K = cs.shape[0]
    f = th / (2.0 * math.pi / K)
    fl = math.floor(f)
    k0 = int(fl) % K
    if k0 < 0:
        k0 += K
    fr = f - fl
    if fr < 1e-6:
        return cs[k0, r, c] != 0
    k1 = (k0 + 1) % K
    if fr > 1.0 - 1e-6:
        return cs[k1, r, c] != 0
    return cs[k0, r, c] != 0 or cs[k1, r, c] != 0


@njit_cached
def _skey(x, y, res, ox, oy, H, W, sub):
    """Closed-set cell index of a continuous position on a grid `sub` times finer than the map."""
    c = int(math.floor((x - ox) / res * sub))
    r = int(math.floor((y - oy) / res * sub))
    return r * (W * sub) + c


# ---------------------------------------------------------------- 2D Dijkstra
@njit_cached
def dijkstra2d(free, seeds_r, seeds_c, res):
    """Geodesic distance (m) over 8-connected free cells from the seed cells. inf = unreachable."""
    H, W = free.shape
    dist = np.full((H, W), np.inf, np.float64)
    hf = np.empty(4096, np.float64)
    hi = np.empty(4096, np.int64)
    n = 0
    for i in range(seeds_r.shape[0]):
        r, c = seeds_r[i], seeds_c[i]
        if 0 <= r < H and 0 <= c < W and free[r, c] and dist[r, c] > 0:
            dist[r, c] = 0.0
            hf, hi, n = _heap_push(hf, hi, n, 0.0, r * W + c)
    s2 = math.sqrt(2.0) * res
    while n > 0:
        f, idx, n = _heap_pop(hf, hi, n)
        r = idx // W
        c = idx - r * W
        if f > dist[r, c]:
            continue
        for dr in range(-1, 2):
            for dc in range(-1, 2):
                if dr == 0 and dc == 0:
                    continue
                rr = r + dr
                cc = c + dc
                if rr < 0 or rr >= H or cc < 0 or cc >= W or not free[rr, cc]:
                    continue
                nd = f + (s2 if dr != 0 and dc != 0 else res)
                if nd < dist[rr, cc]:
                    dist[rr, cc] = nd
                    hf, hi, n = _heap_push(hf, hi, n, nd, rr * W + cc)
    return dist


@njit_cached
def dijkstra2d_weighted(free, weight, seeds_r, seeds_c, res):
    """Like dijkstra2d, with a per-cell cost multiplier (edge cost = mean of the two cells)."""
    H, W = free.shape
    dist = np.full((H, W), np.inf, np.float64)
    hf = np.empty(4096, np.float64)
    hi = np.empty(4096, np.int64)
    n = 0
    for i in range(seeds_r.shape[0]):
        r, c = seeds_r[i], seeds_c[i]
        if 0 <= r < H and 0 <= c < W and free[r, c] and dist[r, c] > 0:
            dist[r, c] = 0.0
            hf, hi, n = _heap_push(hf, hi, n, 0.0, r * W + c)
    s2 = math.sqrt(2.0) * res
    while n > 0:
        f, idx, n = _heap_pop(hf, hi, n)
        r = idx // W
        c = idx - r * W
        if f > dist[r, c]:
            continue
        for dr in range(-1, 2):
            for dc in range(-1, 2):
                if dr == 0 and dc == 0:
                    continue
                rr = r + dr
                cc = c + dc
                if rr < 0 or rr >= H or cc < 0 or cc >= W or not free[rr, cc]:
                    continue
                nd = f + (s2 if dr != 0 and dc != 0 else res) * 0.5 * (weight[r, c] + weight[rr, cc])
                if nd < dist[rr, cc]:
                    dist[rr, cc] = nd
                    hf, hi, n = _heap_push(hf, hi, n, nd, rr * W + cc)
    return dist


# ---------------------------------------------------------------- Hybrid A*
@njit_cached
def _prim_sample(x, y, th0, L, dk, dth, t):
    if dk == 0:
        return x + t * math.cos(th0), y + t * math.sin(th0), th0
    kap = dk * dth / L
    th = th0 + kap * t
    return x + (math.sin(th) - math.sin(th0)) / kap, y - (math.cos(th) - math.cos(th0)) / kap, th


@njit_cached
def hybrid_astar(cs, margin, center, h2d, res, ox, oy, sx, sy, sk, gx, gy, goal_tol,
                 pkind, plen, pdk, pdir, v_nom, w_max, gap_ref, gap_w, rot_pen, hweight, max_nodes, rev_cost, rev_radius,
                 sub):
    """Returns (status, xs, ys, ks, prims, n_nodes). status 0 found, 1 exhausted, 2 node limit.
    The closed set holds one state per (sub-cell, heading); sub > 1 keeps more distinct positions per map cell,
    which a robot needs to line up for a pinch it can pass only on one heading."""
    K, H, W = cs.shape
    dth = 2.0 * math.pi / K
    cap = 1 << 14
    nf = np.empty((cap, 4), np.float64)   # x, y, g, f
    ni = np.empty((cap, 4), np.int64)     # k, parent, prim, closed
    hkeys = np.full(1 << 15, -1, np.int64)
    hvals = np.empty(1 << 15, np.int64)
    hf = np.empty(4096, np.float64)
    hi = np.empty(4096, np.int64)
    nh = 0
    nn = 0
    empty = np.empty(0, np.float64)
    emptyi = np.empty(0, np.int64)

    r0, c0 = _cell(sx, sy, res, ox, oy, H)
    if r0 < 0 or r0 >= H or c0 < 0 or c0 >= W or not np.isfinite(h2d[r0, c0]):
        return 1, empty, empty, emptyi, emptyi, 0
    key0 = _skey(sx, sy, res, ox, oy, H, W, sub) * K + sk
    nf[0, 0] = sx
    nf[0, 1] = sy
    nf[0, 2] = 0.0
    nf[0, 3] = hweight * h2d[r0, c0] / v_nom
    ni[0, 0] = sk
    ni[0, 1] = -1
    ni[0, 2] = -1
    ni[0, 3] = 0
    nn = 1
    s = _hslot(hkeys, key0)
    hkeys[s] = key0
    hvals[s] = 0
    hf, hi, nh = _heap_push(hf, hi, nh, nf[0, 3], 0)
    goal_idx = -1
    status = 1
    while nh > 0:
        f, idx, nh = _heap_pop(hf, hi, nh)
        if ni[idx, 3] == 1 or f > nf[idx, 3] + 1e-9:
            continue
        ni[idx, 3] = 1
        x = nf[idx, 0]
        y = nf[idx, 1]
        k = ni[idx, 0]
        g = nf[idx, 2]
        if math.hypot(x - gx, y - gy) <= goal_tol:
            goal_idx = idx
            status = 0
            break
        if nn >= max_nodes:
            status = 2
            break
        rc_r, rc_c = _cell(x, y, res, ox, oy, H)
        cur_key = _skey(x, y, res, ox, oy, H, W, sub) * K + k
        th0 = k * dth
        for p in range(pkind.shape[0]):
            dk = pdk[p]
            if pkind[p] == 1:
                k2 = (k + dk) % K
                if cs[k2, rc_r, rc_c]:
                    continue
                x2 = x
                y2 = y
                r2 = rc_r
                c2 = rc_c
                cost = dth / w_max + rot_pen
            else:
                if pdir[p] < 0 and math.hypot(x - sx, y - sy) > rev_radius:
                    continue  # backing up is an undocking move, allowed near the start only
                L = plen[p]
                ns = int(math.ceil(L / (0.4 * res)))
                ok = True
                pen = 0.0
                x2 = x
                y2 = y
                r2 = rc_r
                c2 = rc_c
                for i in range(1, ns + 1):
                    xi, yi, thi = _prim_sample(x, y, th0, L, dk, dth, pdir[p] * L * i / ns)
                    ki = _kidx(thi, dth, K)
                    ri, ci = _cell(xi, yi, res, ox, oy, H)
                    if ri < 0 or ri >= H or ci < 0 or ci >= W or center[ri, ci] or _hit(cs, thi, ri, ci):
                        ok = False
                        break
                    m = margin[ki, ri, ci]
                    if m < gap_ref:
                        pen += (gap_ref - m) / gap_ref
                    x2 = xi
                    y2 = yi
                    r2 = ri
                    c2 = ci
                if not ok:
                    continue
                k2 = (k + dk) % K
                cost = L / v_nom * (1.0 + gap_w * pen / ns) * (1.0 if pdir[p] > 0 else rev_cost)
            if not np.isfinite(h2d[r2, c2]):
                continue
            key = _skey(x2, y2, res, ox, oy, H, W, sub) * K + k2
            if key == cur_key:
                continue
            g2 = g + cost
            f2 = g2 + hweight * h2d[r2, c2] / v_nom
            s = _hslot(hkeys, key)
            if hkeys[s] == key:
                j = hvals[s]
                if ni[j, 3] == 1 or g2 >= nf[j, 2] - 1e-12:
                    continue
            else:
                if nn >= cap:
                    cap *= 2
                    nf2 = np.empty((cap, 4), np.float64)
                    ni2 = np.empty((cap, 4), np.int64)
                    nf2[:nn] = nf[:nn]
                    ni2[:nn] = ni[:nn]
                    nf, ni = nf2, ni2
                j = nn
                nn += 1
                hkeys[s] = key
                hvals[s] = j
                ni[j, 3] = 0
                if nn * 2 > hkeys.shape[0]:
                    hkeys, hvals = _hgrow(hkeys, hvals)
            nf[j, 0] = x2
            nf[j, 1] = y2
            nf[j, 2] = g2
            nf[j, 3] = f2
            ni[j, 0] = k2
            ni[j, 1] = idx
            ni[j, 2] = p
            hf, hi, nh = _heap_push(hf, hi, nh, f2, j)
    if goal_idx < 0:
        return status, empty, empty, emptyi, emptyi, nn
    cnt = 0
    j = goal_idx
    while j >= 0:
        cnt += 1
        j = ni[j, 1]
    xs = np.empty(cnt, np.float64)
    ys = np.empty(cnt, np.float64)
    ks = np.empty(cnt, np.int64)
    ps = np.empty(cnt, np.int64)
    j = goal_idx
    i = cnt - 1
    while j >= 0:
        xs[i] = nf[j, 0]
        ys[i] = nf[j, 1]
        ks[i] = ni[j, 0]
        ps[i] = ni[j, 2]
        j = ni[j, 1]
        i -= 1
    return 0, xs, ys, ks, ps, nn


@njit_cached
def cspace_connected(cs, center, sr, sc, sk, gr, gc):
    """Whether the start state reaches any free heading at the goal cell through free (x, y, heading) cells,
    26-connected with the heading axis wrapping. This over-approximates what the robot can drive, so False
    proves that no path exists; True leaves it to the search."""
    K, H, W = cs.shape
    if cs[sk, sr, sc] or center[sr, sc]:
        return False
    seen = np.zeros((K, H, W), np.uint8)
    qk = np.empty(1024, np.int64)
    qr = np.empty(1024, np.int64)
    qc = np.empty(1024, np.int64)
    head = 0
    tail = 0
    seen[sk, sr, sc] = 1
    qk[0] = sk
    qr[0] = sr
    qc[0] = sc
    tail = 1
    while head < tail:
        k = qk[head]
        r = qr[head]
        c = qc[head]
        head += 1
        if r == gr and c == gc:
            return True
        for dk in range(-1, 2):
            k2 = (k + dk) % K
            for dr in range(-1, 2):
                r2 = r + dr
                if r2 < 0 or r2 >= H:
                    continue
                for dc in range(-1, 2):
                    c2 = c + dc
                    if c2 < 0 or c2 >= W or seen[k2, r2, c2] or cs[k2, r2, c2] or center[r2, c2]:
                        continue
                    seen[k2, r2, c2] = 1
                    if tail >= qk.shape[0]:
                        n2 = qk.shape[0] * 2
                        a = np.empty(n2, np.int64)
                        b = np.empty(n2, np.int64)
                        d = np.empty(n2, np.int64)
                        a[:tail] = qk[:tail]
                        b[:tail] = qr[:tail]
                        d[:tail] = qc[:tail]
                        qk, qr, qc = a, b, d
                    qk[tail] = k2
                    qr[tail] = r2
                    qc[tail] = c2
                    tail += 1
    return False


# ---------------------------------------------------------------- smoothing (in-place robots)
@njit_cached
def _segment_ok(cs, margin, center, res, ox, oy, x0, y0, x1, y1, min_m, off):
    K, H, W = cs.shape
    dth = 2.0 * math.pi / K
    L = math.hypot(x1 - x0, y1 - y0)
    if L < 1e-9:
        return True
    phi = math.atan2(y1 - y0, x1 - x0) + off
    k = _kidx(phi, dth, K)
    n = int(math.ceil(L / (0.4 * res)))
    for i in range(n + 1):
        t = i / n
        r, c = _cell(x0 + t * (x1 - x0), y0 + t * (y1 - y0), res, ox, oy, H)
        if r < 0 or r >= H or c < 0 or c >= W or center[r, c] or _hit(cs, phi, r, c) or margin[k, r, c] < min_m:
            return False
    return True


@njit_cached
def _rotation_ok(cs, r, c, th_from, th_to):
    """In-place rotation along the short way from th_from to th_to: every lattice heading swept, and the lattice
    headings bracketing both ends, must be free."""
    K = cs.shape[0]
    dth = 2.0 * math.pi / K
    d = (th_to - th_from + math.pi) % (2.0 * math.pi) - math.pi
    if _hit(cs, th_from, r, c) or _hit(cs, th_from + d, r, c):
        return False
    a = min(th_from, th_from + d) / dth
    b = max(th_from, th_from + d) / dth
    for j in range(int(math.floor(a)), int(math.ceil(b)) + 1):
        kk = j % K
        if kk < 0:
            kk += K
        if (j >= a - 1e-9) and (j <= b + 1e-9) and cs[kk, r, c]:
            return False
    return True


@njit_cached
def smooth_path(cs, margin, center, res, ox, oy, xs, ys, node_m, th0, req_cells, max_fail, rev):
    """Greedy shortcut: from each vertex jump to the farthest later node reachable by a straight,
    collision-free segment whose gap is not smaller than the lattice path's (capped at req_cells)."""
    K, H, W = cs.shape
    n = xs.shape[0]
    out = np.empty(n, np.int64)
    m = 0
    out[m] = 0
    m += 1
    i = 0
    th_prev = th0
    off = math.pi if rev else 0.0
    while i < n - 1:
        best = i + 1
        best_phi = math.atan2(ys[i + 1] - ys[i], xs[i + 1] - xs[i]) + off
        r, c = _cell(xs[i], ys[i], res, ox, oy, H)
        minm = 1e9
        fails = 0
        for j in range(i + 1, n):
            if node_m[j] < minm:
                minm = node_m[j]
            req = min(req_cells, minm) - 1.0
            phi = math.atan2(ys[j] - ys[i], xs[j] - xs[i]) + off
            if _segment_ok(cs, margin, center, res, ox, oy, xs[i], ys[i], xs[j], ys[j], req, off) and \
                    _rotation_ok(cs, r, c, th_prev, phi):
                best = j
                best_phi = phi
                fails = 0
            else:
                fails += 1
                if fails > max_fail:
                    break
        out[m] = best
        m += 1
        th_prev = best_phi
        i = best
    return out[:m]


@njit_cached
def rail_hits(cs, center, res, ox, oy, xs, ys, ths):
    """Index of the first rail sample whose footprint collides (or whose center enters a `center` cell); -1 when
    the rail is clear. Consecutive samples differ by at most one lattice heading step, so checking each sample
    against its bracketing headings covers the turns between them."""
    K, H, W = cs.shape
    dth = 2.0 * math.pi / K
    for i in range(xs.shape[0]):
        if i > 0:
            # fill long steps so no cell between two samples is skipped
            L = math.hypot(xs[i] - xs[i - 1], ys[i] - ys[i - 1])
            dt_ = abs(ths[i] - ths[i - 1])
            n = max(int(math.ceil(L / (0.4 * res))), int(math.ceil(dt_ / (0.5 * dth))), 1)
        else:
            n = 1
        for j in range(1, n + 1):
            if i == 0:
                x, y, th = xs[0], ys[0], ths[0]
            else:
                t = j / n
                x = xs[i - 1] + t * (xs[i] - xs[i - 1])
                y = ys[i - 1] + t * (ys[i] - ys[i - 1])
                th = ths[i - 1] + t * (ths[i] - ths[i - 1])
            r, c = _cell(x, y, res, ox, oy, H)
            if r < 0 or r >= H or c < 0 or c >= W or _hit(cs, th, r, c):
                return i
            if i > 0 and center[r, c]:
                return i
    return -1


# ---------------------------------------------------------------- gap probes
@njit_cached
def ray_free(blocked, res, ox, oy, x, y, dx, dy, max_d):
    """Free distance from (x, y) along unit (dx, dy) until a blocked cell (step res/4)."""
    H, W = blocked.shape
    step = 0.25 * res
    t = 0.0
    while t < max_d:
        r, c = _cell(x + t * dx, y + t * dy, res, ox, oy, H)
        if r < 0 or r >= H or c < 0 or c >= W or blocked[r, c]:
            if t == 0.0:
                return 0.0
            lo = t - step
            hi = t
            for _ in range(8):
                mid = 0.5 * (lo + hi)
                r, c = _cell(x + mid * dx, y + mid * dy, res, ox, oy, H)
                if r < 0 or r >= H or c < 0 or c >= W or blocked[r, c]:
                    hi = mid
                else:
                    lo = mid
            return hi
        t += step
    return max_d


@njit_cached
def free_width(blocked, res, ox, oy, x, y, heading, offsets, max_d):
    """Free width perpendicular to `heading`, measured through points at longitudinal offsets
    from (x, y); returns the smallest width over the offsets."""
    ch, sh = math.cos(heading), math.sin(heading)
    nx, ny = -sh, ch
    best = 1e9
    for o in offsets:
        px = x + o * ch
        py = y + o * sh
        wl = ray_free(blocked, res, ox, oy, px, py, nx, ny, max_d)
        wr = ray_free(blocked, res, ox, oy, px, py, -nx, -ny, max_d)
        if wl + wr < best:
            best = wl + wr
    return best


@njit_cached
def pass_width(blocked, res, ox, oy, x, y, heading, offsets, max_d, lon_lo, lon_hi, max_tilt, n_tilt):
    """Free width of the passage the robot is in: through each probe point (longitudinal `offsets` from (x, y)),
    the shortest chord between obstacles over directions tilted up to `max_tilt` from the perpendicular of
    `heading`. A tilted chord counts only when both of its ends lie beside the footprint (between `lon_lo` and
    `lon_hi` from the center along the heading), so a door crossed at an angle reads its true width while a wall ahead does not
    read as a pinch."""
    ch, sh = math.cos(heading), math.sin(heading)
    best = 1e9
    for o in offsets:
        px = x + o * ch
        py = y + o * sh
        for j in range(-n_tilt, n_tilt + 1):
            d = max_tilt * j / n_tilt if n_tilt > 0 else 0.0
            phi = heading + 0.5 * math.pi + d
            ux, uy = math.cos(phi), math.sin(phi)
            wl = ray_free(blocked, res, ox, oy, px, py, ux, uy, max_d)
            wr = ray_free(blocked, res, ox, oy, px, py, -ux, -uy, max_d)
            if j != 0:
                sd = math.sin(d)
                a = o - wl * sd
                b = o + wr * sd
                if a < lon_lo or a > lon_hi or b < lon_lo or b > lon_hi:
                    continue
            if wl + wr < best:
                best = wl + wr
    return best


# ---------------------------------------------------------------- python-side wrappers
@dataclass
class PrimitiveSet:
    kind: np.ndarray
    length: np.ndarray
    dk: np.ndarray
    step: float
    goal_tol: float
    in_place: bool
    dir: np.ndarray


def primitive_set(in_place: bool, turn_radius: float, res: float, K: int = P.K_HEADINGS) -> PrimitiveSet:
    dth = TWO_PI / K
    if in_place:
        step = max(1.5 * res, 0.03)
        return PrimitiveSet(np.array([0, 0, 1, 1]), np.array([step, step, 0.0, 0.0]), np.array([0, 0, 1, -1]),
                            step, max(1.5 * res, 0.03), True, np.array([1, -1, 1, 1]))
    L = max(turn_radius * dth, 1.5 * res)
    return PrimitiveSet(np.array([0, 0, 0]), np.array([L, L, L]), np.array([0, 1, -1]), L,
                        max(0.6 * L, 1.5 * res, 0.04), False, np.array([1, 1, 1]))


def prim_samples(x, y, k, L, dk, K, res):
    """Intermediate poses of one translation primitive (excluding the start)."""
    dth = TWO_PI / K
    ns = max(1, int(math.ceil(L / (0.5 * res))))
    out = []
    for i in range(1, ns + 1):
        out.append(_prim_sample(x, y, k * dth, L, dk, dth, L * i / ns))
    return out
