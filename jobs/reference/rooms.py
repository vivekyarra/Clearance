"""Reference rooms built from exactly known ground-truth geometry (PLAN Phase 0.5, 15.2).

Site frame: Z up, floor Z = 0. Room interiors span x in [0, W], y in [0, D] (walls are outside
that rectangle), so wall inner faces sit exactly on the room bounds. The (virtual) marker is at
the origin. Primitive pose.xyz is the solid's center (x, y, h/2); size = (l, w, h).
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

import numpy as np

from clearance_core import grid as cgrid
from clearance_core import storage
from clearance_core.grid import FREE, OCC, UNKNOWN, Grid
from clearance_core.schemas import (Accuracy, Floor, Pose2, Primitive, Threshold, TrafficModel, TrafficZone, Twin,
                            Waypoint)

from jobs.common.imageio import imwrite

from .geometry import Solid, free_gap_2d

RES = 0.025
BAND = (0.03, 2.0)
WALL_H = 2.5
MARGIN = 0.5
MOVABLE = {"table": True, "chair": True, "stool": True, "cart": True, "bin": True, "pallet": True,
           "bench": True, "plant": True, "shelf": False, "counter": False, "pillar": False, "printer": False,
           "water_cooler": True, "dock": False, "door": False, "block": False}
GOLDEN = ("golden_narrow_aisle", "golden_exact_aisle", "golden_threshold", "golden_empty")
KINDS = ("cafe", "storeroom", "corridor") + GOLDEN
GOLDEN_ROBOT_WIDTH_M = 0.50
SERVE_OFFSET_M = 0.42  # table edge to the serving robot's centre


@dataclass
class ReferenceRoom:
    kind: str
    seed: int
    width: float
    depth: float
    solids: list[Solid]
    twin: Twin
    gt_measurements: list[dict]
    grid: Grid
    scan_path: list[tuple[float, float]]
    openings: list[tuple[float, float, float, float]] = field(default_factory=list)  # free rects in walls
    notes: dict = field(default_factory=dict)
    room_yaw: float = 0.0  # placement of the room-local frame in the site frame
    room_offset: tuple[float, float] = (0.0, 0.0)


class _Builder:
    def __init__(self, kind: str, seed: int, W: float, D: float, wall_t: float):
        self.kind, self.seed, self.W, self.D, self.t = kind, seed, W, D, wall_t
        self.rng = np.random.default_rng(seed)
        self.solids: list[Solid] = []
        self.openings: list[tuple[float, float, float, float]] = []
        self.meas_specs: list[tuple[str, tuple, tuple]] = []
        self.waypoints: list[Waypoint] = []
        self.thresholds: list[Threshold] = []
        self.zones: list[TrafficZone] = []
        self.od: list[tuple[str, str, float]] = []
        self.path: list[tuple[float, float]] = []
        self._n = {}
        self.notes: dict = {}

    def _id(self, prefix):
        self._n[prefix] = self._n.get(prefix, 0) + 1
        return f"{prefix}{self._n[prefix]}"

    def box(self, label, x0, y0, x1, y1, h, z0=0.0, id=None, primitive=True, yaw=0.0):
        s = Solid(id or self._id(label[:2].upper() + "_"), "box", label, (x0 + x1) / 2, (y0 + y1) / 2,
                  x1 - x0, y1 - y0, z0, z0 + h, yaw, MOVABLE.get(label, False), primitive=primitive)
        self.solids.append(s)
        return s

    def boxc(self, label, cx, cy, l, w, h, yaw=0.0, id=None):
        s = Solid(id or self._id(label[:2].upper() + "_"), "box", label, cx, cy, l, w, 0.0, h, yaw,
                  MOVABLE.get(label, False))
        self.solids.append(s)
        return s

    def cyl(self, label, cx, cy, d, h, id=None):
        s = Solid(id or self._id(label[:2].upper() + "_"), "cyl", label, cx, cy, d, d, 0.0, h, 0.0,
                  MOVABLE.get(label, False))
        self.solids.append(s)
        return s

    def wall_x(self, y0, y1, x0, x1, gaps=()):
        """Horizontal wall occupying y in [y0, y1], from x0 to x1, minus gaps [(gx0, gx1)]."""
        cuts = [x0] + [g for gap in sorted(gaps) for g in gap] + [x1]
        for a, b in zip(cuts[::2], cuts[1::2]):
            if b - a > 1e-6:
                self.box("wall", a, y0, b, y1, WALL_H, id=self._id("W"), primitive=False)
        for g in gaps:
            self.openings.append((g[0], y0, g[1], y1))

    def wall_y(self, x0, x1, y0, y1, gaps=()):
        cuts = [y0] + [g for gap in sorted(gaps) for g in gap] + [y1]
        for a, b in zip(cuts[::2], cuts[1::2]):
            if b - a > 1e-6:
                self.box("wall", x0, a, x1, b, WALL_H, id=self._id("W"), primitive=False)
        for g in gaps:
            self.openings.append((x0, g[0], x1, g[1]))

    def perimeter(self, south=(), north=(), west=(), east=()):
        W, D, t = self.W, self.D, self.t
        self.wall_x(-t, 0, -t, W + t, south)
        self.wall_x(D, D + t, -t, W + t, north)
        self.wall_y(-t, 0, 0, D, west)
        self.wall_y(W, W + t, 0, D, east)

    def lip(self, tid, x0, y0, x1, y1, h, segment):
        self.box("threshold", x0, y0, x1, y1, h, id=tid, primitive=False)
        self.thresholds.append(Threshold(id=tid, segment=segment, height_m=h, status="measured"))

    def meas(self, name, mid, direction):
        self.meas_specs.append((name, mid, direction))

    def wp(self, id, x, y, kind, yaw=0.0):
        self.waypoints.append(Waypoint(id=id, xy=(x, y), yaw=yaw, kind=kind))

    def zone(self, id, x0, y0, x1, y1):
        self.zones.append(TrafficZone(id=id, polygon=[(x0, y0), (x1, y0), (x1, y1), (x0, y1)]))

    def jit(self, a):
        return float(self.rng.uniform(-a, a))


# ---------------------------------------------------------------------------------------------
def _cafe(b: _Builder):
    W, D = b.W, b.D
    b.perimeter(south=[(0.6, 1.6)])
    # kitchen in the NE corner, door with a 2 cm lip
    b.wall_y(6.0, 6.1, 3.6, D)
    b.wall_x(3.5, 3.6, 6.0, W, gaps=[(6.7, 7.6)])
    b.lip("D1", 6.7, 3.5, 7.6, 3.6, 0.02, ((6.7, 3.55), (7.6, 3.55)))
    b.box("counter", 2.6, 5.25, 5.4, D, 1.05, id="COUNTER")
    b.box("counter", 6.3, 5.4, W, D, 0.9, id="KITCHEN_PASS")  # kitchen worktop
    tx, ty = (1.6, 3.6, 5.6), (1.3, 3.0)
    k = 0
    for r, y in enumerate(ty):
        for x in tx:
            k += 1
            dx, dy = b.jit(0.03), b.jit(0.03)
            b.boxc("table", x + dx, y + dy, 0.75, 0.75, 0.75, id=f"TABLE{k}")
            for side in (-1, 1):
                off = 0.62 + float(b.rng.uniform(-0.04, 0.06))
                b.boxc("chair", x + dx + side * off, y + dy + b.jit(0.04), 0.45, 0.45, 0.9,
                       yaw=b.jit(0.08), id=f"CH{k}{'W' if side < 0 else 'E'}")
            # serving spot: beside the table on the aisle side, robot centre 0.42 m from the table edge (where
            # tray robots park to serve), in the open aisle between the table rows
            wy = y + dy + (1 if r == 0 else -1) * (0.75 / 2 + SERVE_OFFSET_M)
            b.wp(f"T{k}", x + dx, wy, "dest")
    b.cyl("plant", 0.6, 5.3, 0.5, 1.2, id="PLANT1")
    b.cyl("bin", 7.75, 0.3, 0.35, 0.8, id="BIN1")
    # charging dock on the west wall between the table rows and the plant, with about 1 m of open floor in
    # front of it (vendor install guidance) and > 0.6 m to the nearest chair and the plant on either side
    b.box("dock", 0.0, 3.95, 0.35, 4.4, 0.3, id="DOCKSTN")
    b.wp("DOCK", 0.8, 4.175, "dock")
    # pickup facing the kitchen pass-through worktop (front edge at y = 5.4)
    b.wp("PICKUP", 7.15, 5.0, "pickup", yaw=np.pi / 2)
    b.wp("ENTRANCE", 1.1, 0.4, "entrance")
    # one street door: it is also the exit; the waypoint sits in the door opening
    b.wp("EXIT", 1.1, -0.075, "exit")
    b.zone("entrance", 0.6, 0.0, 1.6, 0.8)
    b.zone("counter", 2.6, 4.4, 5.4, 5.2)
    b.zone("seating", 1.0, 0.4, 6.0, 3.4)
    b.zone("kitchen", 6.2, 3.7, 7.9, 5.3)
    b.od = [("entrance", "counter", 0.5), ("counter", "seating", 0.3), ("seating", "entrance", 0.15),
            ("kitchen", "counter", 0.05)]
    # reference distances (tape-style)
    b.meas("room_width_x", (4.0, 2.15), (1, 0))
    b.meas("dining_depth_to_kitchen_wall", (6.62, 1.8), (0, 1))
    b.meas("south_wall_to_kitchen_worktop", (7.15, 2.0), (0, 1))
    b.meas("entrance_door_width", (1.1, -0.07), (1, 0))
    b.meas("kitchen_door_width", (7.15, 3.55), (1, 0))
    b.meas("table_row_gap", (3.6, 2.15), (0, 1))
    b.meas("chair_gap_T1_T2", (2.6, 1.3), (1, 0))
    b.meas("chair_gap_T2_T3", (4.6, 1.3), (1, 0))
    b.meas("chair_gap_T4_T5", (2.6, 3.0), (1, 0))
    b.meas("table1_to_south_wall", (1.8, 0.45), (0, 1))
    b.meas("table5_to_counter", (3.6, 4.3), (0, 1))
    b.meas("T3_chair_to_east_wall", (7.2, 1.3), (1, 0))
    b.meas("kitchen_inner_width", (7.0, 4.8), (1, 0))
    b.meas("counter_to_kitchen_wall", (5.7, 5.6), (1, 0))
    b.path = [(1.1, -0.6), (1.1, 0.5), (0.5, 0.6), (0.5, 2.15), (2.6, 2.15), (2.6, 0.45), (4.6, 0.45),
              (4.6, 2.15), (6.6, 2.15), (6.6, 0.5), (7.5, 0.6), (7.5, 2.9), (7.15, 3.2), (7.15, 4.6),
              (6.6, 4.9), (7.6, 4.9), (7.15, 4.4), (7.15, 3.2), (6.6, 2.15), (5.8, 3.3), (5.8, 4.4),
              (3.6, 4.4), (1.2, 4.4), (0.6, 3.9), (0.6, 2.2), (2.6, 2.2), (2.6, 3.8), (4.6, 3.8),
              (4.6, 2.2), (3.6, 2.15), (1.1, 1.0)]


def _storeroom(b: _Builder):
    W, D = b.W, b.D
    b.perimeter(south=[(0.5, 1.7)])
    shelves = {"A": (2.2, 2.8), "B": (4.0, 4.6), "C": (5.4, 6.0), "D": (7.4, 8.0)}
    for n, (y0, y1) in shelves.items():
        b.box("shelf", 2.0, y0, 8.5, y1, 2.0, id=f"SHELF_{n}")
    b.box("pallet", 3.4, 0.6, 4.6, 1.4, 0.15, id="PALLET1")
    b.boxc("cart", 6.5 + b.jit(0.05), 1.0 + b.jit(0.05), 0.9, 0.6, 0.9, yaw=b.jit(0.05), id="CART1")
    b.cyl("pillar", 1.0, 5.7, 0.4, WALL_H, id="PILLAR1")
    b.box("dock", 0.0, 0.5, 0.3, 1.0, 0.3, id="DOCKSTN")
    b.wp("DOCK", 0.7, 0.75, "dock")
    b.wp("PICKUP", 4.0, 1.8, "pickup", yaw=-np.pi / 2)
    b.wp("S1", 5.0, 3.4, "dest")
    b.wp("S2", 5.0, 5.0, "dest")
    b.wp("S3", 5.0, 6.7, "dest")
    b.wp("S4", 9.2, 7.0, "dest")
    # one door to the loading area: entrance just inside, exit in the door opening
    b.wp("ENTRANCE", 1.3, 0.35, "entrance")
    b.wp("EXIT", 1.1, -0.1, "exit")
    b.zone("door", 0.5, 0.0, 1.7, 0.8)
    b.zone("staging", 2.0, 0.2, 8.0, 2.1)
    b.zone("aisles", 2.0, 2.9, 8.5, 7.3)
    b.od = [("door", "staging", 0.5), ("staging", "aisles", 0.4), ("aisles", "door", 0.1)]
    for x in (3.0, 5.0, 7.0):
        b.meas(f"aisle_AB_1.2_x{x:.0f}", (x, 3.4), (0, 1))
        b.meas(f"aisle_BC_0.80_x{x:.0f}", (x, 5.0), (0, 1))
    b.meas("aisle_CD_x5", (5.0, 6.7), (0, 1))
    b.meas("staging_depth_x3", (3.0, 1.1), (0, 1))
    b.meas("door_width", (1.1, -0.1), (1, 0))
    b.meas("west_cross_aisle", (1.0, 2.5), (1, 0))
    b.meas("east_cross_aisle", (9.25, 4.3), (1, 0))
    b.meas("room_width_x", (5.0, 1.9), (1, 0))
    b.meas("pillar_to_west_wall", (0.4, 5.7), (1, 0))
    b.meas("pillar_to_shelf_C", (1.6, 5.7), (1, 0))
    b.meas("pallet_to_shelf_A", (4.0, 1.8), (0, 1))
    b.meas("cart_to_east_wall", (8.5, 1.0), (1, 0))
    b.notes["aisle_080"] = {"y0": 4.6, "y1": 5.4, "x0": 2.0, "x1": 8.5, "width_m": 0.80}
    b.path = [(1.1, -0.6), (1.1, 0.6), (1.1, 1.8), (3.0, 1.8), (5.0, 1.8), (8.0, 1.8), (9.2, 1.0),
              (9.2, 3.4), (8.0, 3.4), (2.0, 3.4), (1.0, 3.4), (1.0, 5.0), (2.0, 5.0), (8.5, 5.0),
              (9.2, 5.0), (9.2, 6.7), (8.0, 6.7), (2.0, 6.7), (0.6, 6.7), (0.6, 4.3), (0.6, 1.8),
              (2.2, 1.6), (6.0, 0.3), (8.8, 0.3), (9.3, 2.0), (9.3, 7.1), (1.0, 7.1), (0.4, 5.0), (1.1, 1.0)]


def _corridor(b: _Builder):
    W, D, t = b.W, b.D, b.t
    north = [(2.0, 2.9), (6.0, 6.9), (10.0, 10.9)]
    south = [(4.0, 4.9), (12.0, 12.9)]
    b.perimeter(south=south, north=north)
    for i, (x0, x1) in enumerate(north):
        b.box("door", x0, D + t - 0.04, x1, D + t, 2.1, id=f"DOOR_N{i + 1}")
    for i, (x0, x1) in enumerate(south):
        b.box("door", x0, -t, x1, -t + 0.04, 2.1, id=f"DOOR_S{i + 1}")
    b.box("bench", 7.5, 0.0, 9.0, 0.45, 0.45, id="BENCH1")
    b.boxc("printer", 14.5, 1.45, 0.6, 0.6, 1.0, id="PRINTER1")
    b.cyl("water_cooler", 0.4, 1.5, 0.35, 1.1, id="COOLER1")
    b.cyl("plant", 11.6, 0.3, 0.45, 1.0, id="PLANT1")
    b.wp("DOCK", 0.6, 0.6, "dock")
    b.wp("PICKUP", 14.5, 0.55, "pickup")
    b.wp("O1", 2.45, 1.45, "dest")
    b.wp("O2", 4.45, 0.35, "dest")
    b.wp("O3", 6.45, 1.45, "dest")
    b.wp("O4", 10.45, 1.45, "dest")
    # the two south doors lead to the lobby (S1, way in) and the stairwell (S2, way out)
    b.wp("ENTRANCE", 4.45, -0.075, "entrance")
    b.wp("EXIT", 12.45, -0.075, "exit")
    b.zone("west", 0.0, 0.0, 3.0, D)
    b.zone("middle", 5.0, 0.0, 11.0, D)
    b.zone("east", 12.0, 0.0, W, D)
    b.od = [("west", "east", 0.4), ("east", "west", 0.4), ("middle", "west", 0.1), ("middle", "east", 0.1)]
    for x in (1.5, 5.0, 9.5, 13.0):
        b.meas(f"corridor_width_x{x:g}", (x, 0.9), (0, 1))
    b.meas("bench_to_north_wall", (8.25, 1.1), (0, 1))
    b.meas("printer_to_south_wall", (14.5, 0.5), (0, 1))
    b.meas("plant_to_north_wall", (11.6, 1.0), (0, 1))
    b.meas("door_N1_width", (2.45, D + 0.07), (1, 0))
    b.meas("door_N2_width", (6.45, D + 0.07), (1, 0))
    b.meas("door_S1_width", (4.45, -0.07), (1, 0))
    b.meas("corridor_length", (8.0, 0.9), (1, 0))
    b.path = [(0.9, 0.9), (4.0, 0.7), (8.0, 1.1), (12.0, 0.9), (15.4, 0.9), (15.4, 0.5), (12.0, 1.1),
              (8.0, 0.8), (4.0, 1.1), (1.0, 0.9)]


def _golden(b: _Builder, variant: str):
    W, D = b.W, b.D
    b.perimeter()
    b.wp("DOCK", 0.6, 2.0, "dock")
    b.wp("PICKUP", 1.2, 1.0, "pickup")
    b.wp("T1", 5.2, 2.0, "dest")
    b.zone("left", 0.0, 0.0, 2.2, D)
    b.zone("right", 3.8, 0.0, W, D)
    b.od = [("left", "right", 0.5), ("right", "left", 0.5)]
    b.meas("left_depth", (1.0, 1.0), (0, 1))
    b.meas("right_depth", (5.0, 1.0), (0, 1))
    b.path = [(0.5, 0.5), (0.5, 3.5), (2.0, 3.5), (2.0, 0.5), (0.8, 2.0), (2.0, 2.0), (4.0, 2.0), (5.5, 0.5),
              (5.5, 3.5), (4.0, 3.5), (4.0, 0.5), (5.2, 2.0), (2.0, 2.0), (1.0, 1.0)]
    if variant in ("golden_narrow_aisle", "golden_exact_aisle"):
        aisle = 0.45 if variant == "golden_narrow_aisle" else GOLDEN_ROBOT_WIDTH_M + 0.02
        y0, y1 = 2.0 - aisle / 2, 2.0 + aisle / 2
        b.box("block", 2.25, 0.0, 3.75, y0, 2.0, id="BLOCK_S")
        b.box("block", 2.25, y1, 3.75, D, 2.0, id="BLOCK_N")
        for x in (2.6, 3.0, 3.4):
            b.meas(f"aisle_width_x{x:g}", (x, 2.0), (0, 1))
        b.meas("room_width_through_aisle", (1.0, 2.0), (1, 0))
        for y in (1.0, 3.0):
            b.meas(f"block_to_west_wall_y{y:g}", (1.1, y), (1, 0))
            b.meas(f"block_to_east_wall_y{y:g}", (4.9, y), (1, 0))
        b.meas("west_part_depth", (1.6, 2.5), (0, 1))
        b.notes["aisle_width_m"] = aisle
    elif variant == "golden_threshold":
        b.wall_y(2.94, 3.06, 0.0, D, gaps=[(1.6, 2.4)])
        b.lip("D1", 2.94, 1.6, 3.06, 2.4, 0.03, ((3.0, 1.6), (3.0, 2.4)))
        b.meas("door_width", (3.0, 2.0), (0, 1))
        b.meas("room_width_through_door", (1.0, 2.0), (1, 0))
        for y in (0.8, 3.2):
            b.meas(f"west_room_width_y{y:g}", (1.5, y), (1, 0))
            b.meas(f"east_room_width_y{y:g}", (4.5, y), (1, 0))
        b.meas("west_depth_x2", (2.0, 1.0), (0, 1))
        b.meas("east_depth_x4", (4.0, 1.0), (0, 1))
        b.meas("west_depth_x05", (0.5, 1.0), (0, 1))
        b.meas("east_depth_x55", (5.5, 1.0), (0, 1))
    else:  # empty
        for y in (0.5, 1.5, 2.5, 3.5):
            b.meas(f"room_width_y{y:g}", (3.0, y), (1, 0))
        for x in (0.5, 2.0, 3.0, 4.0, 5.5):
            b.meas(f"room_depth_x{x:g}", (x, 2.0), (0, 1))


# ---------------------------------------------------------------------------------------------
def _room_pose(kind: str, seed: int) -> tuple[float, tuple[float, float]]:
    """Placement of the room in the site frame. Golden rooms stay axis-aligned at the origin (their
    answers are analytic). Other rooms get a sub-cell offset (walls do not sit on cell edges) and,
    for seed >= 1, a yaw of 8-25 degrees (the marker is not square to the walls)."""
    if kind in GOLDEN:
        return 0.0, (0.0, 0.0)
    rng = np.random.default_rng(777 + seed)
    off = tuple(float(v) for v in rng.uniform(0.002, RES - 0.002, 2))
    yaw = 0.0 if seed == 0 else float(np.deg2rad(rng.uniform(8, 25)) * rng.choice([-1, 1]))
    return yaw, off


class _Pose:
    def __init__(self, yaw, off):
        self.yaw, self.c, self.s, self.t = yaw, np.cos(yaw), np.sin(yaw), np.asarray(off, float)

    def xy(self, p):
        x, y = p
        return (float(self.c * x - self.s * y + self.t[0]), float(self.s * x + self.c * y + self.t[1]))

    def vec(self, d):
        x, y = d
        return (float(self.c * x - self.s * y), float(self.s * x + self.c * y))

    def to_local(self, x, y):
        x, y = np.asarray(x) - self.t[0], np.asarray(y) - self.t[1]
        return self.c * x + self.s * y, -self.s * x + self.c * y


def _apply_pose(b: _Builder, P: _Pose):
    for s in b.solids:
        s.cx, s.cy = P.xy((s.cx, s.cy))
        s.yaw = s.yaw + P.yaw
    b.meas_specs = [(n, P.xy(m), P.vec(d)) for n, m, d in b.meas_specs]
    b.waypoints = [Waypoint(id=w.id, xy=P.xy(w.xy), yaw=w.yaw + P.yaw, kind=w.kind) for w in b.waypoints]
    b.thresholds = [Threshold(id=t.id, segment=(P.xy(t.segment[0]), P.xy(t.segment[1])), height_m=t.height_m,
                              status=t.status) for t in b.thresholds]
    b.zones = [TrafficZone(id=z.id, polygon=[P.xy(q) for q in z.polygon]) for z in b.zones]
    b.path = [P.xy(q) for q in b.path]
    a = b.notes.get("aisle_080")
    if a:
        yc = (a["y0"] + a["y1"]) / 2
        a["p0"], a["p1"] = P.xy((a["x0"], yc)), P.xy((a["x1"], yc))


def rasterize(solids: list[Solid], W: float, D: float, openings, res: float = RES, margin: float = MARGIN,
              band=BAND, pose: "_Pose | None" = None) -> Grid:
    """Ground-truth occupancy: cell centers inside a band solid -> OCC; inside the room or a wall
    opening (tested in room-local coordinates) -> FREE; everything else (beyond walls) -> UNKNOWN."""
    P = pose or _Pose(0.0, (0.0, 0.0))
    corners = np.array([P.xy(q) for q in ((0, 0), (W, 0), (W, D), (0, D))])
    lo = np.floor((corners.min(axis=0) - margin) / res) * res
    hi = corners.max(axis=0) + margin
    nx, ny = (int(v) for v in np.ceil((hi - lo) / res))
    g = Grid(np.full((ny, nx), UNKNOWN, np.uint8), res, (float(lo[0]), float(lo[1])))
    rr, cc = np.mgrid[0:ny, 0:nx]
    x, y = g.cell_to_world(rr, cc)
    lx, ly = P.to_local(x, y)
    free = (lx >= 0) & (lx <= W) & (ly >= 0) & (ly <= D)
    for (x0, y0, x1, y1) in openings:
        free |= (lx >= x0) & (lx <= x1) & (ly >= y0) & (ly <= y1)
    g.data[free] = FREE
    for s in solids:
        if s.in_band(*band):
            g.data[s.contains_xy(x, y)] = OCC
    return g


def make_room(kind: Literal["cafe", "storeroom", "corridor", "golden_narrow_aisle", "golden_exact_aisle",
                            "golden_threshold", "golden_empty"], seed: int = 0, *, save: bool = True) -> ReferenceRoom:
    if kind not in KINDS:
        raise ValueError(f"unknown room kind {kind!r}; expected one of {KINDS}")
    dims = {"cafe": (8.0, 6.0, 0.15), "storeroom": (10.0, 8.0, 0.2), "corridor": (16.0, 1.8, 0.15)}
    W, D, t = dims.get(kind, (6.0, 4.0, 0.15))
    b = _Builder(kind, seed, W, D, t)
    {"cafe": _cafe, "storeroom": _storeroom, "corridor": _corridor}.get(kind, lambda bb: _golden(bb, kind))(b)
    P = _Pose(*_room_pose(kind, seed))
    _apply_pose(b, P)

    gts = []
    for name, mid, direction in b.meas_specs:
        a, bb, v = free_gap_2d(b.solids, mid, direction, BAND)
        gts.append({"name": name, "a_xy": a, "b_xy": bb, "value_m": round(v, 4)})

    grid = rasterize(b.solids, W, D, b.openings, pose=P)
    key = f"reference/{kind}_s{seed}/occ.png"
    occ = cgrid.save(grid, key) if save else grid.meta(key)
    occ.height_band_m = BAND
    prims = [Primitive(id=s.id, type="box" if s.kind == "box" else "cylinder", label=s.label,
                       pose=Pose2(xyz=(s.cx, s.cy, (s.z0 + s.z1) / 2), yaw=s.yaw),
                       size=(s.l, s.w, s.z1 - s.z0), movable=s.movable, fragile=s.fragile, confidence=1.0,
                       source="reference") for s in b.solids if s.primitive]
    twin = Twin(twin_id=f"twin_ref_{kind}_s{seed}", site_id=f"ref_{kind}_s{seed}", version=1,
                accuracy=Accuracy(method="reference_ground_truth", median_abs_err_m=0.0, p90_abs_err_m=0.0),
                floor=Floor(plane=[0.0, 0.0, 1.0, 0.0],
                            bounds_polygon=[P.xy(q) for q in ((0, 0), (W, 0), (W, D), (0, D))]),
                occupancy=occ, primitives=prims, thresholds=b.thresholds, waypoints=b.waypoints,
                traffic=TrafficModel(source="user_estimated", zones=b.zones, od_pairs=b.od),
                assets={}, geometry_source="reference")
    room = ReferenceRoom(kind, seed, W, D, b.solids, twin, gts, grid, b.path, b.openings, b.notes,
                         room_yaw=P.yaw, room_offset=(float(P.t[0]), float(P.t[1])))
    # raster quantization of the ground-truth grid itself (what a perfect scan would read)
    from jobs.recon.measure import evaluate_grid
    ev = evaluate_grid(grid, gts)
    twin.accuracy.median_abs_err_m = ev["median_abs_err_m"]
    twin.accuracy.p90_abs_err_m = ev["p90_abs_err_m"]
    return room


def save_sample_site(kind: str, site_id: str, seed: int = 0, root: str | Path | None = None) -> Twin:
    """Write <root>/<site_id>/site.json + twin_v1/{twin.json, occ.png, gt_measurements.json}.

    root defaults to <repo>/sample_sites (the API imports it into storage). Inside twin.json the
    occupancy uri is the storage key the site has after import: sites/<site_id>/twin_v1/occ.png."""
    room = make_room(kind, seed, save=False)
    base = Path(root) if root is not None else storage.REPO / "sample_sites"
    tdir = base / site_id / "twin_v1"
    tdir.mkdir(parents=True, exist_ok=True)
    key_prefix = f"sites/{site_id}/twin_v1/"
    twin = room.twin.model_copy(deep=True)
    twin.site_id = site_id
    twin.twin_id = f"twin_{site_id}_v1"
    twin.occupancy = room.grid.meta(key_prefix + "occ.png")
    twin.occupancy.height_band_m = BAND
    imwrite(tdir / "occ.png", room.grid.data)

    def dump(path: Path, obj):
        if hasattr(obj, "model_dump"):
            obj = obj.model_dump(mode="json", by_alias=True)
        path.write_text(json.dumps(obj, indent=2), encoding="utf-8")

    dump(tdir / "twin.json", twin)
    dump(tdir / "gt_measurements.json", room.gt_measurements)
    site_type = {"cafe": "cafe", "storeroom": "storeroom", "corridor": "office"}.get(kind, "golden")
    dump(base / site_id / "site.json", {
        "site_id": site_id, "name": f"Reference {kind.replace('_', ' ')}", "type": site_type,
        "kind": kind, "seed": seed, "geometry_source": "reference", "area_m2": round(room.width * room.depth, 2),
        "latest_version": 1, "twin": key_prefix + "twin.json"})
    return twin


def render_capture(room: ReferenceRoom, out_dir: str, tier: Literal["T1_LIDAR", "T2_VIO_MONO"] = "T1_LIDAR",
                   seed: int = 0, **kw) -> str:
    """Contract entry point; implementation in jobs/reference/render.py."""
    from .render import render_capture as _render
    return _render(room, out_dir, tier, seed, **kw)
