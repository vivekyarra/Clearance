"""Candidate objects, candidate moves and human-readable descriptions (PLAN 7.8).

Move generation and pruning, per object:
  * Lattice: every (dx, dy) on a `step_m` grid with 0 < |d| <= `max_shift_m` (112 offsets for
    5 cm / 0.30 m), each combined with every rotation in `rotations_deg` (default 0, +15, -15),
    plus pure rotations. Rotations are skipped for cylinders (no-ops).
  * Direction cone: when bottleneck peaks are near the object, only translations within +-67.5 deg
    of the "away from the bottleneck" vector (bottleneck-weighted sum of unit vectors from each
    peak to the object centre) are kept. Moving toward the pinch cannot widen it.
  * Geometric gain: gain = sum_k w_k * (dist(new footprint, peak_k) - dist(old footprint, peak_k)),
    w_k = peak value / max peak value. Moves with negative gain (they narrow the pinch) are dropped.
  * Stratified cap: moves are bucketed by displacement (one bucket per 5 cm ring, one bucket for
    pure rotations), sorted by gain inside each bucket, and drawn round-robin across buckets until
    `max_moves` admissible moves are collected. This keeps small "minimal disruption" moves and the
    largest ones in the pool instead of only the 40 largest shifts.
  * Constraints (constraints.ConstraintChecker) are evaluated lazily in that order, so the
    expensive egress check runs only for moves that would actually be simulated.
"""
from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np
from shapely import affinity
from shapely.geometry import Point

from clearance_core.grid import Grid
from clearance_core.schemas import Twin

from .constraints import ConstraintChecker
from .geometry import primitive_polygon


@dataclass(frozen=True)
class Peak:
    x: float
    y: float
    value: float
    row: int
    col: int


def bottleneck_peaks(bottleneck: np.ndarray, grid: Grid, k: int = 3, min_sep_m: float = 0.5) -> list[Peak]:
    """Top-k bottleneck cells with non-maximum suppression (peaks at least min_sep_m apart), so
    three adjacent cells of one pinch do not crowd out a second pinch elsewhere."""
    b = np.nan_to_num(np.asarray(bottleneck, dtype=float), nan=0.0, posinf=0.0, neginf=0.0)
    if b.shape != grid.data.shape:
        raise ValueError(f"bottleneck shape {b.shape} != occupancy shape {grid.data.shape}")
    flat = b.ravel()
    order = np.argsort(-flat, kind="stable")
    peaks: list[Peak] = []
    w = b.shape[1]
    for idx in order:
        v = flat[idx]
        if v <= 0 or len(peaks) >= k:
            break
        r, c = divmod(int(idx), w)
        x, y = grid.cell_to_world(r, c)
        if all(math.hypot(x - p.x, y - p.y) >= min_sep_m for p in peaks):
            peaks.append(Peak(float(x), float(y), float(v), r, c))
    return peaks


def candidate_objects(twin: Twin, peaks: list[Peak], radius_m: float = 1.5,
                      hint_ids: list[str] | None = None) -> tuple[list[dict], list[str]]:
    """Movable primitives within radius_m of any peak; hinted objects first (in hint order).
    Returns (candidates, skipped_hints) where each candidate is {"id", "hint", "dist_m"}."""
    prims = {p.id: p for p in twin.primitives}
    out, seen, skipped = [], set(), []

    def dist(pid):
        poly = primitive_polygon(prims[pid])
        return min((poly.distance(Point(p.x, p.y)) for p in peaks), default=None)

    for hid in hint_ids or []:
        if hid in seen:
            continue
        if hid not in prims or not prims[hid].movable:
            skipped.append(hid)
            continue
        seen.add(hid)
        out.append({"id": hid, "hint": True, "dist_m": dist(hid)})
    near = []
    for p in twin.primitives:
        if p.movable and p.id not in seen:
            d = dist(p.id)
            if d is not None and d <= radius_m:
                near.append({"id": p.id, "hint": False, "dist_m": d})
    near.sort(key=lambda c: (c["dist_m"], c["id"]))
    return out + near, skipped


def _away_vector(cx, cy, peaks, weights):
    v = np.zeros(2)
    for p, wt in zip(peaks, weights):
        d = np.array([cx - p.x, cy - p.y])
        n = np.linalg.norm(d)
        if n > 1e-9:
            v += wt * d / n
    n = np.linalg.norm(v)
    return v / n if n > 1e-6 else None


