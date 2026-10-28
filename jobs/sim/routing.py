"""World geometry for one run (c-space, masks) and leg planning into followable rails."""
from __future__ import annotations

import math
from dataclasses import dataclass, field

import numpy as np
import shapely

from . import params as P
from .cspace import Kernels, add_discs, add_polygons, any_free, build_cspace, make_kernels, margin_cells
from .geometry import RobotModel, transform
from .planner import _hit, _kidx, cspace_connected, dijkstra2d, pass_width, rail_hits, dijkstra2d_weighted, free_width, hybrid_astar, prim_samples, primitive_set, smooth_path
from .raster import Layers, classify_thresholds

U_JUNCTION = 0.05
PASS_TILT_RAD = math.radians(60.0)
PASS_TILT_STEPS = 6   # tilts of 10 degrees


@dataclass
class Geom:
    """Static geometry of one episode (or of a feasibility check)."""
    layers: Layers
    model: RobotModel
    kern: Kernels
    cs: np.ndarray            # (K,H,W) uint8
    margin: np.ndarray        # (K,H,W) uint8 cells
    free_any: np.ndarray      # (H,W) bool
    robot_blocked: np.ndarray  # (H,W) uint8
    ped_blocked: np.ndarray    # (H,W) uint8
    thr_block: np.ndarray      # (H,W) uint8 center may not cross
    thr_unknown: np.ndarray    # (H,W) uint8
    thr_class: dict
    polys: list = field(default_factory=list)
    cost_map: np.ndarray | None = None

    @property
    def res(self):
        return self.layers.res

    @property
    def origin(self):
        return self.layers.origin


@dataclass
class BaseWorld:
    """Per-batch precomputation: c-space of everything that does not vary between episodes."""
    layers: Layers
    model: RobotModel
    kern: Kernels
    cs_static: np.ndarray
    static_robot: np.ndarray
    static_ped: np.ndarray
    thr_block: np.ndarray
    thr_unknown: np.ndarray
    thr_class: dict


def make_base_world(layers: Layers, model: RobotModel) -> BaseWorld:
    kern = make_kernels(model.footprint, layers.res)
    robot_cells = layers.compose([], keepouts=True)
    ped_cells = layers.compose([], keepouts=False)
    static_robot = (robot_cells != 255).astype(np.uint8)
    static_ped = (ped_cells != 255).astype(np.uint8)
    cs = build_cspace(static_robot, kern)
    blk, unk, cls = classify_thresholds(layers, model.max_threshold_m)
    return BaseWorld(layers, model, kern, cs, static_robot, static_ped, blk.astype(np.uint8),
                     unk.astype(np.uint8), cls)


def make_geom(base: BaseWorld, polys: list[np.ndarray]) -> Geom:
    from .geometry import fill_polygons
    L = base.layers
    cs = base.cs_static.copy()
    rb = base.static_robot.copy()
    pb = base.static_ped.copy()
    if polys:
        add_polygons(cs, polys, base.kern, L.res, L.origin)
        fill_polygons(rb, polys, L.res, L.origin, 1)
        fill_polygons(pb, polys, L.res, L.origin, 1)
    return Geom(L, base.model, base.kern, cs, margin_cells(cs), any_free(cs), rb, pb,
                base.thr_block, base.thr_unknown, base.thr_class, list(polys))


# ---------------------------------------------------------------- rails
@dataclass
class Rail:
    x: np.ndarray
    y: np.ndarray
    th: np.ndarray     # unwrapped heading
    s: np.ndarray      # pseudo arc length: translation m + rotation rad * r_eq
    rot: np.ndarray    # uint8, 1 if the step ending at i is an in-place rotation
    u_allow: np.ndarray
    length_m: float
    rev: np.ndarray    # uint8, 1 if the step ending at i drives backwards

    @property
    def s_end(self) -> float:
        return float(self.s[-1])

    def pose(self, s: float) -> tuple[float, float, float]:
        return (float(np.interp(s, self.s, self.x)), float(np.interp(s, self.s, self.y)),
                float(np.interp(s, self.s, self.th)))

    def index(self, s: float) -> int:
        return int(min(max(np.searchsorted(self.s, s, side="right"), 1), len(self.s) - 1))


