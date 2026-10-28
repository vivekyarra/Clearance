"""Pedestrians: Helbing-style social force with geodesic route fields, spawned from the traffic model.

Each pedestrian is a disc (r = 0.25 m) heading to a goal point in its destination zone. Forces:
driving term toward the route-field direction, anisotropic exponential repulsion from other
pedestrians, exponential repulsion from walls (distance transform) and from the robot footprint.
Walls and the robot are also hard constraints."""
from __future__ import annotations

import hashlib
import math

import cv2
import numpy as np
from .jitcache import njit_cached

from clearance_core.schemas import TrafficModel

from . import params as P
from .geometry import fill_polygons
from .planner import dijkstra2d_weighted


# ---------------------------------------------------------------- numba kernels
@njit_cached
def poly_sdist(poly, px, py):
    """Signed distance from a point to a polygon (negative inside) and the outward unit normal."""
    n = poly.shape[0]
    best = 1e18
    cx = 0.0
    cy = 0.0
    inside = False
    for i in range(n):
        ax, ay = poly[i, 0], poly[i, 1]
        bx, by = poly[(i + 1) % n, 0], poly[(i + 1) % n, 1]
        ex, ey = bx - ax, by - ay
        l2 = ex * ex + ey * ey
        t = 0.0
        if l2 > 0:
            t = ((px - ax) * ex + (py - ay) * ey) / l2
            t = min(1.0, max(0.0, t))
        qx, qy = ax + t * ex, ay + t * ey
        d2 = (px - qx) ** 2 + (py - qy) ** 2
        if d2 < best:
            best = d2
            cx, cy = qx, qy
        if (ay > py) != (by > py):
            xint = ax + (py - ay) * (bx - ax) / (by - ay)
            if px < xint:
                inside = not inside
    d = math.sqrt(best)
    if d < 1e-12:
        return 0.0, 0.0, 0.0, cx, cy
    if inside:
        return -d, (cx - px) / d, (cy - py) / d, cx, cy
    return d, (px - cx) / d, (py - cy) / d, cx, cy


@njit_cached
def _bilinear(a, x, y, res, ox, oy):
    H, W = a.shape
    fc = (x - ox) / res - 0.5
    fr = H - (y - oy) / res - 0.5
    c0 = int(math.floor(fc))
    r0 = int(math.floor(fr))
    tc = fc - c0
    tr = fr - r0
    v = 0.0
    for dr in range(2):
        for dc in range(2):
            r = min(max(r0 + dr, 0), H - 1)
            c = min(max(c0 + dc, 0), W - 1)
            w = (tr if dr else 1 - tr) * (tc if dc else 1 - tc)
            v += w * a[r, c]
    return v


@njit_cached
def _steer_around(x, y, ex, ey, cx, cy, R):
    """Desired direction adjusted to pass tangent to the robot's inflated disc when the straight line
    would hit it within the look-ahead (velocity-obstacle style anticipation)."""
    dx = cx - x
    dy = cy - y
    d = math.hypot(dx, dy)
    if d > 3.0 or d < 1e-9:
        return ex, ey
    along = dx * ex + dy * ey
    if along <= 0.0:
        return ex, ey
    lat = abs(-dx * ey + dy * ex)
    if lat >= R:
        return ex, ey
    phi = math.atan2(dy, dx)
    if d <= R:
        a = math.pi / 2 + 0.3
    else:
        a = math.asin(R / d)
    best_x, best_y, best_dot = ex, ey, -2.0
    for sgn in (1.0, -1.0):
        tx = math.cos(phi + sgn * a)
        ty = math.sin(phi + sgn * a)
        dot = tx * ex + ty * ey + (0.02 if sgn < 0 else 0.0)
        if dot > best_dot:
            best_dot = dot
            best_x, best_y = tx, ty
    return best_x, best_y


