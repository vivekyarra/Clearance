"""Randomized rooms for layout stress tests: rectangular room with 0.1 m walls, an interior column,
rotated tables and chairs (some pushed against walls), cylinders, a keep-out zone and waypoints."""
from __future__ import annotations

import math

import numpy as np
import shapely
from shapely.geometry import Polygon

from clearance_core.grid import FREE, OCC, Grid
from clearance_core.schemas import (Floor, KeepoutZone, Occupancy, Pose2, Primitive, RobotSpec, Task, Twin,
                                    Waypoint)

from layout_check import footprint

RES = 0.05


class Room:
    def __init__(self, seed: int, n_objects: int = 8, wall_hugging: bool = True, res: float = RES):
        rng = np.random.default_rng(seed)
        self.res = res
        self.Wm, self.Hm = float(rng.uniform(4.0, 7.0)), float(rng.uniform(3.0, 5.0))
        self.origin = (-0.1, -0.1)
        self.w = int(round((self.Wm + 0.2) / res))
        self.h = int(round((self.Hm + 0.2) / res))
        self.walls = np.ones((self.h, self.w), bool)
        k = int(round(0.1 / res))
        self.walls[k:self.h - k, k:self.w - k] = False
        # interior column (static structure, not a primitive)
        cx, cy = rng.uniform(1.5, self.Wm - 1.5), rng.uniform(1.2, self.Hm - 1.2)
        self.column = Polygon([(cx - 0.15, cy - 0.15), (cx + 0.15, cy - 0.15), (cx + 0.15, cy + 0.15),
                               (cx - 0.15, cy + 0.15)])
        self._paint(self.walls, [self.column])
        prims: list[Primitive] = []
        tries = 0
        while len(prims) < n_objects and tries < 400:
            tries += 1
            kind = rng.choice(["table", "chair", "stool"], p=[0.4, 0.4, 0.2])
            if kind == "stool":
                d = float(rng.uniform(0.3, 0.5))
                size, typ, yaw = (d, d, 0.5), "cylinder", 0.0
            else:
                l, w_ = (rng.uniform(0.6, 1.2), rng.uniform(0.5, 0.9)) if kind == "table" else (0.45, 0.45)
                size, typ, yaw = (float(l), float(w_), 0.75), "box", float(rng.choice([0.0, rng.uniform(-math.pi, math.pi)]))
            half = max(size[0], size[1]) / 2
            if wall_hugging and rng.random() < 0.4:
                side = rng.integers(4)
                gap = float(rng.uniform(0.0, 0.03))
                x = rng.uniform(half, self.Wm - half)
                y = rng.uniform(half, self.Hm - half)
                yaw = float(rng.choice([0.0, math.pi / 2]))
                ext = (size[0] if yaw == 0.0 else size[1]) / 2 if typ == "box" else size[0] / 2
                exty = (size[1] if yaw == 0.0 else size[0]) / 2 if typ == "box" else size[0] / 2
                if side == 0:
                    x = ext + gap
                elif side == 1:
                    x = self.Wm - ext - gap
                elif side == 2:
                    y = exty + gap
                else:
                    y = self.Hm - exty - gap
            else:
                x, y = rng.uniform(half, self.Wm - half), rng.uniform(half, self.Hm - half)
            p = Primitive(id=f"O{len(prims)}", type=typ, label=str(kind), pose=Pose2(xyz=(float(x), float(y), size[2] / 2),
                          yaw=yaw), size=size, movable=bool(rng.random() < 0.9), source="reference")
            poly = footprint(p)
            if not Polygon([(0, 0), (self.Wm, 0), (self.Wm, self.Hm), (0, self.Hm)]).contains(poly):
                continue
            if poly.intersects(self.column) or any(poly.intersects(footprint(q)) for q in prims):
                continue
            prims.append(p)
        self.prims = prims
        kx, ky = rng.uniform(0.5, self.Wm - 1.5), rng.uniform(0.5, self.Hm - 1.5)
        self.keepout = [(kx, ky), (kx + 0.6, ky), (kx + 0.6, ky + 0.6), (kx, ky + 0.6)]
        self.waypoints = [Waypoint(id="DOCK", xy=(0.4, 0.4), kind="dock"),
                          Waypoint(id="DEST", xy=(self.Wm - 0.4, self.Hm - 0.4), kind="dest"),
                          Waypoint(id="ENT", xy=(0.4, self.Hm - 0.4), kind="entrance"),
                          Waypoint(id="EXIT", xy=(self.Wm - 0.4, 0.4), kind="exit")]

    def _paint(self, mask, polys):
        h, w = mask.shape
        rr, cc = np.mgrid[0:h, 0:w]
        xs = self.origin[0] + (cc + 0.5) * self.res
        ys = self.origin[1] + (h - rr - 0.5) * self.res
        for poly in polys:
            mask |= shapely.contains_xy(poly, xs, ys)
        return mask

    def twin(self, with_exit: bool = True) -> Twin:
        wps = [w for w in self.waypoints if with_exit or w.kind != "exit"]
        return Twin(twin_id=f"twin_room", site_id="site_room", version=1, geometry_source="reference",
                    floor=Floor(bounds_polygon=[(0, 0), (self.Wm, 0), (self.Wm, self.Hm), (0, self.Hm)]),
                    occupancy=Occupancy(uri="sites/site_room/twin_v1/occ.png", resolution_m=self.res,
                                        origin_xy=self.origin, width=self.w, height=self.h),
                    primitives=[p.model_copy(deep=True) for p in self.prims],
                    keepout_zones=[KeepoutZone(id="K1", polygon=self.keepout)], waypoints=wps)

    def rasterize(self, twin, bound="nominal", p90_err_m=None, overrides=None) -> Grid:
        """Walls + column + keep-out + primitives at their (moved) poses (like jobs.sim rasterize_twin)."""
        ov = overrides or {}
        removed = set(ov.get("remove") or [])
        mv = ov.get("move") or {}
        mask = self.walls.copy()
        polys = [Polygon(k.polygon) for k in twin.keepout_zones]
        for p in twin.primitives:
            if p.id in removed:
                continue
            m = mv.get(p.id) or {}
            polys.append(footprint(p, m.get("dx", 0.0), m.get("dy", 0.0), m.get("dyaw", 0.0)))
        self._paint(mask, polys)
        return Grid(np.where(mask, OCC, FREE).astype(np.uint8), self.res, self.origin)


def robot(radius: float = 0.3) -> RobotSpec:
    s = radius / math.sqrt(2)
    return RobotSpec(robot_id="room_bot", **{"class": "A_service"}, vendor="Test", model="Bot",
                     footprint_polygon_m=[(s, s), (-s, s), (-s, -s), (s, -s)])


def task() -> Task:
    return Task(sequence=["DOCK", "DEST", "DOCK"])


def bottleneck_near(room: Room, grid: Grid, oid: str, rng) -> np.ndarray:
    """A pinch peak just beside object `oid` (so it becomes a candidate)."""
    p = next(q for q in room.prims if q.id == oid)
    b = np.zeros(grid.data.shape, np.float32)
    ang = rng.uniform(0, 2 * math.pi)
    r = max(p.size[0], p.size[1]) / 2 + 0.2
    x = min(max(p.pose.xyz[0] + r * math.cos(ang), 0.1), room.Wm - 0.1)
    y = min(max(p.pose.xyz[1] + r * math.sin(ang), 0.1), room.Hm - 0.1)
    rr, cc = grid.world_to_cell(x, y)
    b[int(rr), int(cc)] = 5.0
    return b
