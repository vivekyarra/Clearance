"""One Monte Carlo episode (and the steppable mission used by live sessions)."""
from __future__ import annotations

import hashlib
import json
import math
from dataclasses import dataclass, field

import cv2
import numpy as np
from shapely.geometry import Point, Polygon

from clearance_core.schemas import RobotSpec, Task, Twin

from . import params as P
from .control import choose_speed
from .cspace import add_discs
from .geometry import RobotModel, primitive_polygon, robot_model, transform
from .peds import Crowd, robot_ped_relations
from .raster import Layers, build_layers, layers_key, load_base_grid
from .routing import (BaseWorld, Geom, PlanResult, exact_gap, make_base_world, make_geom, plan_route,
                      rail_drive_time, snap_pose, waypoint_check)

_CTX_CACHE: dict = {}


@dataclass
class BatchContext:
    twin: Twin
    robot: RobotSpec
    task: Task
    density: str
    bound: str
    model: RobotModel
    layers: Layers
    base: BaseWorld
    cfg: dict
    zone_scale: dict
    key: str
    plan_cache: dict = field(default_factory=dict)

    @property
    def rate_per_min(self) -> float:
        if self.density == "none":
            return 0.0
        ppm = self.twin.traffic.persons_per_min
        if self.density not in ppm:
            raise ValueError(f"traffic model has no persons_per_min for density {self.density!r}")
        return float(ppm[self.density])


def make_context(twin: Twin, robot: RobotSpec, task: Task, density: str, bound: str,
                 overrides: dict | None = None, sim_config: dict | None = None,
                 p90_err_m: float | None = None, occ_png_b64: str | None = None) -> BatchContext:
    overrides = dict(overrides or {})
    density = overrides.get("density", density)
    if density not in ("none", "quiet", "normal", "peak"):
        raise ValueError(f"unknown density {density!r}")
    cfg = P.defaults_dict()
    cfg.update(sim_config or {})
    wp = {w.id: w for w in twin.waypoints}
    missing = [s for s in task.sequence if s not in wp]
    if missing:
        raise ValueError(f"task references unknown waypoint ids: {missing}")
    if len(task.sequence) < 2:
        raise ValueError("task.sequence needs at least two waypoints")
    grid_id = hashlib.sha1(load_base_grid(twin, occ_png_b64).data.tobytes()).hexdigest()
    key = layers_key(twin, bound, p90_err_m, overrides, robot.model_dump_json(by_alias=True) + grid_id)
    hit = _CTX_CACHE.get(key)
    if hit is None:
        model = robot_model(robot)
        layers = build_layers(twin, bound, p90_err_m, overrides, occ_png_b64)
        base = make_base_world(layers, model)
        hit = (model, layers, base, {})
        if len(_CTX_CACHE) >= 6:
            _CTX_CACHE.pop(next(iter(_CTX_CACHE)))
        _CTX_CACHE[key] = hit
    model, layers, base, plan_cache = hit
    return BatchContext(twin, robot, task, density, bound, model, layers, base, cfg,
                        _zone_scale(twin, overrides.get("ped_zone_scale")), key, plan_cache)


def _zone_scale(twin: Twin, zs) -> dict:
    """ped_zone_scale = {zone_id: factor >= 0} on the twin's traffic zones."""
    if not zs:
        return {}
    if not isinstance(zs, dict):
        raise ValueError("ped_zone_scale must be a mapping {zone_id: factor}")
    known = {z.id for z in twin.traffic.zones}
    out = {}
    for z, f in zs.items():
        if z not in known:
            raise ValueError(f"ped_zone_scale references unknown traffic zone {z!r}; known: {sorted(known)}")
        try:
            v = float(f)
        except (TypeError, ValueError) as e:
            raise ValueError(f"ped_zone_scale[{z!r}] must be a number") from e
        if not math.isfinite(v) or v < 0:
            raise ValueError(f"ped_zone_scale[{z!r}] must be a finite factor >= 0, got {f!r}")
        out[z] = v
    return out


