"""Code-first candidate selection: static filters before any simulation or LLM.

Filters: human-verified only (unless include_awaiting_review=True, which admits specs awaiting review and marks
them "awaiting review"); footprint published; robot class (optional); payload vs task; footprint width vs the
narrowest gap the route must pass (maximin-gap path per task leg on the nominal grid); thresholds above the robot's
limit that no route can avoid. Operator-confirmed clear zones (Twin.clear_zones) open unseen floor cells
before the route-gap pre-screen, exactly as the simulator does. The gap filter is a coarse pre-screen with a one-cell tolerance; static feasibility
(jobs/sim check_feasibility) is the authority afterwards."""
from __future__ import annotations

import numpy as np

from clearance_core.grid import Grid
from clearance_core.schemas import RobotSpec, Task, Twin

from agents.investigate.tools import robot_width_m


def _disc_seeds(grid: Grid, xy, radius_m: float) -> np.ndarray:
    H, W = grid.data.shape
    r0, c0 = (int(v) for v in grid.world_to_cell(xy[0], xy[1]))
    k = max(0, int(round(radius_m / grid.res)))
    rr, cc = np.mgrid[-k:k + 1, -k:k + 1]
    m = rr ** 2 + cc ** 2 <= k ** 2
    rr, cc = rr[m] + r0, cc[m] + c0
    ok = (rr >= 0) & (rr < H) & (cc >= 0) & (cc < W)
    out = np.zeros((H, W), bool)
    out[rr[ok], cc[ok]] = True
    return out


def _connected(mask: np.ndarray, a: np.ndarray, b: np.ndarray) -> bool:
    from scipy.ndimage import label
    if not (mask & a).any() or not (mask & b).any():
        return False
    lab, _ = label(mask, structure=np.ones((3, 3), int))
    la = set(np.unique(lab[mask & a])) - {0}
    lb = set(np.unique(lab[mask & b])) - {0}
    return bool(la & lb)


def threshold_mask(grid: Grid, twin: Twin, limit_m: float | None) -> tuple[np.ndarray, list[str]]:
    """Cells on thresholds (measured / user_entered) higher than the robot's limit."""
    import cv2
    m = np.zeros(grid.data.shape, np.uint8)
    ids = []
    if limit_m is None:
        return m.astype(bool), ids
    for t in twin.thresholds:
        if t.status == "flagged_manual" or t.height_m is None or t.height_m <= limit_m:
            continue
        (x1, y1), (x2, y2) = t.segment
        r1, c1 = grid.world_to_cell(x1, y1)
        r2, c2 = grid.world_to_cell(x2, y2)
        cv2.line(m, (int(c1), int(r1)), (int(c2), int(r2)), 1, thickness=2)
        ids.append(t.id)
    return m.astype(bool), ids


def route_gaps(grid: Grid, twin: Twin, task: Task, *, extra_blocked: np.ndarray | None = None,
               endpoint_slack_m: float = 0.3, iters: int = 24) -> list[dict]:
    """Per task leg: the widest gap any path must squeeze through (2 x maximin distance-to-obstacle), in metres."""
    from scipy.ndimage import distance_transform_edt
    free = ~grid.blocked()
    if extra_blocked is not None:
        free &= ~extra_blocked
    d = distance_transform_edt(free) * grid.res
    wps = {w.id: w for w in twin.waypoints}
    out = []
    for a, b in zip(task.sequence, task.sequence[1:]):
        leg = {"from": a, "to": b, "gap_m": None}
        if a not in wps or b not in wps:
            leg["note"] = "waypoint missing in twin"
            out.append(leg)
            continue
        sa, sb = _disc_seeds(grid, wps[a].xy, endpoint_slack_m), _disc_seeds(grid, wps[b].xy, endpoint_slack_m)
        lo, hi = 0.0, float(d.max())
        if not _connected(d > lo, sa, sb):
            leg["gap_m"] = 0.0
            out.append(leg)
            continue
        for _ in range(iters):
            mid = (lo + hi) / 2
            if _connected(d >= mid, sa, sb):
                lo = mid
            else:
                hi = mid
        leg["gap_m"] = round(2 * lo, 4)
        out.append(leg)
    return out


def with_clear_zones(grid: Grid, twin: Twin) -> tuple[Grid, dict | None]:
    """Grid with UNKNOWN -> FREE inside the twin's clear zones (idempotent: a grid from rasterize_twin already
    has them) plus a summary for the analysis notes."""
    if not twin.clear_zones:
        return grid, None
    from jobs.sim.raster import apply_clear_zones, clear_zone_summary
    summary = clear_zone_summary(twin, grid)
    cells, _ = apply_clear_zones(grid.data, twin.clear_zones, grid.res, grid.origin)
    return (grid if cells is grid.data else Grid(cells, grid.res, grid.origin)), summary