@njit_cached
def ped_step(px, py, vx, vy, v0, gx, gy, dz, alive, age, stuck, prog, yld, ctrl, fields, edt, gradx, grady, blocked,
             res, ox, oy, robot_poly, rcx, rcy, rR, dt, nsub, r_ped, tau, A, B, lam, Aw, Bw, Ar, Br, vmax_f,
             goal_r, lifetime, stuck_max):
    """Advance all live pedestrians by dt (in place). Returns uint8 flags: pedestrian overlapped the robot."""
    n = px.shape[0]
    H, W = edt.shape
    over = np.zeros(n, np.uint8)
    h = dt / nsub
    fxs = np.zeros(n)
    fys = np.zeros(n)
    for _ in range(nsub):
        for i in range(n):
            fxs[i] = 0.0
            fys[i] = 0.0
            if not alive[i] or ctrl[i]:
                continue
            dgx = gx[i] - px[i]
            dgy = gy[i] - py[i]
            dg = math.hypot(dgx, dgy)
            ex, ey = 0.0, 0.0
            if dg < 1.0 and dg > 1e-9:
                ex, ey = dgx / dg, dgy / dg
            else:
                c = int(math.floor((px[i] - ox) / res))
                r = H - 1 - int(math.floor((py[i] - oy) / res))
                if 0 <= r < H and 0 <= c < W:
                    ex = fields[dz[i], r, c, 0]
                    ey = fields[dz[i], r, c, 1]
                if ex == 0.0 and ey == 0.0 and dg > 1e-9:
                    ex, ey = dgx / dg, dgy / dg
            ex, ey = _steer_around(px[i], py[i], ex, ey, rcx, rcy, rR)
            # yielding: a walker stalled next to the robot steps back for a moment
            if yld[i] <= 0.0 and stuck[i] > 4.0 and math.hypot(px[i] - rcx, py[i] - rcy) < rR + 1.0:
                yld[i] = 3.0
            if yld[i] > 0.0:
                yld[i] -= h
                ax = px[i] - rcx
                ay = py[i] - rcy
                am = math.hypot(ax, ay)
                if am > 1e-9:
                    ex, ey = ax / am, ay / am
            fx = (v0[i] * ex - vx[i]) / tau
            fy = (v0[i] * ey - vy[i]) / tau
            for j in range(n):
                if j == i or not alive[j]:
                    continue
                dx = px[i] - px[j]
                dy = py[i] - py[j]
                d = math.hypot(dx, dy)
                if d > 3.0 or d < 1e-9:
                    continue
                nx, ny = dx / d, dy / d
                cosphi = -(nx * ex + ny * ey)
                wgt = lam + (1 - lam) * 0.5 * (1 + cosphi)
                mag = A * math.exp((2 * r_ped - d) / B) * wgt
                fx += mag * nx
                fy += mag * ny
                if cosphi > 0.7:  # head-on: sidestep (keep right) so opposing walkers do not lock
                    fx += 0.3 * mag * ny
                    fy -= 0.3 * mag * nx
            dw = _bilinear(edt, px[i], py[i], res, ox, oy)
            if dw < 1.5:
                c = min(max(int(math.floor((px[i] - ox) / res)), 0), W - 1)
                r = min(max(H - 1 - int(math.floor((py[i] - oy) / res)), 0), H - 1)
                mag = Aw * math.exp((r_ped - dw) / Bw)
                fx += mag * gradx[r, c]
                fy += mag * grady[r, c]
            dr_, nrx, nry, _, _ = poly_sdist(robot_poly, px[i], py[i])
            if dr_ < 4.0:
                mag = Ar * math.exp((r_ped - dr_) / Br)
                fx += mag * nrx
                fy += mag * nry

            fxs[i] = fx
            fys[i] = fy
        for i in range(n):
            if not alive[i]:
                continue
            if not ctrl[i]:
                vx[i] += fxs[i] * h
                vy[i] += fys[i] * h
                sp = math.hypot(vx[i], vy[i])
                vm = vmax_f * v0[i]
                if sp > vm:
                    vx[i] *= vm / sp
                    vy[i] *= vm / sp
            nxp = px[i] + vx[i] * h
            nyp = py[i] + vy[i] * h
            c = int(math.floor((nxp - ox) / res))
            r = H - 1 - int(math.floor((nyp - oy) / res))
            if r < 0 or r >= H or c < 0 or c >= W or blocked[r, c]:
                vx[i] = 0.0
                vy[i] = 0.0
                continue
            px[i] = nxp
            py[i] = nyp
            dw = _bilinear(edt, px[i], py[i], res, ox, oy)
            if dw < r_ped:
                gxx = gradx[r, c]
                gyy = grady[r, c]
                px[i] += (r_ped - dw) * gxx
                py[i] += (r_ped - dw) * gyy
                vn = vx[i] * gxx + vy[i] * gyy
                if vn < 0:
                    vx[i] -= vn * gxx
                    vy[i] -= vn * gyy
            d, nrx, nry, cx, cy = poly_sdist(robot_poly, px[i], py[i])
            if d < r_ped:
                over[i] = 1
                if nrx != 0.0 or nry != 0.0:
                    px[i] = cx + nrx * r_ped
                    py[i] = cy + nry * r_ped
                    vn = vx[i] * nrx + vy[i] * nry
                    if vn < 0:
                        vx[i] -= vn * nrx
                        vy[i] -= vn * nry
            age[i] += h
            dgoal = math.hypot(gx[i] - px[i], gy[i] - py[i])
            if dgoal < prog[i] - 0.5:
                prog[i] = dgoal
                stuck[i] = 0.0
            else:
                stuck[i] += h
            if not ctrl[i] and (dgoal < goal_r or age[i] > lifetime or stuck[i] > stuck_max):
                alive[i] = 0
    return over


