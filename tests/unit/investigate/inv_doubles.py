"""Test doubles for the sim API (jobs/sim/api.py contract) and fixtures for the investigation tests."""
from __future__ import annotations

import math

import numpy as np
import pytest

from clearance_core import storage
from clearance_core.grid import Grid
from clearance_core.schemas import (BatchResults, Occupancy, Pose2, Primitive, RobotSpec, SimBatch, Task,
                                    TrafficModel, TrafficZone, Twin, Waypoint)

RES = 0.05
H, W = 60, 80  # 3 m x 4 m


def wilson(k, n, z=1.96):
    if n == 0:
        return (0.0, 1.0)
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def make_twin() -> Twin:
    prims = [
        Primitive(id="T1", type="box", label="table", pose=Pose2(xyz=(2.0, 1.75, 0.4)), size=(0.6, 0.6, 0.75),
                  movable=True),
        Primitive(id="S1", type="box", label="shelf", pose=Pose2(xyz=(2.0, 0.6, 0.9)), size=(1.0, 0.4, 1.8),
                  movable=False),
        Primitive(id="C9", type="cylinder", label="bin", pose=Pose2(xyz=(3.6, 2.6, 0.3)), size=(0.3, 0.3, 0.6),
                  movable=True),
    ]
    return Twin(twin_id="tw_test", site_id="site_test",
                occupancy=Occupancy(uri="sites/site_test/occ.png", resolution_m=RES, origin_xy=(0.0, 0.0),
                                    width=W, height=H),
                primitives=prims,
                waypoints=[Waypoint(id="DOCK", xy=(0.3, 1.5), kind="dock"), Waypoint(id="T7", xy=(3.7, 1.5),
                                                                                     kind="dest")],
                traffic=TrafficModel(zones=[TrafficZone(id="Z_COUNTER", polygon=[(1.5, 1.2), (2.5, 1.2), (2.5, 2.4),
                                                                                  (1.5, 2.4)]),
                                            TrafficZone(id="Z_FAR", polygon=[(0.0, 2.8), (0.4, 2.8), (0.4, 3.0)])]))


def make_robot(rid="bot_a", width=0.5) -> RobotSpec:
    return RobotSpec(robot_id=rid, robot_class="A_service", vendor="VendorA", model=f"Model-{rid}",
                     footprint_polygon_m=[(0.3, width / 2), (-0.3, width / 2), (-0.3, -width / 2), (0.3, -width / 2)],
                     verified_by_human=True, safety_stop_distance_m=0.5)


class SimDouble:
    """Deterministic sim: success depends on whether T1 is moved/removed, pedestrians in Z_COUNTER, density."""

    def __init__(self, base=0.5, fixed=0.95, zone_fixed=0.6):
        self.base, self.fixed, self.zone_fixed = base, fixed, zone_fixed
        self.calls = []

    def rasterize_twin(self, twin, bound="nominal", p90_err_m=None, overrides=None):
        g = np.full((H, W), 255, np.uint8)
        g[0, :] = g[-1, :] = 0
        g[:, 0] = g[:, -1] = 0
        grid = Grid(g, RES, (0.0, 0.0))
        for p in twin.primitives:
            x, y = p.pose.xyz[0], p.pose.xyz[1]
            hl, hw = p.size[0] / 2, p.size[1] / 2
            r0, c0 = grid.world_to_cell(x - hl, y + hw)
            r1, c1 = grid.world_to_cell(x + hl, y - hw)
            g[max(0, r0):r1 + 1, max(0, c0):c1 + 1] = 0
        return grid

    def run_batch(self, twin, robot, task, density, bound, n_episodes, seed_base, *, overrides=None,
                  record_episodes=True, out_prefix=None):
        self.calls.append({"robot": robot.robot_id, "density": density, "bound": bound, "n": n_episodes,
                           "overrides": overrides})
        ov = overrides or {}
        sr = self.base
        if "T1" in (ov.get("move") or {}) or "T1" in (ov.get("remove") or []):
            sr = self.fixed
        elif (ov.get("ped_zone_scale") or {}).get("Z_COUNTER") is not None:
            sr = self.zone_fixed
        elif density in ("quiet", "none"):
            sr = 0.7
        if robot.robot_id == "bot_b":
            sr = 0.97
        k = int(round(sr * n_episodes))
        bid = f"sb_probe_{len(self.calls):03d}"
        return SimBatch(batch_id=bid, twin_id=twin.twin_id, twin_version=twin.version, robot_id=robot.robot_id,
                        task=task, density=density, geometry_bound=bound, n_episodes=n_episodes, seed_base=seed_base,
                        results=BatchResults(success_rate=k / n_episodes, ci95=wilson(k, n_episodes), n_success=k,
                                             n_episodes=n_episodes, fail_reasons={"blocked_timeout": n_episodes - k}))

    def check_feasibility(self, *a, **k):
        raise AssertionError("not used by the investigation")


def bottleneck_map(peak_xy=(2.0, 2.2), second_xy=(0.8, 0.4)) -> np.ndarray:
    a = np.zeros((H, W), np.float32)
    g = Grid(np.zeros((H, W), np.uint8), RES, (0.0, 0.0))
    for (x, y), w in ((peak_xy, 10.0), (second_xy, 3.0)):
        r, c = g.world_to_cell(x, y)
        a[r - 1:r + 2, c - 1:c + 2] += w
    return a


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA", tmp_path)
    monkeypatch.setenv("CLEARANCE_USAGE_DB", str(tmp_path / "usage.db"))
    return tmp_path


@pytest.fixture
def scene(data_dir):
    twin, robot = make_twin(), make_robot()
    task = Task(sequence=["DOCK", "T7", "DOCK"])
    bn = bottleneck_map()
    key = "analyses/a1/batches/sb_nn.bottleneck.npy"
    np.save(storage.path(key), bn)
    ep_key = "analyses/a1/batches/sb_nn.episodes.jsonl"
    lines = ['{"t": 0.0, "robot": [0,0,0,0], "peds": [], "events": []}',
             '{"t": 0.1, "robot": [0,0,0,0], "peds": [], "events": [{"type": "stop", "xy": [2.0, 2.2]}]}',
             '{"t": 0.0, "robot": [0,0,0,0], "peds": [], "events": [{"type": "stop", "xy": [2.0, 2.2]},'
             ' {"type": "near_miss", "xy": [2.1, 2.2]}]}']
    storage.path(ep_key).write_text("\n".join(lines) + "\n", encoding="utf-8")
    batches = [
        SimBatch(batch_id="sb_nn", twin_id="tw_test", twin_version=1, robot_id=robot.robot_id, task=task,
                 density="normal", geometry_bound="nominal", n_episodes=100, seed_base=1000,
                 results=BatchResults(success_rate=0.5, ci95=wilson(50, 100), n_success=50, n_episodes=100,
                                      fail_reasons={"blocked_timeout": 50}),
                 bottleneck_uri=key, episodes_uri=ep_key),
        SimBatch(batch_id="sb_pc", twin_id="tw_test", twin_version=1, robot_id=robot.robot_id, task=task,
                 density="peak", geometry_bound="conservative", n_episodes=100, seed_base=1000,
                 results=BatchResults(success_rate=0.3, ci95=wilson(30, 100), n_success=30, n_episodes=100)),
    ]
    return {"twin": twin, "robot": robot, "task": task, "batches": batches, "sim": SimDouble()}
