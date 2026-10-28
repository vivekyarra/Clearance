"""Builders for reference rooms (known geometry) and robot specs, used by tests and benchmarks.
Grids are written through clearance_core.storage, so point CLEARANCE_DATA (or storage.DATA) at a
scratch directory first."""
from __future__ import annotations

import numpy as np

from clearance_core import grid as cgrid
from clearance_core.grid import FREE, OCC, Grid
from clearance_core.schemas import (Accuracy, KeepoutZone, Occupancy, Pose2, Primitive, RobotSpec, Threshold,
                                    TrafficModel, TrafficZone, Twin, Waypoint)


class RoomCanvas:
    """Occupancy canvas in world coordinates. Rectangles are rasterized by cell centers."""

    def __init__(self, width_m: float, height_m: float, res: float = 0.025, origin=(0.0, 0.0),
                 wall_m: float = 0.10, fill=FREE):
        self.res = float(res)
        self.origin = (float(origin[0]), float(origin[1]))
        self.w = int(round(width_m / res))
        self.h = int(round(height_m / res))
        self.data = np.full((self.h, self.w), fill, np.uint8)
        if wall_m > 0:
            x1, y1 = self.origin[0] + width_m, self.origin[1] + height_m
            ox, oy = self.origin
            self.rect(ox, oy, x1, oy + wall_m)
            self.rect(ox, y1 - wall_m, x1, y1)
            self.rect(ox, oy, ox + wall_m, y1)
            self.rect(x1 - wall_m, oy, x1, y1)

    def rect(self, x0, y0, x1, y1, value=OCC):
        xs = self.origin[0] + (np.arange(self.w) + 0.5) * self.res
        ys = self.origin[1] + (self.h - np.arange(self.h) - 0.5) * self.res
        cm = (xs > min(x0, x1)) & (xs < max(x0, x1))
        rm = (ys > min(y0, y1)) & (ys < max(y0, y1))
        self.data[np.ix_(rm, cm)] = value
        return self

    def save(self, key: str) -> Occupancy:
        return cgrid.save(Grid(self.data, self.res, self.origin), key)


def make_twin(canvas: RoomCanvas, key_prefix: str, waypoints: list[tuple], *, primitives=(), thresholds=(),
              keepouts=(), zones=(), od_pairs=(), persons_per_min=None, p90: float | None = None,
              twin_id: str = "twin_ref", version: int = 1) -> Twin:
    """waypoints: (id, x, y, yaw, kind). primitives: Primitive or dict. thresholds: (id, (a, b), height, status).
    keepouts: (id, polygon). zones: (id, polygon). od_pairs: (from, to, weight)."""
    occ = canvas.save(key_prefix.rstrip("/") + "/occ.png")
    prims = [p if isinstance(p, Primitive) else Primitive.model_validate(p) for p in primitives]
    traffic = TrafficModel(zones=[TrafficZone(id=z, polygon=list(map(tuple, poly))) for z, poly in zones],
                           od_pairs=[tuple(o) for o in od_pairs])
    if persons_per_min:
        traffic.persons_per_min = dict(persons_per_min)
    return Twin(
        twin_id=twin_id, site_id="site_ref", version=version, occupancy=occ,
        accuracy=Accuracy(method="reference_ground_truth", p90_abs_err_m=p90),
        primitives=prims,
        thresholds=[Threshold(id=i, segment=seg, height_m=h, status=s) for i, seg, h, s in thresholds],
        keepout_zones=[KeepoutZone(id=i, polygon=list(map(tuple, poly))) for i, poly in keepouts],
        waypoints=[Waypoint(id=i, xy=(x, y), yaw=yaw, kind=kind) for i, x, y, yaw, kind in waypoints],
        traffic=traffic, geometry_source="reference")


def box(id: str, x: float, y: float, l: float, w: float, yaw: float = 0.0, *, label="table", movable=True,
        h: float = 0.75) -> Primitive:
    return Primitive(id=id, type="box", label=label, pose=Pose2(xyz=(x, y, 0.0), yaw=yaw), size=(l, w, h),
                     movable=movable, source="reference")


def rect_robot(width: float, length: float, *, robot_id="robot_ref", in_place=True, turn_radius=0.0,
               drive="diff_drive", speed=0.7, max_threshold=0.02, safety_stop=0.5) -> RobotSpec:
    hl, hw = length / 2, width / 2
    return RobotSpec(**{
        "robot_id": robot_id, "class": "A_service", "vendor": "reference", "model": robot_id,
        "footprint_polygon_m": [(hl, hw), (-hl, hw), (-hl, -hw), (hl, -hw)],
        "drive": drive, "in_place_rotation": in_place, "min_turn_radius_m": turn_radius,
        "nominal_speed_mps": speed, "max_speed_mps": speed * 1.5, "max_threshold_m": max_threshold,
        "safety_stop_distance_m": safety_stop, "verified_by_human": True})