@njit_cached
def route_field(dist):
    """Unit direction field (world x, y) descending a geodesic distance map."""
    H, W = dist.shape
    out = np.zeros((H, W, 2), np.float32)
    for r in range(H):
        for c in range(W):
            d0 = dist[r, c]
            if not np.isfinite(d0) or d0 == 0.0:
                continue
            sx = 0.0
            sy = 0.0
            for dr in range(-1, 2):
                for dc in range(-1, 2):
                    if dr == 0 and dc == 0:
                        continue
                    rr = r + dr
                    cc = c + dc
                    if rr < 0 or rr >= H or cc < 0 or cc >= W:
                        continue
                    d1 = dist[rr, cc]
                    if not np.isfinite(d1) or d1 >= d0:
                        continue
                    L = math.sqrt(dr * dr + dc * dc)
                    w = (d0 - d1) / L
                    sx += w * dc / L
                    sy += w * (-dr) / L
            m = math.hypot(sx, sy)
            if m > 0:
                out[r, c, 0] = sx / m
                out[r, c, 1] = sy / m
    return out


@njit_cached
def robot_ped_relations(robot_poly, x, y, th, px, py, alive, r_ped):
    """Edge-to-edge distance robot<->pedestrian and whether the pedestrian is ahead of the robot."""
    n = px.shape[0]
    d = np.full(n, 1e9)
    ahead = np.zeros(n, np.uint8)
    c, s = math.cos(th), math.sin(th)
    for i in range(n):
        if not alive[i]:
            continue
        dd, _, _, _, _ = poly_sdist(robot_poly, px[i], py[i])
        d[i] = dd - r_ped
        if (px[i] - x) * c + (py[i] - y) * s > 0:
            ahead[i] = 1
    return d, ahead


