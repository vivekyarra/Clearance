"""Static feasibility per task leg (PLAN 7.7): reachability, tightest point, thresholds."""
from __future__ import annotations

import numpy as np

from clearance_core.schemas import Feasibility, LegFeasibility, RobotSpec, Task, Twin

from .episode import make_context
from .routing import make_geom, plan_route, point_route_gap, rail_gap_profile, snap_pose, waypoint_check


def _r(v, n=4):
    return None if v is None else round(float(v), n)


def check_feasibility(twin: Twin, robot: RobotSpec, task: Task, bound: str = "nominal", *,
                      overrides: dict | None = None, p90_err_m: float | None = None,
                      sim_config: dict | None = None) -> Feasibility:
    ctx = make_context(twin, robot, task, "none", bound, overrides, sim_config, p90_err_m)
    geom = ctx.plan_cache.get(("geom", "nominal"))
    if geom is None:
        geom = make_geom(ctx.base, ctx.layers.movable_polys())
        ctx.plan_cache[("geom", "nominal")] = geom
    model = ctx.model
    wp = {w.id: w for w in twin.waypoints}
    thr = {t.id: t for t in twin.thresholds}
    both = (geom.thr_block | geom.thr_unknown).astype(np.uint8)
    none = np.zeros_like(geom.thr_block)
    legs: list[LegFeasibility] = []
    pose = None
    for a, b in zip(task.sequence[:-1], task.sequence[1:]):
        wa, wb = wp[a], wp[b]
        start = pose or snap_pose(geom, wa.xy[0], wa.xy[1], wa.yaw)
        goal = snap_pose(geom, wb.xy[0], wb.xy[1], wb.yaw)
        leg = LegFeasibility(from_wp=a, to_wp=b, reachable=False, required_gap_m=_r(model.width))
        pose = None
        if start is None or goal is None:
            bad = wa if start is None else wb
            diag = waypoint_check(geom, bad.id, bad.xy[0], bad.xy[1], bad.yaw)
            leg.reason = diag["message"]
            leg.narrowest_gap_m = diag["gap_m"]
            leg.narrowest_xy = (_r(bad.xy[0], 3), _r(bad.xy[1], 3))
            legs.append(leg)
            continue
        res_ok = plan_route(geom, start, goal[:2], both, ctx.cfg)
        chosen, unknown_hit, blocked_hit = None, None, None
        if res_ok.status == "ok":
            chosen = res_ok
            leg.reachable = True
        elif res_ok.status == "search_limit":
            leg.reachable = None
            leg.reason = "planner search limit reached; reachability unknown"
        else:
            via_unknown = plan_route(geom, start, goal[:2], geom.thr_block, ctx.cfg) if geom.thr_unknown.any() else None
            if via_unknown is not None and via_unknown.status == "ok":
                chosen = via_unknown
                leg.reachable = None
                unknown_hit = next((t for t in via_unknown.crossed if geom.thr_class.get(t) == "unknown"), None)
            elif geom.thr_block.any():
                via_any = plan_route(geom, start, goal[:2], none, ctx.cfg)
                if via_any.status == "ok":
                    chosen = via_any
                    leg.reachable = False
                    blocked_hit = next((t for t in via_any.crossed if geom.thr_class.get(t) == "block"), None)
        if chosen is not None:
            rail = chosen.rail
            prof = rail_gap_profile(geom, rail)
            if prof:
                leg.narrowest_gap_m, leg.required_gap_m, xy = _r(prof[0]), _r(prof[1]), prof[2]
                leg.narrowest_xy = (_r(xy[0], 3), _r(xy[1], 3))
            leg.path_length_m = _r(rail.length_m, 3)
            if leg.reachable:
                pose = (float(rail.x[-1]), float(rail.y[-1]), float(rail.th[-1]))
        if unknown_hit:
            t = thr[unknown_hit]
            leg.blocking_threshold = unknown_hit
            if t.status == "flagged_manual" or t.height_m is None:
                leg.reason = f"route crosses threshold {unknown_hit}: height to be measured on site"
            else:
                leg.reason = (f"route crosses threshold {unknown_hit} ({t.height_m:.3f} m); robot threshold "
                              f"limit awaiting a sourced value")
        elif blocked_hit:
            t = thr[blocked_hit]
            leg.blocking_threshold = blocked_hit
            leg.reason = (f"threshold {blocked_hit} is {t.height_m:.3f} m, above the robot limit "
                          f"{model.max_threshold_m:.3f} m")
        elif chosen is None and leg.reachable is False:
            pg = point_route_gap(geom, start[:2], goal[:2])
            if pg is None:
                leg.reason = "no free route between the waypoints (walls or unscanned areas)"
            else:
                w, xy = pg
                leg.narrowest_gap_m = _r(w)
                leg.narrowest_xy = (_r(xy[0], 3), _r(xy[1], 3))
                if w < model.width:
                    leg.reason = f"no path: narrowest gap {w:.2f} m vs {model.width:.2f} m needed"
                else:
                    # The footprint fits the gap driving straight but cannot turn where the route bends: the space
                    # that binds here is the turning diameter (2 x circumradius about the rotation center).
                    leg.required_gap_m = _r(2 * model.radius)
                    leg.reason = (f"no path: narrowest gap {w:.2f} m; turning this footprint there needs "
                                  f"{2 * model.radius:.2f} m")
        legs.append(leg)
    vals = [l.reachable for l in legs]
    allr = False if any(v is False for v in vals) else (None if any(v is None for v in vals) else True)
    return Feasibility(robot_id=robot.robot_id, twin_id=twin.twin_id, twin_version=twin.version, legs=legs,
                       all_reachable=allr)


def check_waypoints(twin: Twin, robot: RobotSpec, task: Task | None = None, bound: str = "nominal", *,
                    overrides: dict | None = None, p90_err_m: float | None = None) -> list[dict]:
    """Placement check for every waypoint (or the task's): does the robot footprint have a collision-free pose
    there at some heading? Each entry is routing.waypoint_check's dict; `message` names the waypoint, the obstacle
    it is too close to and the free width there, e.g. "stop point T1 is too close to TABLE1 (table) ..."."""
    seq = list(dict.fromkeys(task.sequence)) if task is not None else [w.id for w in twin.waypoints]
    t = task if task is not None else Task(sequence=seq if len(seq) >= 2 else seq * 2)
    ctx = make_context(twin, robot, t, "none", bound, overrides, None, p90_err_m)
    geom = ctx.plan_cache.get(("geom", "nominal"))
    if geom is None:
        geom = make_geom(ctx.base, ctx.layers.movable_polys())
        ctx.plan_cache[("geom", "nominal")] = geom
    wp = {w.id: w for w in twin.waypoints}
    return [waypoint_check(geom, i, wp[i].xy[0], wp[i].xy[1], wp[i].yaw) for i in seq]