def _speed_profile(s: np.ndarray, rot: np.ndarray, rev: np.ndarray, v_nom: float, decel: float) -> np.ndarray:
    n = len(s)
    cap = np.full(n, v_nom)
    if n > 2:
        mode = rot.astype(np.int64) * 2 + rev
        j = np.nonzero(mode[1:-1] != mode[2:])[0] + 1
        cap[j] = U_JUNCTION
    cap[-1] = 0.0
    u = cap.copy()
    for i in range(n - 2, -1, -1):
        u[i] = min(cap[i], math.sqrt(u[i + 1] ** 2 + 2 * decel * (s[i + 1] - s[i])))
    return u


def rail_drive_time(rail: Rail, accel: float) -> float:
    """Time to drive a rail alone: the speed profile's braking caps (turn junctions, end of leg) plus the
    acceleration limit, starting from rest. This is the nominal path time the episode timeout scales."""
    s, cap = rail.s, rail.u_allow
    if len(s) < 2:
        return 0.0
    ds = np.diff(s)
    v = np.empty(len(s))
    v[0] = 0.0
    for i in range(len(ds)):
        v[i + 1] = min(cap[i + 1], math.sqrt(v[i] * v[i] + 2.0 * accel * ds[i]))
    vm = np.maximum(0.5 * (v[:-1] + v[1:]), 0.02)  # the follower never creeps slower than 0.02 m/s
    return float(np.sum(ds / vm))


def _finish(xs, ys, ths, rots, model: RobotModel, accel: float, omega_max: float, revs=None) -> Rail:
    rev = np.zeros(len(xs), np.uint8) if revs is None else np.asarray(revs, np.uint8)
    x = np.asarray(xs, float)
    y = np.asarray(ys, float)
    th = np.asarray(ths, float)
    rot = np.asarray(rots, np.uint8)
    r_eq = model.nominal_speed / omega_max
    ds = np.where(rot[1:] == 1, r_eq * np.abs(np.diff(th)), np.hypot(np.diff(x), np.diff(y)))
    s = np.concatenate([[0.0], np.cumsum(ds)])
    # drop zero-length steps (duplicate samples) to keep s strictly increasing
    keep = np.concatenate([[True], ds > 1e-9])
    x, y, th, rot, s, rev = x[keep], y[keep], th[keep], rot[keep], s[keep], rev[keep]
    length = float(np.hypot(np.diff(x), np.diff(y))[rot[1:] == 0].sum()) if len(x) > 1 else 0.0
    return Rail(x, y, th, s, rot, _speed_profile(s, rot, rev, model.nominal_speed, accel), length, rev)


def rail_from_vertices(vx, vy, th0: float, model: RobotModel, res: float, accel: float, omega_max: float,
                       seg_rev=None) -> Rail:
    """Straight segments between vertices with in-place rotations at the vertices.
    seg_rev[i-1] = True drives segment i-1 -> i backwards."""
    xs, ys, ths, rots, revs = [vx[0]], [vy[0]], [th0], [0], [0]
    th = th0
    step = min(0.02, res)
    for i in range(1, len(vx)):
        dx, dy = vx[i] - vx[i - 1], vy[i] - vy[i - 1]
        L = math.hypot(dx, dy)
        if L < 1e-9:
            continue
        back = bool(seg_rev[i - 1]) if seg_rev is not None else False
        phi = math.atan2(dy, dx) + (math.pi if back else 0.0)
        d = (phi - th + math.pi) % (2 * math.pi) - math.pi
        if abs(d) > 1e-4:
            n = max(1, int(math.ceil(abs(d) / math.radians(5))))
            for j in range(1, n + 1):
                xs.append(vx[i - 1]); ys.append(vy[i - 1]); ths.append(th + d * j / n); rots.append(1); revs.append(0)
            th = th + d
        n = max(1, int(math.ceil(L / step)))
        for j in range(1, n + 1):
            xs.append(vx[i - 1] + dx * j / n); ys.append(vy[i - 1] + dy * j / n); ths.append(th); rots.append(0)
            revs.append(1 if back else 0)
    return _finish(xs, ys, ths, rots, model, accel, omega_max, revs)