# ---------------------------------------------------------------- crowd (python orchestration)
class Crowd:
    def __init__(self, ped_blocked: np.ndarray, res: float, origin, traffic: TrafficModel, rate_per_min: float,
                 zone_scale: dict | None, rng: np.random.Generator, cap: int = P.PED_MAX_COUNT,
                 route_blocked: np.ndarray | None = None, route_cache: dict | None = None):
        """ped_blocked: this episode's walls/objects (forces, hard constraints). route_blocked: the grid
        used for zones and route fields (defaults to ped_blocked); fields are memoized in route_cache,
        so pass a fixed nominal grid when sharing a cache across episodes."""
        self.res = res
        self.origin = origin
        self.rng = rng
        self.blocked = ped_blocked.astype(np.uint8)
        H, W = self.blocked.shape
        free = (self.blocked == 0).astype(np.uint8)
        e = cv2.distanceTransform(free, cv2.DIST_L2, 5)
        self.edt = np.maximum((e - 0.5) * res, 0.0).astype(np.float64)
        gy_img, gx_img = np.gradient(self.edt)
        gx, gy = gx_img, -gy_img
        mag = np.hypot(gx, gy)
        mag[mag == 0] = 1.0
        self.gradx = (gx / mag).astype(np.float64)
        self.grady = (gy / mag).astype(np.float64)
        self.walk_mean, self.walk_std = traffic.walk_speed_mps
        if route_blocked is None:
            route_blocked, route_cache = self.blocked, None
        cache = route_cache if route_cache is not None else {}
        rkey = ("ped_route", hashlib.sha1(np.ascontiguousarray(route_blocked).tobytes()).hexdigest(),
                route_blocked.shape, traffic.model_dump_json())
        if rkey not in cache:
            rfree = (route_blocked == 0).astype(np.uint8)
            redt = np.maximum((cv2.distanceTransform(rfree, cv2.DIST_L2, 5) - 0.5) * res, 0.0)
            rwalk = redt > P.PED_RADIUS_M + 0.02
            rw = P.PED_RADIUS_M + 0.1
            cache[rkey] = {"free": rfree, "edt": redt, "walk": rwalk, "fields": {},
                           "weight": 1.0 + 4.0 * np.clip((rw - redt) / rw, 0.0, 1.0),
                           "zones": self._zones(traffic, rwalk), "anchors": None}
        route = cache[rkey]
        zones = route["zones"]
        pairs = []
        self.dropped_pairs = [(o, d) for o, d, w in traffic.od_pairs
                              if not (o in zones and d in zones and o != d and 0 < w < math.inf)]
        for o, d, w in traffic.od_pairs:
            if o in zones and d in zones and o != d and 0 < w < math.inf:
                f = 1.0
                for z in (o, d):
                    f *= float((zone_scale or {}).get(z, 1.0))
                pairs.append((o, d, float(w), float(w) * f))
        if not pairs:
            if route["anchors"] is None:
                route["anchors"] = self._anchor_zones(route["walk"], route["edt"])
            zones = route["anchors"]
            ids = list(zones)
            pairs = [(a, b, 1.0, 1.0) for a in ids for b in ids if a != b]
            self.source = "uniform_anchors"
        else:
            self.source = "traffic_model"
        dest_ids = sorted({p[1] for p in pairs})
        self.dest_index = {z: i for i, z in enumerate(dest_ids)}
        self.fields = np.zeros((max(len(dest_ids), 1), H, W, 2), np.float32)
        # routes prefer walkable floor but stay defined right up to the walls
        for z, i in self.dest_index.items():
            if z not in route["fields"]:
                rr, cc = zones[z]
                dist = dijkstra2d_weighted(route["free"], route["weight"], rr.astype(np.int64),
                                           cc.astype(np.int64), res)
                route["fields"][z] = route_field(dist)
            self.fields[i] = route["fields"][z]
        self.zones = zones
        self.pairs = pairs
        tot_w = sum(p[2] for p in pairs)
        eff_w = sum(p[3] for p in pairs)
        self.rate_s = rate_per_min / 60.0 * (eff_w / tot_w if tot_w > 0 else 0.0)
        self.pair_p = np.array([p[3] for p in pairs], float)
        if self.pair_p.sum() > 0:
            self.pair_p /= self.pair_p.sum()

        self.n = cap
        self.px = np.zeros(cap); self.py = np.zeros(cap)
        self.vx = np.zeros(cap); self.vy = np.zeros(cap)
        self.v0 = np.ones(cap); self.gx = np.zeros(cap); self.gy = np.zeros(cap)
        self.dz = np.zeros(cap, np.int64)
        self.alive = np.zeros(cap, np.uint8)
        self.age = np.zeros(cap)
        self.stuck = np.zeros(cap)
        self.prog = np.full(cap, 1e9)
        self.yld = np.zeros(cap)
        self.ctrl = np.zeros(cap, np.uint8)
        self.armed = np.ones(cap, np.uint8)
        self.peak = 0

    def _cells_world(self, rr, cc):
        H = self.blocked.shape[0]
        return self.origin[0] + (cc + 0.5) * self.res, self.origin[1] + (H - rr - 0.5) * self.res

    def _zones(self, traffic: TrafficModel, walkable) -> dict:
        out = {}
        H, W = walkable.shape
        for z in traffic.zones:
            if len(z.polygon) < 3:
                continue
            m = np.zeros((H, W), np.uint8)
            fill_polygons(m, [np.asarray(z.polygon, float)], self.res, self.origin, 1)
            rr, cc = np.nonzero(m.astype(bool) & walkable)
            if len(rr):
                out[z.id] = (rr, cc)
        return out

    def _anchor_zones(self, walkable, edt) -> dict:
        """No usable traffic zones: six well-spread anchor areas in open floor (fixed seed per grid)."""
        rng = np.random.default_rng(7)
        open_ = edt > 0.5
        rr, cc = np.nonzero(open_ if open_.any() else walkable)
        out = {}
        if len(rr) == 0:
            return out
        order = rng.permutation(len(rr))
        xs, ys = self._cells_world(rr, cc)
        pts = []
        for i in order:
            p = (xs[i], ys[i])
            if all(math.hypot(p[0] - q[0], p[1] - q[1]) >= 2.0 for q in pts):
                pts.append(p)
                if len(pts) == 6:
                    break
        if len(pts) < 2 and len(order) > 1:
            pts = [(xs[order[0]], ys[order[0]]), (xs[order[-1]], ys[order[-1]])]
        wr, wc = np.nonzero(walkable)
        wx, wy = self._cells_world(wr, wc)
        for k, (x, y) in enumerate(pts):
            sel = np.hypot(wx - x, wy - y) <= 0.6
            if sel.any():
                out[f"anchor_{k}"] = (wr[sel], wc[sel])
        return out

    @property
    def n_alive(self) -> int:
        return int(self.alive.sum())

    def spawn(self, dt: float, robot_xy, robot_r: float):
        if self.rate_s <= 0 or not self.pairs:
            return
        k = self.rng.poisson(self.rate_s * dt)
        for _ in range(int(k)):
            free_slots = np.nonzero((self.alive == 0) & (self.ctrl == 0))[0]
            if len(free_slots) == 0:
                return
            pi = int(self.rng.choice(len(self.pairs), p=self.pair_p))
            o, d, _, _ = self.pairs[pi]
            orr, occ = self.zones[o]
            for _try in range(3):
                j = int(self.rng.integers(len(orr)))
                x, y = self._cells_world(orr[j], occ[j])
                x += self.rng.uniform(-0.5, 0.5) * self.res
                y += self.rng.uniform(-0.5, 0.5) * self.res
                if self.edt[orr[j], occ[j]] <= P.PED_RADIUS_M:  # this episode put an object here
                    continue
                al = self.alive.astype(bool)
                if al.any() and np.min(np.hypot(self.px[al] - x, self.py[al] - y)) < 0.6:
                    continue
                if math.hypot(x - robot_xy[0], y - robot_xy[1]) < robot_r + P.PED_RADIUS_M + 0.3:
                    continue
                drr, dcc = self.zones[d]
                g = int(self.rng.integers(len(drr)))
                gx, gy = self._cells_world(drr[g], dcc[g])
                i = int(free_slots[0])
                self.px[i], self.py[i] = x, y
                self.gx[i], self.gy[i] = gx, gy
                self.dz[i] = self.dest_index[d]
                self.v0[i] = float(np.clip(self.rng.normal(self.walk_mean, self.walk_std), 0.4, 1.9))
                H = self.blocked.shape[0]
                c = int((x - self.origin[0]) / self.res)
                r = H - 1 - int((y - self.origin[1]) / self.res)
                f = self.fields[self.dz[i], r, c]
                self.vx[i], self.vy[i] = self.v0[i] * f[0], self.v0[i] * f[1]
                self.age[i] = 0.0
                self.stuck[i] = 0.0
                self.prog[i] = 1e9
                self.yld[i] = 0.0
                self.armed[i] = 1
                self.alive[i] = 1
                break
        self.peak = max(self.peak, self.n_alive)

    def add_controlled(self, x: float, y: float) -> int:
        i = 0
        self.px[i], self.py[i] = x, y
        self.vx[i] = self.vy[i] = 0.0
        self.ctrl[i] = 1
        self.alive[i] = 1
        self.v0[i] = P.JOYSTICK_MAX_SPEED
        return i

    def step(self, dt: float, robot_poly: np.ndarray, robot_xy=None, robot_r: float = 0.0) -> np.ndarray:
        if robot_xy is None:
            robot_xy = robot_poly.mean(axis=0)
            robot_r = float(np.hypot(*(robot_poly - robot_xy).T).max())
        return ped_step(self.px, self.py, self.vx, self.vy, self.v0, self.gx, self.gy, self.dz, self.alive,
                        self.age, self.stuck, self.prog, self.yld, self.ctrl, self.fields, self.edt, self.gradx, self.grady, self.blocked,
                        self.res, self.origin[0], self.origin[1], robot_poly, float(robot_xy[0]),
                        float(robot_xy[1]), robot_r + P.PED_RADIUS_M + 0.1, dt, 2, P.PED_RADIUS_M,
                        P.PED_TAU, P.PED_A, P.PED_B, P.PED_LAMBDA, P.PED_WALL_A, P.PED_WALL_B,
                        P.PED_ROBOT_A, P.PED_ROBOT_B, P.PED_MAX_SPEED_FACTOR, P.PED_GOAL_RADIUS_M,
                        P.PED_MAX_LIFETIME_S, P.PED_STUCK_MAX_S)

    def positions(self, decimals: int = 2) -> list:
        al = np.nonzero(self.alive)[0]
        return [[round(float(self.px[i]), decimals), round(float(self.py[i]), decimals)] for i in al]