# ---------------------------------------------------------------- per-episode variation
_TAG_JITTER, _TAG_N_CLUTTER, _TAG_CLUTTER = 1, 2, 3
CLUTTER_CANDIDATES = 256


def stable_int(text: str) -> int:
    """Process-independent 64-bit integer for a string (Python's hash() is salted per process)."""
    return int.from_bytes(hashlib.sha1(text.encode("utf-8")).digest()[:8], "little")


def _stream(seed: int, *key: int) -> np.random.Generator:
    """Independent random stream per (episode seed, purpose, item). Each object and each clutter slot draws from
    its own stream, so adding, removing or reordering objects or waypoints never shifts the draws of the others."""
    return np.random.default_rng([int(seed) & 0xFFFFFFFFFFFFFFFF, *key])


def episode_variation(ctx: BatchContext, seed: int):
    """Jittered movable objects and random clutter boxes -> (bounded polygons, jitter, clutter polygons).
    Keep-clear spots are the waypoints this task visits (other waypoints of the twin do not change the episode)."""
    L = ctx.layers
    wp = {w.id: w for w in ctx.twin.waypoints}
    wps = [np.array(wp[i].xy, dtype=np.float64) for i in dict.fromkeys(ctx.task.sequence)]
    static = ctx.base.static_robot
    jitter = {}
    if ctx.cfg.get("jitter", True):
        others = {p.id: Polygon(primitive_polygon(p)) for p in L.prims}
        for p in L.movables:
            chair = any(lbl in p.label.lower() for lbl in P.CHAIR_LABELS)
            amp = P.JITTER_CHAIR_M if chair else P.JITTER_MOVABLE_M
            base_wall = _blocked_count(static, primitive_polygon(p), L)
            draws = _stream(seed, _TAG_JITTER, stable_int(p.id)).uniform(-1.0, 1.0, (5, 3))
            for dx, dy, dyaw in draws * np.array([amp, amp, P.JITTER_YAW_RAD]):
                poly = primitive_polygon(p, dx, dy, dyaw)
                sp = Polygon(poly)
                if any(sp.distance(Point(w)) < 0.3 for w in wps):
                    continue
                if any(sp.intersects(g) for oid, g in others.items() if oid != p.id):
                    continue
                if _blocked_count(static, poly, L) > base_wall:
                    continue
                jitter[p.id] = (float(dx), float(dy), float(dyaw))
                break
    polys = L.movable_polys(jitter)
    cmax = int(ctx.cfg.get("clutter_max", P.CLUTTER_MAX))
    n_clutter = int(_stream(seed, _TAG_N_CLUTTER).integers(0, cmax + 1)) if cmax > 0 else 0
    clutter = []
    if n_clutter:
        from .geometry import fill_polygons
        occ_now = static.copy()
        fill_polygons(occ_now, polys, L.res, L.origin, 1)
        # clutter goes on open floor: it narrows routes but leaves a robot-width lane beside it
        edt = cv2.distanceTransform((occ_now == 0).astype(np.uint8), cv2.DIST_L2, 5) * L.res
        half_diag = math.hypot(*P.CLUTTER_SIZE_RANGE[1:] * 2) / 2
        eligible = edt >= half_diag + ctx.model.width + 0.15
        H, W = L.shape
        wp_arr = np.array(wps).reshape(-1, 2)
        lo, hi = P.CLUTTER_SIZE_RANGE
        for slot in range(n_clutter):
            # candidates are drawn over the whole grid extent, not indexed into the eligible-cell list, so a
            # change of the free floor elsewhere does not move this slot's box
            d = _stream(seed, _TAG_CLUTTER, slot).random((CLUTTER_CANDIDATES, 5))
            xs = L.origin[0] + d[:, 0] * W * L.res
            ys = L.origin[1] + d[:, 1] * H * L.res
            rr, cc = L.world_to_cell(xs, ys)
            ok = (rr >= 0) & (rr < H) & (cc >= 0) & (cc < W)
            ok[ok] = eligible[rr[ok], cc[ok]]
            if len(wp_arr):
                dmin = np.min(np.hypot(xs[:, None] - wp_arr[None, :, 0], ys[:, None] - wp_arr[None, :, 1]), axis=1)
                ok &= dmin >= P.CLUTTER_WAYPOINT_KEEPAWAY_M
            for j in np.nonzero(ok)[0]:
                hl, hw = 0.5 * (lo + (hi - lo) * d[j, 2]), 0.5 * (lo + (hi - lo) * d[j, 3])
                poly = transform(np.array([[hl, hw], [-hl, hw], [-hl, -hw], [hl, -hw]]), float(xs[j]), float(ys[j]),
                                 float(d[j, 4] * math.pi))
                if _blocked_count(occ_now, poly, L) > 0:
                    continue
                clutter.append(poly)
                break
    return polys + clutter, jitter, clutter