def rail_from_lattice(nx, ny, nk, npr, prims, th0: float, model: RobotModel, res: float,
                      accel: float, omega_max: float) -> Rail:
    K = P.K_HEADINGS
    dth = 2 * math.pi / K
    xs, ys, ths, rots = [nx[0]], [ny[0]], [th0], [0]
    th = th0
    d = (nk[0] * dth - th + math.pi) % (2 * math.pi) - math.pi
    if abs(d) > 1e-4:
        xs.append(nx[0]); ys.append(ny[0]); th = th + d; ths.append(th); rots.append(1)
    for i in range(1, len(nx)):
        p = int(npr[i])
        if prims.kind[p] == 1:
            th = th + prims.dk[p] * dth
            xs.append(nx[i]); ys.append(ny[i]); ths.append(th); rots.append(1)
            continue
        th_start = th
        base = nk[i - 1] * dth
        for (sx, sy, sth) in prim_samples(nx[i - 1], ny[i - 1], int(nk[i - 1]), prims.length[p], int(prims.dk[p]), K, res):
            xs.append(sx); ys.append(sy); ths.append(th_start + (sth - base)); rots.append(0)
        th = ths[-1]
    return _finish(xs, ys, ths, rots, model, accel, omega_max)


def rail_from_inplace_lattice(nx, ny, nk, npr, prims, th0: float, model: RobotModel, accel: float,
                              omega_max: float) -> Rail:
    """Rail that follows an in-place lattice path exactly: straight steps (forward or backward) at lattice
    headings and in-place turns one heading step at a time, in the direction the search turned."""
    dth = 2 * math.pi / P.K_HEADINGS
    xs, ys, ths, rots, revs = [float(nx[0])], [float(ny[0])], [th0], [0], [0]
    th = th0
    d = (nk[0] * dth - th + math.pi) % (2 * math.pi) - math.pi
    if abs(d) > 1e-9:
        th = th + d
        xs.append(float(nx[0])); ys.append(float(ny[0])); ths.append(th); rots.append(1); revs.append(0)
    for i in range(1, len(nx)):
        p = int(npr[i])
        if prims.kind[p] == 1:
            th = th + prims.dk[p] * dth
            xs.append(float(nx[i])); ys.append(float(ny[i])); ths.append(th); rots.append(1); revs.append(0)
        else:
            xs.append(float(nx[i])); ys.append(float(ny[i])); ths.append(th); rots.append(0)
            revs.append(1 if prims.dir[p] < 0 else 0)
    return _finish(xs, ys, ths, rots, model, accel, omega_max, revs)


# ---------------------------------------------------------------- planning
@dataclass
class PlanResult:
    status: str                 # ok | no_path | search_limit | start_blocked | goal_blocked
    rail: Rail | None = None
    crossed: list = field(default_factory=list)
    nodes: int = 0
    start: tuple | None = None
    goal: tuple | None = None