def generate_moves(checker: ConstraintChecker, obj_id: str, peaks: list[Peak], *, step_m=0.05, max_shift_m=0.30,
                   rotations_deg=(0.0, 15.0, -15.0), max_moves=40, radius_m=1.5,
                   cone_deg=67.5) -> tuple[list[dict], dict[str, int]]:
    """Admissible moves for one object, best-first per the module docstring.
    Returns (moves, rejected_counts); each move is {"dx","dy","dyaw","gain"}."""
    prim = checker.prims[obj_id]
    base = checker.base_polys[obj_id]
    cx, cy = prim.pose.xyz[0], prim.pose.xyz[1]
    near = [p for p in peaks if base.distance(Point(p.x, p.y)) <= radius_m]
    vmax = max((p.value for p in near), default=1.0)
    weights = [p.value / vmax for p in near]
    away = _away_vector(cx, cy, near, weights)
    cos_cone = math.cos(math.radians(cone_deg))
    rots = [0.0] if prim.type == "cylinder" else sorted({float(r) for r in rotations_deg}, key=lambda r: (abs(r), -r))
    n = int(round(max_shift_m / step_m))
    offsets = []
    for i in range(-n, n + 1):
        for j in range(-n, n + 1):
            if i * i + j * j > n * n + 1e-9:
                continue
            dx, dy = round(i * step_m, 4), round(j * step_m, 4)
            if (i or j) and away is not None and (dx * away[0] + dy * away[1]) / math.hypot(dx, dy) < cos_cone - 1e-9:
                continue
            offsets.append((dx, dy, i * i + j * j))

    def gain(poly):
        return sum(wt * (poly.distance(Point(p.x, p.y)) - base.distance(Point(p.x, p.y)))
                   for p, wt in zip(near, weights))

    buckets: dict[int, list] = {}
    for dx, dy, r2 in offsets:
        for rot in rots:
            if r2 == 0 and rot == 0.0:
                continue
            dyaw = math.radians(rot)
            g = gain(primitive_polygon(prim, dx, dy, dyaw)) if near else 0.0
            if near and g < -1e-9:
                continue
            ring = int(math.ceil(math.sqrt(r2) - 1e-9))  # 0 = pure rotation, 1..n = 5 cm rings
            buckets.setdefault(ring, []).append((-round(g, 3), r2, abs(rot), -rot, dx, dy, dyaw, g))
    for b in buckets.values():
        b.sort(key=lambda t: t[:6])
    order = sorted(buckets)
    rejected: dict[str, int] = {}
    moves: list[dict] = []
    cursors = {k: 0 for k in order}
    while len(moves) < max_moves and any(cursors[k] < len(buckets[k]) for k in order):
        for k in order:
            if len(moves) >= max_moves:
                break
            if cursors[k] >= len(buckets[k]):
                continue
            *_, dx, dy, dyaw, g = buckets[k][cursors[k]]
            cursors[k] += 1
            res = checker.check({obj_id: (dx, dy, dyaw)})
            if res.ok:
                moves.append({"dx": dx, "dy": dy, "dyaw": round(dyaw, 6), "gain": round(g, 4)})
            else:
                key = res.reason.split(":")[0]
                rejected[key] = rejected.get(key, 0) + 1
    return moves, rejected


# ---------- descriptions ----------

_COMPASS = ["east", "north-east", "north", "north-west", "west", "south-west", "south", "south-east"]


def compass(dx: float, dy: float) -> str:
    ang = math.degrees(math.atan2(dy, dx)) % 360.0
    return _COMPASS[int(((ang + 22.5) % 360.0) // 45.0)]


def _first_hit(checker: ConstraintChecker, obj_id: str, ux: float, uy: float,
               reach_m: float = 2.0) -> tuple[str, float] | None:
    """Sweep the object's footprint along (ux, uy) in 5 cm steps; return ('wall' or the label of the
    first object reached, distance) or None if nothing is reached within reach_m."""
    base = checker.base_polys[obj_id]
    touching = {oid for oid, p in checker.base_polys.items() if oid != obj_id and p.distance(base) < 1e-6}
    wall0 = checker.wall_area(base) + checker._wall_tol
    for s in range(1, int(round(reach_m / 0.05)) + 1):
        moved = affinity.translate(base, ux * 0.05 * s, uy * 0.05 * s)
        for oid, p in checker.base_polys.items():
            if oid != obj_id and oid not in touching and moved.intersects(p):
                return checker.prims[oid].label or oid, 0.05 * s
        if not checker.floor.contains(moved) or checker.wall_area(moved) > wall0:
            return "wall", 0.05 * s
    return None


def describe_move(checker: ConstraintChecker, obj_id: str, dx: float, dy: float, dyaw: float) -> str:
    """'Move T4 0.20 m toward wall (east)'. The reference is whichever neighbour (wall or labelled
    object) is nearer: the one ahead ("toward") or the one behind ("away from")."""
    parts = []
    dist = math.hypot(dx, dy)
    if dist > 1e-6:
        ux, uy = dx / dist, dy / dist
        word = compass(dx, dy)
        ahead = _first_hit(checker, obj_id, ux, uy)
        behind = _first_hit(checker, obj_id, -ux, -uy)
        if ahead and (not behind or ahead[1] <= behind[1]):
            phrase = f"toward {ahead[0]} ({word})"
        elif behind:
            phrase = f"away from {behind[0]} ({word})"
        else:
            phrase = word
        parts.append(f"Move {obj_id} {dist:.2f} m {phrase}")
    if abs(dyaw) > 1e-9:
        sense = "counter-clockwise" if dyaw > 0 else "clockwise"
        deg = abs(math.degrees(dyaw))
        parts.append(f"{'rotate' if parts else 'Rotate ' + obj_id} {deg:.0f}° {sense}")
    return " and ".join(parts) if parts else f"Keep {obj_id}"