def _blocked_count(mask, poly, L) -> int:
    from .geometry import fill_polygons, polygon_bbox_rc
    r0, r1, c0, c1 = polygon_bbox_rc(poly, L.res, L.origin, L.shape[0], 1)
    r0, c0 = max(r0, 0), max(c0, 0)
    r1, c1 = min(r1, L.shape[0]), min(c1, L.shape[1])
    if r0 >= r1 or c0 >= c1:
        return 10 ** 6
    sub = np.zeros((r1 - r0, c1 - c0), np.uint8)
    fill_polygons(sub, [poly], L.res, L.origin, 1, offset_rc=(r0, c0), full_h=L.shape[0])
    return int((mask[r0:r1, c0:c1].astype(bool) & sub.astype(bool)).sum())


def nominal_geom(ctx: BatchContext) -> Geom:
    """Geometry with movable objects at their twin poses and no clutter (cached per context)."""
    g = ctx.plan_cache.get(("geom", "nominal"))
    if g is None:
        g = make_geom(ctx.base, ctx.layers.movable_polys())
        ctx.plan_cache[("geom", "nominal")] = g
    return g


# ---------------------------------------------------------------- planning a mission
def plan_mission(ctx: BatchContext, geom: Geom, cache_key=None):
    """-> (rails, None) or (None, reason)."""
    if cache_key is not None and cache_key in ctx.plan_cache:
        return ctx.plan_cache[cache_key]
    wp = {w.id: w for w in ctx.twin.waypoints}
    seq = ctx.task.sequence
    w0 = wp[seq[0]]
    pose = snap_pose(geom, w0.xy[0], w0.xy[1], w0.yaw)
    out = None
    if pose is None:
        out = (None, waypoint_check(geom, w0.id, w0.xy[0], w0.xy[1], w0.yaw)["message"])
    rails = []
    if out is None:
        for a, b in zip(seq[:-1], seq[1:]):
            g = snap_pose(geom, wp[b].xy[0], wp[b].xy[1], wp[b].yaw)
            if g is None:
                out = (None, waypoint_check(geom, b, wp[b].xy[0], wp[b].xy[1], wp[b].yaw)["message"])
                break
            pr: PlanResult = plan_route(geom, pose, (g[0], g[1]), geom.thr_block, ctx.cfg)
            if pr.status != "ok":
                out = (None, f"leg {a}->{b}: {pr.status}")
                break
            rails.append(pr.rail)
            r = pr.rail
            pose = (float(r.x[-1]), float(r.y[-1]), float(r.th[-1]))
        if out is None:
            out = (rails, None)
    if cache_key is not None:
        if len(ctx.plan_cache) > 64:
            ctx.plan_cache.clear()
        ctx.plan_cache[cache_key] = out
    return out


# ---------------------------------------------------------------- the mission state machine
def _r3(v):
    return round(float(v), 3)


