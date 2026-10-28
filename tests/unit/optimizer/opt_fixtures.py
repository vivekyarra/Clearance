"""Reference pinch room + fast deterministic scripted runner (test double) for optimizer tests.

Room 6 m x 3 m (walls 0.1 m thick, grid 5 cm). Two pinch points:
  P = (3.0, 0.35): gap between table T4 (0.8 m square, y 0.7..1.5) and the south wall (0.70 m).
  Q = (1.6, 2.85): gap between chair C1 (0.45 m square, y 2.175..2.625) and the north wall (0.375 m).
Scripted success probability = p1(gap_P) * p2(gap_Q); known-good fix: T4 north >= 0.20 m and C1 south.
"""
from __future__ import annotations

import hashlib
import json
import math
import threading

import numpy as np

from jobs.optimizer.geometry import move_of, paint, primitive_polygon
from clearance_core.grid import FREE, OCC, Grid
from clearance_core.schemas import (BatchResults, Floor, KeepoutZone, Occupancy, Pose2, Primitive, RobotSpec, SimBatch,
                            Task, Twin, Waypoint)

RES = 0.05
ORIGIN = (-0.1, -0.1)
H, W = 64, 124  # 3.2 m x 6.2 m including walls
P = (3.0, 0.35)
Q = (1.6, 2.85)


def walls_grid() -> Grid:
    data = np.full((H, W), OCC, np.uint8)
    data[2:-2, 2:-2] = FREE  # interior [0, 6] x [0, 3]
    return Grid(data, RES, ORIGIN)


def box(pid, x, y, l, w, label, movable=True, yaw=0.0, h=0.75):
    return Primitive(id=pid, type="box", label=label, pose=Pose2(xyz=(x, y, h / 2), yaw=yaw), size=(l, w, h),
                     movable=movable, source="reference")


def make_twin(with_bounds=True) -> Twin:
    return Twin(
        twin_id="twin_pinch", site_id="site_pinch", version=1, geometry_source="reference",
        floor=Floor(bounds_polygon=[(0, 0), (6, 0), (6, 3), (0, 3)] if with_bounds else []),
        occupancy=Occupancy(uri="sites/site_pinch/twin_v1/occ.png", resolution_m=RES, origin_xy=ORIGIN,
                            width=W, height=H),
        primitives=[
            box("T4", 3.0, 1.1, 0.8, 0.8, "table"),
            box("C1", 1.6, 2.4, 0.45, 0.45, "chair"),
            box("CNT", 5.0, 2.6, 1.6, 0.6, "counter", movable=False, h=1.0),
            Primitive(id="S1", type="cylinder", label="stool", pose=Pose2(xyz=(5.0, 1.9, 0.35)), size=(0.4, 0.4, 0.7),
                      movable=True, source="reference"),
        ],
        keepout_zones=[KeepoutZone(id="K1", polygon=[(3.5, 1.0), (4.0, 1.0), (4.0, 2.0), (3.5, 2.0)], reason="kitchen")],
        waypoints=[
            Waypoint(id="DOCK", xy=(0.5, 0.35), kind="dock"),
            Waypoint(id="DEST", xy=(5.5, 0.35), kind="dest"),
            Waypoint(id="ENT", xy=(0.3, 2.0), kind="entrance"),
            Waypoint(id="EXIT", xy=(5.7, 1.0), kind="exit"),
        ],
    )


def make_robot() -> RobotSpec:
    return RobotSpec(robot_id="test_bot", **{"class": "A_service"}, vendor="Test", model="Bot",
                     footprint_polygon_m=[(0.25, 0.25), (-0.25, 0.25), (-0.25, -0.25), (0.25, -0.25)])


def make_task() -> Task:
    return Task(sequence=["DOCK", "DEST", "DOCK"])


def make_bottleneck(grid: Grid) -> np.ndarray:
    b = np.zeros((H, W), np.float32)
    for (x, y), v in ((P, 10.0), (Q, 6.0), ((5.0, 1.0), 1.0)):
        r, c = grid.world_to_cell(x, y)
        b[r - 1:r + 2, c - 1:c + 2] = v * 0.5
        b[r, c] = v
    return b


def scripted_rasterize(twin: Twin, bound="nominal", p90_err_m=None, overrides=None) -> Grid:
    g = walls_grid()
    ov = overrides or {}
    removed = set(ov.get("remove") or [])
    mask = g.data != FREE
    polys = [primitive_polygon(p, *move_of(ov, p.id)) for p in twin.primitives if p.id not in removed]
    polys += [primitive_polygon(Primitive.model_validate(b) if isinstance(b, dict) else b)
              for b in ov.get("add_boxes") or []]
    paint(mask, polys, RES, ORIGIN, True)
    if bound == "conservative":  # obstacles grow by one cell (5 cm)
        from scipy import ndimage
        mask = ndimage.binary_dilation(mask)
    return Grid(np.where(mask, OCC, FREE).astype(np.uint8), RES, ORIGIN)


def column_gap(grid: Grid, xy) -> float:
    r, c = grid.world_to_cell(*xy)
    free = grid.data[:, c] == FREE
    if not free[r]:
        return 0.0
    top = r
    while top > 0 and free[top - 1]:
        top -= 1
    bot = r
    while bot < len(free) - 1 and free[bot + 1]:
        bot += 1
    return (bot - top + 1) * grid.res


def success_prob(grid: Grid) -> float:
    p1 = min(max((column_gap(grid, P) - 0.6) / 0.3, 0.05), 0.97)
    p2 = min(max((column_gap(grid, Q) - 0.3) / 0.3, 0.3), 1.0)
    return p1 * p2


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return 0.0, 1.0
    ph = k / n
    d = 1 + z * z / n
    centre = (ph + z * z / (2 * n)) / d
    half = z * math.sqrt(ph * (1 - ph) / n + z * z / (4 * n * n)) / d
    return max(0.0, centre - half), min(1.0, centre + half)


class ScriptedRunner:
    """Signature-compatible with jobs.sim.api.run_batch; records every call (thread safe)."""

    def __init__(self):
        self.calls: list[dict] = []
        self._lock = threading.Lock()

    def __call__(self, twin, robot, task, density, bound, n_episodes, seed_base, *, overrides=None,
                 record_episodes=True, out_prefix=None) -> SimBatch:
        with self._lock:
            self.calls.append({"n": n_episodes, "seed_base": seed_base, "overrides": overrides,
                               "density": density, "bound": bound})
        p = success_prob(scripted_rasterize(twin, bound, overrides=overrides))
        u = np.random.default_rng(seed_base).random(n_episodes)
        k = int((u < p).sum())
        key = json.dumps([overrides, n_episodes, seed_base], sort_keys=True)
        bid = "sb_" + hashlib.sha1(key.encode()).hexdigest()[:10]
        res = BatchResults(success_rate=k / n_episodes, ci95=wilson(k, n_episodes), n_success=k,
                           n_episodes=n_episodes, fail_reasons={"blocked_timeout": n_episodes - k})
        return SimBatch(batch_id=bid, twin_id=twin.twin_id, twin_version=twin.version, robot_id=robot.robot_id,
                        task=task, density=density, geometry_bound=bound, n_episodes=n_episodes,
                        seed_base=seed_base, results=res)