def snap_pose(geom: Geom, x: float, y: float, yaw: float, center: np.ndarray | None = None):
    """Nearest collision-free pose within WAYPOINT_SNAP_M; prefers the requested heading."""
    L = geom.layers
    H, W = L.shape
    K = geom.cs.shape[0]
    dth = 2 * math.pi / K
    r0, c0 = L.world_to_cell(x, y)
    rad = int(math.ceil(P.WAYPOINT_SNAP_M / L.res))
    cand = []
    for dr in range(-rad, rad + 1):
        for dc in range(-rad, rad + 1):
            d = math.hypot(dr, dc) * L.res
            if d <= P.WAYPOINT_SNAP_M + 1e-9:
                cand.append((d, dr, dc))
    cand.sort()
    k_want = _kidx(yaw, dth, K)
    ok = [(d, dr, dc) for d, dr, dc in cand
          if 0 <= int(r0) + dr < H and 0 <= int(c0) + dc < W and geom.free_any[int(r0) + dr, int(c0) + dc]
          and (center is None or not center[int(r0) + dr, int(c0) + dc])]
    # prefer poses that keep the requested heading, then the nearest free cell at any heading
    ok.sort(key=lambda t: (geom.cs[k_want, int(r0) + t[1], int(c0) + t[2]] != 0, t[0]))
    for d, dr, dc in ok:
        r, c = int(r0) + dr, int(c0) + dc
        free_k = np.nonzero(geom.cs[:, r, c] == 0)[0]
        kd = np.abs((free_k - k_want + K // 2) % K - K // 2)
        k = int(free_k[np.argmin(kd)])
        if dr == 0 and dc == 0:
            px, py = x, y
        else:
            px, py = L.origin[0] + (c + 0.5) * L.res, L.origin[1] + (H - r - 0.5) * L.res
        th = yaw if (k == k_want and not _hit(geom.cs, float(yaw), r, c)) else k * dth
        return px, py, th
    return None


WAYPOINT_SEARCH_M = 1.0


def waypoint_check(geom: Geom, wp_id: str, x: float, y: float, yaw: float = 0.0) -> dict:
    """Why a waypoint has (or lacks) a collision-free robot pose. -> {"waypoint", "ok", "xy", "inside",
    "gap_m" (narrowest free width through the point), "required_gap_m" (footprint width), "nearest_obstacle",
    "nearest_obstacle_m", "nearest_free_xy", "nearest_free_m", "message"}."""
    from clearance_core.grid import UNKNOWN
    L = geom.layers
    H, W = L.shape
    model = geom.model
    out = {"waypoint": wp_id, "xy": [round(float(x), 3), round(float(y), 3)], "ok": True, "inside": False,
           "gap_m": None, "required_gap_m": round(model.width, 3), "nearest_obstacle": None,
           "nearest_obstacle_m": None, "nearest_free_xy": None, "nearest_free_m": None,
           "required_clearance_m": None, "message": ""}
    if snap_pose(geom, x, y, yaw) is not None:
        return out
    out["ok"] = False
    r0, c0 = (int(v) for v in L.world_to_cell(x, y))
    if not (0 <= r0 < H and 0 <= c0 < W):
        out["inside"] = True
        out["message"] = f"waypoint {wp_id} lies outside the mapped area"
        return out
    rb = geom.robot_blocked
    n = int(math.ceil((WAYPOINT_SEARCH_M + model.radius) / L.res))
    ra, rb_, ca, cb = max(r0 - n, 0), min(r0 + n + 1, H), max(c0 - n, 0), min(c0 + n + 1, W)
    win = rb[ra:rb_, ca:cb].astype(bool)
    rr, cc = np.nonzero(win)
    inside = bool(rb[r0, c0])
    out["inside"] = inside
    if len(rr):
        cx = L.origin[0] + (cc + ca + 0.5) * L.res
        cy = L.origin[1] + (H - (rr + ra) - 0.5) * L.res
        d = np.hypot(cx - x, cy - y)
        j = int(np.argmin(d))
        bx, by = float(cx[j]), float(cy[j])
        out["nearest_obstacle_m"] = 0.0 if inside else round(max(float(d[j]) - 0.5 * L.res, 0.0), 3)
        out["nearest_obstacle"] = _what_is_at(geom, bx, by, int(rr[j] + ra), int(cc[j] + ca), UNKNOWN)
    widths = [free_width(rb, L.res, L.origin[0], L.origin[1], float(x), float(y), a, np.zeros(1), 3.0)
              for a in np.arange(0.0, math.pi, math.pi / 18)]
    out["gap_m"] = round(float(min(widths)), 3)
    # nearest robot center with a collision-free heading
    fr, fc = np.nonzero(geom.free_any[ra:rb_, ca:cb])
    if len(fr):
        fx = L.origin[0] + (fc + ca + 0.5) * L.res
        fy = L.origin[1] + (H - (fr + ra) - 0.5) * L.res
        d = np.hypot(fx - x, fy - y)
        j = int(np.argmin(d))
        if d[j] <= WAYPOINT_SEARCH_M:
            out["nearest_free_xy"] = [round(float(fx[j]), 3), round(float(fy[j]), 3)]
            out["nearest_free_m"] = round(float(d[j]), 3)
    obj = out["nearest_obstacle"] or "an obstacle"
    inr = float(shapely.Polygon(model.footprint).exterior.distance(shapely.Point(0.0, 0.0)))
    out["required_clearance_m"] = round(inr, 3)
    d_obs = out["nearest_obstacle_m"]
    if inside:
        msg = f"waypoint {wp_id} is inside an obstacle ({obj})"
    elif d_obs is not None and d_obs < inr:
        msg = (f"stop point {wp_id} is too close to {obj} for this robot: {d_obs:.2f} m from it, while the footprint "
               f"reaches at least {inr:.2f} m from the robot center at every heading (free width there "
               f"{out['gap_m']:.2f} m, footprint width {model.width:.2f} m)")
    else:
        msg = (f"stop point {wp_id} has no collision-free pose for this robot at any heading: free width there "
               f"{out['gap_m']:.2f} m, footprint width {model.width:.2f} m")
        if d_obs is not None:
            msg += f", nearest obstacle {obj} {d_obs:.2f} m away"
    if out["nearest_free_xy"] is not None:
        msg += (f"; nearest collision-free robot pose {out['nearest_free_m']:.2f} m away at "
                f"({out['nearest_free_xy'][0]:.2f}, {out['nearest_free_xy'][1]:.2f}), beyond the "
                f"{P.WAYPOINT_SNAP_M:.2f} m a stop point may shift")
    else:
        msg += f"; no collision-free robot pose within {WAYPOINT_SEARCH_M:.1f} m"
    out["message"] = msg
    return out


def _what_is_at(geom: Geom, x: float, y: float, r: int, c: int, unknown_value: int) -> str:
    """Name of the obstacle covering a blocked cell: object id and label, keep-out zone, unscanned area or wall."""
    from shapely.geometry import Point
    L = geom.layers
    pt = Point(x, y)
    fixed = [p for p in L.prims if not p.movable]
    named = [(poly, f"{p.id} ({p.label})") for poly, p in zip(L.fixed_polys, fixed)]
    nm = len(L.movables)
    named += [(poly, f"{p.id} ({p.label})") for poly, p in zip(geom.polys[:nm], L.movables)]
    named += [(poly, "a clutter box") for poly in geom.polys[nm:]]
    best = None
    for poly, name in named:
        d = shapely.Polygon(poly).distance(pt)
        if d <= 1.5 * L.res and (best is None or d < best[0]):
            best = (d, name)
    if best is not None:
        return best[1]
    if L.keepout[r, c]:
        return "a keep-out zone"
    if L.static_cells[r, c] == unknown_value:
        return "an unscanned area"
    return "a wall or scanned obstacle"


def plan_route(geom: Geom, start: tuple[float, float, float], goal: tuple[float, float],
               center: np.ndarray, cfg: dict, extra_cs: np.ndarray | None = None) -> PlanResult:
    """Plan from a start pose to a goal position. `center` cells may not be crossed by the robot center."""
    model = geom.model
    L = geom.layers
    cs = geom.cs if extra_cs is None else extra_cs
    margin = geom.margin
    H, W = L.shape
    K = cs.shape[0]
    dth = 2 * math.pi / K
    prims = primitive_set(model.in_place, model.turn_radius, L.res)
    gr, gc = L.world_to_cell(goal[0], goal[1])
    free = (cs == 0).any(axis=0) & (center == 0)
    if not (0 <= gr < H and 0 <= gc < W) or not free[gr, gc]:
        return PlanResult("goal_blocked")
    # heuristic: geodesic distance weighted by the smallest gap penalty any heading can get in each cell
    if geom.cost_map is None:
        ref = P.GAP_COST_REF_M / L.res
        best = geom.margin.max(axis=0).astype(np.float64)
        geom.cost_map = 1.0 + P.GAP_COST_WEIGHT * np.clip((ref - best) / ref, 0.0, 1.0)
    h2d = dijkstra2d_weighted(free.astype(np.uint8), geom.cost_map, np.array([gr]), np.array([gc]), L.res)
    sr, sc = L.world_to_cell(start[0], start[1])
    if not (0 <= sr < H and 0 <= sc < W):
        return PlanResult("start_blocked")
    if not np.isfinite(h2d[sr, sc]):
        return PlanResult("no_path")
    sk = _kidx(start[2], dth, K)
    if cs[sk, sr, sc]:
        free_k = np.nonzero(cs[:, sr, sc] == 0)[0]
        kd = np.abs((free_k - sk + K // 2) % K - K // 2)
        if not model.in_place:
            free_k, kd = free_k[kd <= 1], kd[kd <= 1]  # no spin in place: only the adjacent lattice heading
        if len(free_k) == 0:
            return PlanResult("start_blocked")
        sk = int(free_k[np.argmin(kd)])
    gap_ref = P.GAP_COST_REF_M / L.res
    max_nodes = int(cfg.get("max_nodes", P.PLANNER_MAX_NODES))
    nn_total = 0
    # one state per (cell, heading) first. When that exhausts, the leg may still be drivable: a pinch passable on a
    # single heading needs the robot lined up to a fraction of a cell, and an earlier state in the same cell prunes
    # the one that would have made it. If the c-space connectivity check does not rule a path out, search again
    # with a 4x finer closed set. Replans around pedestrians make one pass (a failed replan just waits again).
    subs = (1, 4) if extra_cs is None else (1,)
    for sub in subs:
        st, xs, ys, ks, ps, nn = hybrid_astar(
            cs, margin, center, h2d, L.res, L.origin[0], L.origin[1], float(start[0]), float(start[1]), sk,
            float(goal[0]), float(goal[1]), prims.goal_tol, prims.kind, prims.length, prims.dk, prims.dir,
            model.nominal_speed, cfg["omega_max"], gap_ref, P.GAP_COST_WEIGHT, P.ROTATION_PENALTY_S,
            cfg.get("hweight", P.HEURISTIC_WEIGHT), max_nodes if sub == 1 else min(max_nodes, P.FINE_RETRY_MAX_NODES), P.REVERSE_COST_FACTOR,
            P.REVERSE_RADIUS_M, sub)
        nn_total += int(nn)
        if st != 1 or sub == subs[-1]:
            break
        if not cspace_connected(cs, center, int(sr), int(sc), int(sk), int(gr), int(gc)):
            break
    if st != 0:
        return PlanResult("search_limit" if st == 2 else "no_path", nodes=nn_total)
    if model.in_place:
        vx, vy, vrev = _smooth_runs(cs, margin, center, L, xs, ys, ks, ps, prims, float(start[2]))
        rail = rail_from_vertices(vx, vy, float(start[2]), model, L.res, cfg["accel"], cfg["omega_max"], vrev)
        if rail_hits(cs, center, L.res, L.origin[0], L.origin[1], rail.x, rail.y, rail.th) >= 0:
            # a shortcut or the short-way turn at a vertex sweeps the footprint through an obstacle:
            # follow the lattice path itself (every pose and turn on it was checked by the search)
            rail = rail_from_inplace_lattice(xs, ys, ks, ps, prims, float(start[2]), model, cfg["accel"],
                                             cfg["omega_max"])
    else:
        rail = rail_from_lattice(xs, ys, ks, ps, prims, float(start[2]), model, L.res, cfg["accel"], cfg["omega_max"])
    return PlanResult("ok", rail, crossed_thresholds(geom, rail), nn_total, start, goal)


def _smooth_runs(cs, margin, center, L, xs, ys, ks, ps, prims, th0):
    """Shortcut-smooth the lattice path run by run (forward runs and backward runs separately)."""
    trans = [i for i in range(1, len(xs)) if prims.kind[ps[i]] == 0]
    if not trans:
        return [float(xs[0])], [float(ys[0])], []
    pidx = [0] + trans
    dirs = [int(prims.dir[ps[i]]) for i in trans]
    px, py, pk = xs[pidx], ys[pidx], ks[pidx]
    rr, cc = L.world_to_cell(px, py)
    node_m = margin[pk, rr, cc].astype(np.float64)
    req = P.SMOOTH_MIN_MARGIN_M / L.res
    vx, vy, vrev = [float(px[0])], [float(py[0])], []
    th = th0
    a = 0
    while a < len(dirs):
        b = a
        while b + 1 < len(dirs) and dirs[b + 1] == dirs[a]:
            b += 1
        back = dirs[a] < 0
        sl = slice(a, b + 2)
        idx = smooth_path(cs, margin, center, L.res, L.origin[0], L.origin[1], px[sl].copy(), py[sl].copy(),
                          node_m[sl].copy(), th, req, 40, back)
        for j in idx[1:]:
            vx.append(float(px[a + j])); vy.append(float(py[a + j])); vrev.append(back)
        if len(vx) >= 2:
            th = math.atan2(vy[-1] - vy[-2], vx[-1] - vx[-2]) + (math.pi if back else 0.0)
        a = b + 1
    return vx, vy, vrev


def crossed_thresholds(geom: Geom, rail: Rail) -> list[str]:
    if not geom.layers.thresholds:
        return []
    rr, cc = geom.layers.world_to_cell(rail.x, rail.y)
    H, W = geom.layers.shape
    ok = (rr >= 0) & (rr < H) & (cc >= 0) & (cc < W)
    rr, cc = rr[ok], cc[ok]
    hits = []
    for t in geom.layers.thresholds:
        m = t.mask[rr, cc]
        if m.any():
            hits.append((int(np.argmax(m)), t.id))
    return [i for _, i in sorted(hits)]


# ---------------------------------------------------------------- gap measurement
def exact_gap(geom: Geom, x: float, y: float, th: float) -> float:
    """Exact distance between the footprint polygon at a pose and nearby blocked cell squares."""
    L = geom.layers
    fp = transform(geom.model.footprint, x, y, th)
    reach = geom.model.radius + 0.6
    H, W = L.shape
    r0, c0 = L.world_to_cell(x, y)
    n = int(math.ceil(reach / L.res))
    ra, rb = max(int(r0) - n, 0), min(int(r0) + n + 1, H)
    ca, cb = max(int(c0) - n, 0), min(int(c0) + n + 1, W)
    sub = geom.robot_blocked[ra:rb, ca:cb]
    rr, cc = np.nonzero(sub)
    if len(rr) == 0:
        return reach
    rr = rr + ra
    cc = cc + ca
    x0 = L.origin[0] + cc * L.res
    y0 = L.origin[1] + (H - rr - 1) * L.res
    boxes = shapely.box(x0, y0, x0 + L.res, y0 + L.res)
    d = shapely.distance(shapely.Polygon(fp), boxes)
    return float(min(d.min(), reach))


def rail_gap_profile(geom: Geom, rail: Rail):
    """Tightest point of a rail: (narrowest free width, required width, xy)."""
    m = geom.model
    sel = np.nonzero(rail.rot == 0)[0]
    if len(sel) == 0:
        return None
    step = max(1, int(round(0.02 / max(np.median(np.diff(rail.s)) if len(rail.s) > 1 else 0.02, 1e-3))))
    sel = sel[::step]
    offs = np.linspace(m.footprint[:, 0].min() + 0.01, m.footprint[:, 0].max() - 0.01, 5)
    req = m.width
    best = None
    rb = geom.robot_blocked
    L = geom.layers
    lo, hi = float(m.footprint[:, 0].min()), float(m.footprint[:, 0].max())
    for i in sel:
        for o in offs:
            w = pass_width(rb, L.res, L.origin[0], L.origin[1], rail.x[i], rail.y[i], rail.th[i], np.array([o]), 3.0,
                           lo, hi, PASS_TILT_RAD, PASS_TILT_STEPS)
            slack = w - req
            if best is None or slack < best[0]:
                best = (slack, w, i, o)
    _, w, i, o = best
    c, s_ = math.cos(rail.th[i]), math.sin(rail.th[i])
    # location of the tightest point: the probe on the footprint where the passage is narrowest
    return float(w), float(req), (float(rail.x[i] + o * c), float(rail.y[i] + o * s_))


def _widest_corridor(free: np.ndarray, s_rc, g_rc, res: float, slack_m: float = 0.3, iters: int = 20) -> np.ndarray:
    """Free cells whose distance to the nearest obstacle is at least the maximin value of the widest route between
    the two points (plus discs of `slack_m` around the endpoints). The shortest route inside this mask runs through
    the widest available passage, so its narrowest point is the pinch that actually binds the leg."""
    from scipy.ndimage import distance_transform_edt, label
    H, W = free.shape
    d = distance_transform_edt(free) * res
    rr, cc = np.ogrid[:H, :W]
    k = max(1, int(round(slack_m / res)))
    ends = (((rr - s_rc[0]) ** 2 + (cc - s_rc[1]) ** 2 <= k * k) | ((rr - g_rc[0]) ** 2 + (cc - g_rc[1]) ** 2 <= k * k))
    ends &= free

    def joined(th):
        m = (d >= th) | ends
        lab, _ = label(m, structure=np.ones((3, 3), int))
        a, b = lab[s_rc], lab[g_rc]
        return a != 0 and a == b, m

    ok, m = joined(0.0)
    if not ok:
        return free.copy()
    lo, hi, best = 0.0, float(d.max()), m
    for _ in range(iters):
        mid = (lo + hi) / 2
        ok, m = joined(mid)
        if ok:
            lo, best = mid, m
        else:
            hi = mid
    return best & free | (ends & free)


def point_route_gap(geom: Geom, start_xy, goal_xy):
    """For a leg with no robot path: narrowest free width along the shortest point route, or None."""
    L = geom.layers
    free = (geom.robot_blocked == 0)
    H, W = L.shape
    gr, gc = L.world_to_cell(*goal_xy)
    sr, sc = L.world_to_cell(*start_xy)
    if not (0 <= gr < H and 0 <= gc < W and 0 <= sr < H and 0 <= sc < W):
        return None
    free = _widest_corridor(free, (int(sr), int(sc)), (int(gr), int(gc)), L.res)
    free[gr, gc] = True
    free[sr, sc] = True
    d = dijkstra2d(free.astype(np.uint8), np.array([gr]), np.array([gc]), L.res)
    if not np.isfinite(d[sr, sc]):
        return None
    path = [(int(sr), int(sc))]
    r, c = int(sr), int(sc)
    for _ in range(H * W):
        if (r, c) == (int(gr), int(gc)):
            break
        best = (d[r, c], r, c)
        for dr in (-1, 0, 1):
            for dc in (-1, 0, 1):
                rr, cc = r + dr, c + dc
                if 0 <= rr < H and 0 <= cc < W and d[rr, cc] < best[0]:
                    best = (d[rr, cc], rr, cc)
        if (best[1], best[2]) == (r, c):
            break
        r, c = best[1], best[2]
        path.append((r, c))
    pr = np.array([p[0] for p in path])
    pc = np.array([p[1] for p in path])
    xs = L.origin[0] + (pc + 0.5) * L.res
    ys = L.origin[1] + (H - pr - 0.5) * L.res
    look = max(2, int(round(0.10 / L.res)))
    best = None
    rb = geom.robot_blocked
    offs = np.zeros(1)
    for i in range(len(xs)):
        j0, j1 = max(i - look, 0), min(i + look, len(xs) - 1)
        if j1 == j0:
            continue
        hd = math.atan2(ys[j1] - ys[j0], xs[j1] - xs[j0])
        w = pass_width(rb, L.res, L.origin[0], L.origin[1], xs[i], ys[i], hd, offs, 3.0, -0.5 * geom.model.length,
                       0.5 * geom.model.length, PASS_TILT_RAD, PASS_TILT_STEPS)
        if best is None or w < best[0]:
            best = (w, xs[i], ys[i])
    if best is None:
        return None
    return float(best[0]), (float(best[1]), float(best[2]))