class MissionSim:
    def __init__(self, ctx: BatchContext, geom: Geom, rails: list, rng: np.random.Generator, dt: float,
                 loop: bool = False, bottleneck: np.ndarray | None = None, timeout: float | None = None):
        self.ctx, self.geom, self.rails, self.rng = ctx, geom, rails, rng
        self.model = ctx.model
        self.fp = np.ascontiguousarray(self.model.footprint, dtype=np.float64)
        self.dt = dt
        self.loop = loop
        self.cfg = ctx.cfg
        self.leg = 0
        self.s = 0.0
        self.u = 0.0
        self.t = 0.0
        self.n_steps = 0             # time is n_steps * dt: no drift from summing dt
        self.blocked_steps = 0
        r = rails[0]
        self.x, self.y, self.th = float(r.x[0]), float(r.y[0]), float(r.th[0])
        self.v_lin = 0.0
        self.done = False
        self.outcome = None
        self.stopped = False
        self.blocked_cont = 0.0
        self.blocked_time = 0.0
        self.stops = 0
        self.near_misses = 0
        self.replans = 0
        self.path_len = 0.0
        self.min_margin = 1e9
        self.min_margin_pose = (self.x, self.y, self.th)
        self.min_ped_d = math.inf
        self.legs_done = 0
        self.bottleneck = bottleneck
        # nominal path time: the rails driven alone with this robot's speed profile (turn stops, acceleration);
        # length / top speed would hand fast robots on twisty routes a timeout they cannot meet without traffic
        nominal = sum(rail_drive_time(rr, self.cfg["accel"]) for rr in rails)
        self.nominal_time = nominal
        self.timeout = timeout if timeout is not None else max(self.cfg["timeout_factor"] * nominal, P.MIN_TIMEOUT_S)
        self.state = "moving"
        self.planned = list(rails)
        self.laps = 0
        self._src = self.planned       # rails of the current lap as planned (self.rails may hold replans)
        self._lap_rails = self._plan_lap(rails) if loop and len(rails) else list(rails)
        self.crowd = None
        rate = ctx.rate_per_min
        if rate > 0 or loop:
            nominal = nominal_geom(ctx)
            self.crowd = Crowd(geom.ped_blocked, geom.res, geom.origin, ctx.twin.traffic, rate,
                               ctx.zone_scale, rng, route_blocked=nominal.ped_blocked, route_cache=ctx.plan_cache)

    # -- helpers
    def robot_poly(self, x=None, y=None, th=None):
        return transform(self.fp, self.x if x is None else x, self.y if y is None else y,
                         self.th if th is None else th)

    def _cell(self, x, y):
        r, c = self.geom.layers.world_to_cell(x, y)
        return int(r), int(c)

    def _bump(self, val: float):
        if self.bottleneck is None:
            return
        r, c = self._cell(self.x, self.y)
        H, W = self.bottleneck.shape
        if 0 <= r < H and 0 <= c < W:
            if self.bottleneck.dtype.kind in "iu":   # exact integer milli-units (batch shards)
                self.bottleneck[r, c] += int(round(val * 1000))
            else:
                self.bottleneck[r, c] += val

    def warmup(self, seconds: float):
        if self.crowd is None or seconds <= 0:
            return
        poly = self.robot_poly()
        for _ in range(int(round(seconds / self.dt))):
            self.crowd.spawn(self.dt, (self.x, self.y), self.model.radius)
            self.crowd.step(self.dt, poly, (self.x, self.y), self.model.radius)
        self.crowd.peak = self.crowd.n_alive

    def _event(self, events, typ, detail=None, xy=None):
        e = {"type": typ, "xy": [_r3(self.x), _r3(self.y)] if xy is None else [_r3(xy[0]), _r3(xy[1])]}
        if detail is not None:
            e["detail"] = detail
        events.append(e)
        return e

    # -- one control step
    def step(self) -> list:
        events: list = []
        if self.done:
            return events
        dt = self.dt
        cfg = self.cfg
        rail = self.rails[self.leg]
        cr = self.crowd
        poly = self.robot_poly()
        idx = rail.index(self.s)
        rotating = bool(rail.rot[idx])
        backing = bool(rail.rev[idx])
        remaining = rail.s_end - self.s
        s_look = min(self.s + max(self.u, 0.05) * dt, rail.s_end)
        u_cap = float(np.interp(s_look, rail.s, rail.u_allow))
        u_hi = min(self.u + cfg["accel"] * dt, max(u_cap, 0.02 if remaining > 1e-6 else 0.0),
                   self.model.nominal_speed)
        u_lo = max(0.0, self.u - cfg["brake"] * dt)
        safety = False
        if cr is not None and cr.n_alive:
            d, ahead = robot_ped_relations(poly, self.x, self.y, self.th + (math.pi if backing else 0.0),
                                           cr.px, cr.py, cr.alive, P.PED_RADIUS_M)
            stop_d = self.model.safety_stop_m
            safety = bool(np.any(d < stop_d)) if rotating else bool(np.any((d < stop_d) & (ahead == 1)))
            if safety:
                u_new = u_lo
            else:
                u_new = choose_speed(rail.s, rail.x, rail.y, rail.th, self.s, u_lo, u_hi, self.fp, cr.px, cr.py,
                                     cr.vx, cr.vy, cr.alive, P.PED_RADIUS_M + P.DYN_MARGIN_M,
                                     P.CONTROL_HORIZON_S, dt, cfg["brake"], 6)
        else:
            u_new = u_hi
        s_new = min(self.s + u_new * dt, rail.s_end)
        x0, y0 = self.x, self.y
        self.x, self.y, self.th = rail.pose(s_new)
        moved = math.hypot(self.x - x0, self.y - y0)
        self.path_len += moved
        self.v_lin = -moved / dt if bool(rail.rev[rail.index(s_new)]) else moved / dt
        self.s = s_new
        self.u = u_new
        moving = u_new > 0.05
        poly = self.robot_poly()

        if cr is not None:
            cr.spawn(dt, (self.x, self.y), self.model.radius)
            over = cr.step(dt, poly, (self.x, self.y), self.model.radius)
            if cr.n_alive:
                d, _ = robot_ped_relations(poly, self.x, self.y, self.th, cr.px, cr.py, cr.alive, P.PED_RADIUS_M)
                al = cr.alive.astype(bool)
                self.min_ped_d = min(self.min_ped_d, float(d[al].min()))
                if moving and np.any(over.astype(bool) & al):
                    i = int(np.nonzero(over.astype(bool) & al)[0][0])
                    self._event(events, "collision", {"ped_xy": [_r3(cr.px[i]), _r3(cr.py[i])]})
                    self._finish(events, "collision")
                    return events
                if abs(self.v_lin) > P.NEAR_MISS_SPEED_MPS:
                    hit = np.nonzero(al & (d < P.NEAR_MISS_DIST_M) & (cr.armed == 1))[0]
                    for i in hit:
                        cr.armed[i] = 0
                        self.near_misses += 1
                        self._bump(1.0)
                        self._event(events, "near_miss", {"ped_xy": [_r3(cr.px[i]), _r3(cr.py[i])],
                                                          "distance_m": _r3(d[i])})
                rearm = al & (d > P.NEAR_MISS_DIST_M + 0.2)
                cr.armed[rearm] = 1

        r, c = self._cell(self.x, self.y)
        K = self.geom.margin.shape[0]
        k = int(math.floor(self.th / (2 * math.pi / K) + 0.5)) % K
        H, W = self.geom.layers.shape
        if 0 <= r < H and 0 <= c < W and self.geom.margin[k, r, c] < self.min_margin:
            self.min_margin = float(self.geom.margin[k, r, c])
            self.min_margin_pose = (self.x, self.y, self.th)

        want = self.s < rail.s_end - 1e-9
        if want and u_new < 0.01:
            if not self.stopped:
                self.stopped = True
                self.stops += 1
                self._bump(1.0)
                self._event(events, "stop", {"reason": "safety_stop" if safety else "yield"})
            self.blocked_time += dt
            self.blocked_steps += 1
            self.blocked_cont = self.blocked_steps * dt
            self._bump(dt)
            self.state = "stopped"
            if self.blocked_cont >= cfg["replan_after_s"] - 1e-9:
                self.blocked_steps = 0
                self.blocked_cont = 0.0
                self._replan(events)
        else:
            self.stopped = False
            self.blocked_steps = 0
            self.blocked_cont = 0.0
            self.state = "rotating" if bool(rail.rot[rail.index(self.s)]) and moving else "moving"

        if not want and self.u < 0.05:
            self.legs_done += 1
            self.leg += 1
            self.s = 0.0
            self.u = 0.0
            if self.leg >= len(self.rails):
                if self.loop:
                    self.leg = 0
                    self.laps += 1
                    self._src = self._lap_rails
                    self.rails = list(self._lap_rails)
                else:
                    self._tick()
                    self._event(events, "success", {"mission_time_s": round(self.t, 2)})
                    self._finish(events, "success", fail_event=False)
                    return events
            self._next_rail()
        self._tick()
        if not self.loop and self.t >= self.timeout - 1e-9:
            self._finish(events, "blocked_timeout")
        return events

    def _plan_lap(self, rails: list) -> list:
        """Rails for every lap after the first: an open task (last waypoint != first) drives back to the first
        waypoint, then the first leg starts from where the robot really is; the other legs chain as planned."""
        g = self.geom
        last, first = rails[-1], rails[0]
        end = (float(last.x[-1]), float(last.y[-1]), float(last.th[-1]))
        lap = []
        if math.hypot(end[0] - first.x[0], end[1] - first.y[0]) > 0.1:
            pr = plan_route(g, end, (float(first.x[0]), float(first.y[0])), g.thr_block, self.cfg)
            if pr.status == "ok" and pr.rail is not None and len(pr.rail.s) > 1:
                lap.append(pr.rail)
                end = (float(pr.rail.x[-1]), float(pr.rail.y[-1]), float(pr.rail.th[-1]))
        pr = plan_route(g, end, (float(first.x[-1]), float(first.y[-1])), g.thr_block, self.cfg)
        lap.append(pr.rail if pr.status == "ok" and pr.rail is not None and len(pr.rail.s) > 1 else first)
        return lap + list(rails[1:])

    def _tick(self):
        self.n_steps += 1
        self.t = self.n_steps * self.dt

    def _next_rail(self):
        """Start the next leg from where the robot actually stands. Planned legs chain exactly; after a replan (which
        ends within the goal tolerance, possibly at another heading) or when a loop wraps around, the planned rail
        would start elsewhere, so the leg is planned again from the current pose instead of jumping to its start."""
        nr = self._src[self.leg]
        if abs(nr.x[0] - self.x) + abs(nr.y[0] - self.y) > 1e-6 or abs(_wrap(nr.th[0] - self.th)) > 1e-6:
            pr = plan_route(self.geom, (self.x, self.y, self.th), (float(nr.x[-1]), float(nr.y[-1])),
                            self.geom.thr_block, self.cfg)
            if pr.status == "ok" and pr.rail is not None and len(pr.rail.s) > 1:
                nr = pr.rail
            else:
                self.x, self.y, self.th = float(nr.x[0]), float(nr.y[0]), float(nr.th[0])
        self.rails[self.leg] = nr

    def _replan(self, events):
        cr = self.crowd
        rail = self.rails[self.leg]
        goal = (float(rail.x[-1]), float(rail.y[-1]))
        extra = self.geom.cs.copy()
        if cr is not None and cr.n_alive:
            al = np.nonzero(cr.alive)[0]
            add_discs(extra, np.stack([cr.px[al], cr.py[al]], axis=1), P.PED_RADIUS_M + P.DYN_MARGIN_M,
                      self.geom.kern, self.geom.res, self.geom.origin)
        cfg = dict(self.cfg)
        cfg["max_nodes"] = min(int(cfg.get("max_nodes", P.PLANNER_MAX_NODES)), P.REPLAN_MAX_NODES)
        pr = plan_route(self.geom, (self.x, self.y, self.th), goal, self.geom.thr_block, cfg, extra_cs=extra)
        self.replans += 1
        ok = pr.status == "ok" and pr.rail is not None and len(pr.rail.s) > 1
        self._event(events, "replan", {"ok": ok})
        if ok:
            self.rails[self.leg] = pr.rail
            self.s = 0.0
            self.u = 0.0

    def _finish(self, events, outcome, fail_event=True):
        self.done = True
        self.outcome = outcome
        self.state = "done" if outcome == "success" else "failed"
        if fail_event:
            self._event(events, "fail", {"reason": outcome})

    def frame_line(self, ep: int | None, events: list) -> str:
        d = {"t": round(self.t, 2), "robot": [_r3(self.x), _r3(self.y), _r3(_wrap(self.th)), _r3(self.v_lin)],
             "peds": self.crowd.positions() if self.crowd is not None else [], "events": events}
        if ep is not None:
            d = {"episode": ep, **d}
        return json.dumps(d, separators=(",", ":"))

    def summary(self) -> dict:
        gap = exact_gap(self.geom, *self.min_margin_pose) if self.min_margin < 1e9 else None
        return {
            "outcome": self.outcome,
            "mission_time_s": round(self.t, 3) if self.outcome == "success" else None,
            "elapsed_s": round(self.t, 3),
            "path_length_m": round(self.path_len, 4),
            "nominal_time_s": round(self.nominal_time, 3),
            "stops": self.stops,
            "near_misses": self.near_misses,
            "replans": self.replans,
            "blocked_time_s": round(self.blocked_time, 2),
            "min_gap_m": None if gap is None else round(gap, 4),
            "min_gap_xy": [_r3(self.min_margin_pose[0]), _r3(self.min_margin_pose[1])],
            "min_ped_distance_m": None if not math.isfinite(self.min_ped_d) else round(self.min_ped_d, 3),
            "peds_peak": self.crowd.peak if self.crowd is not None else 0,
            "legs_done": self.legs_done,
        }