def _summary_without_grid(twin: Twin) -> dict | None:
    if not twin.clear_zones:
        return None
    from jobs.sim.raster import clear_zone_summary
    return clear_zone_summary(twin)


def clear_zone_note(summary: dict | None) -> str | None:
    if not summary:
        return None
    who = ", ".join(summary["confirmed_by"]) if summary["confirmed_by"] else "the operator"
    n = summary["count"]
    return (f"operator-confirmed clear floor: {n} zone{'s' if n != 1 else ''}, "
            f"{summary['area_m2']:.2f} m² total, confirmed by {who}; unseen cells inside count as free floor, "
            f"measured obstacles are unchanged")


def static_filter(twin: Twin, robots: list[RobotSpec], task: Task, business: dict | None = None, *,
                  grid: Grid | None = None, max_robots: int = 6, include_awaiting_review: bool = False) -> dict:
    business = business or {}
    candidates, excluded, notes = [], [], []
    if grid is not None:
        grid, cz = with_clear_zones(grid, twin)
    else:
        cz = _summary_without_grid(twin)
    if clear_zone_note(cz):
        notes.append(clear_zone_note(cz))
    base_gaps = route_gaps(grid, twin, task) if grid is not None else None
    if grid is None:
        notes.append("geometry pre-screen skipped: no occupancy grid available")
    flagged = [t.id for t in twin.thresholds if t.status == "flagged_manual"]
    need_payload = business.get("payload_kg")
    want_class = business.get("robot_class")
    for r in robots:
        def drop(rule, reason):
            excluded.append({"robot_id": r.robot_id, "rule": rule, "reason": reason})
        if not r.verified_by_human and not include_awaiting_review:
            drop("verified_by_human", "spec awaiting human review")
            continue
        spec_status = "human-reviewed" if r.verified_by_human else "awaiting review"
        w = robot_width_m(r)
        if w is None:
            drop("footprint", "footprint not published in the reviewed spec")
            continue
        if want_class and r.robot_class != want_class:
            drop("class", f"robot class {r.robot_class} does not match requested {want_class}")
            continue
        rnotes = [] if r.verified_by_human else ["catalog specs awaiting review"]
        if need_payload is not None:
            if r.payload_kg is None:
                rnotes.append("payload not published")
            elif r.payload_kg < float(need_payload):
                drop("payload", "payload below the task requirement")
                continue
        route_gap = margin = None
        if base_gaps is not None:
            gaps = [g["gap_m"] for g in base_gaps if g["gap_m"] is not None]
            route_gap = min(gaps) if gaps else None
            tol = 2 * grid.res
            if route_gap is not None:
                margin = route_gap - w
                if margin < -tol:
                    drop("footprint_vs_route_gap", "footprint wider than the narrowest gap on the route")
                    continue
            tmask, tids = threshold_mask(grid, twin, r.max_threshold_m)
            if tids:
                g2 = route_gaps(grid, twin, task, extra_blocked=tmask)
                g2v = [g["gap_m"] for g in g2 if g["gap_m"] is not None]
                if g2v and min(g2v) - w < -tol:
                    drop("threshold", f"every route crosses threshold(s) {', '.join(tids)} above the robot's limit")
                    continue
            if r.max_threshold_m is None and any(t.status != "flagged_manual" and t.height_m for t in
                                                  twin.thresholds):
                rnotes.append("threshold limit not published; checked in feasibility")
        if flagged:
            rnotes.append("threshold(s) " + ", ".join(flagged) + ": measure on site")
        candidates.append({"robot_id": r.robot_id, "vendor": r.vendor, "model": r.model, "class": r.robot_class,
                           "width_m": round(w, 4), "route_gap_m": route_gap, "spec_status": spec_status,
                           "gap_margin_m": None if margin is None else round(margin, 4), "notes": rnotes})
    candidates.sort(key=lambda c: (-(c["gap_margin_m"] if c["gap_margin_m"] is not None else -1e9), c["robot_id"]))
    for c in candidates[max_robots:]:
        excluded.append({"robot_id": c["robot_id"], "rule": "budget", "reason": f"budget allows {max_robots} robots"})
    return {"candidates": candidates[:max_robots], "excluded": excluded, "route_gaps": base_gaps, "notes": notes}