def _wrap(a: float) -> float:
    return (a + math.pi) % (2 * math.pi) - math.pi


# ---------------------------------------------------------------- run one episode
def run_episode(ctx: BatchContext, ep: int, seed: int, bottleneck: np.ndarray | None, record: bool):
    ped_rng = np.random.default_rng(np.random.SeedSequence(int(seed) & 0xFFFFFFFFFFFFFFFF).spawn(2)[1])
    polys, jitter, clutter = episode_variation(ctx, seed)
    cache_key = "nominal" if not jitter and not clutter else None
    geom = ctx.plan_cache.get(("geom", cache_key)) if cache_key else None
    if geom is None:
        geom = make_geom(ctx.base, polys)
        if cache_key:
            ctx.plan_cache[("geom", cache_key)] = geom
    rails, reason = plan_mission(ctx, geom, cache_key)
    lines: list[str] = []
    base = {"episode": ep, "seed": int(seed), "n_clutter": len(clutter), "n_jittered": len(jitter)}
    if rails is None:
        if record:
            wpf = {w.id: w for w in ctx.twin.waypoints}[ctx.task.sequence[0]]
            ev = [{"type": "fail", "xy": [_r3(wpf.xy[0]), _r3(wpf.xy[1])],
                   "detail": {"reason": "no_path_static", "why": reason}}]
            lines.append(json.dumps({"episode": ep, "t": 0.0, "robot": [_r3(wpf.xy[0]), _r3(wpf.xy[1]), _r3(wpf.yaw), 0.0],
                                     "peds": [], "events": ev}, separators=(",", ":")))
        return {**base, "outcome": "no_path_static", "why": reason, "mission_time_s": None, "elapsed_s": 0.0,
                "path_length_m": 0.0, "stops": 0, "near_misses": 0, "replans": 0, "blocked_time_s": 0.0,
                "min_gap_m": None, "min_ped_distance_m": None, "peds_peak": 0, "legs_done": 0}, lines
    rails = list(rails)
    sim = MissionSim(ctx, geom, rails, ped_rng, ctx.cfg["dt"], bottleneck=bottleneck)
    sim.warmup(ctx.cfg.get("warmup_s", P.PED_WARMUP_S))
    if record:
        lines.append(sim.frame_line(ep, []))
    while not sim.done:
        ev = sim.step()
        if record:
            lines.append(sim.frame_line(ep, ev))
    return {**base, **sim.summary()}, lines
