"""Test doubles for the orchestrator: a deterministic sim (jobs/sim/api.py contract), optimizer and report hooks."""
from __future__ import annotations

import math

import numpy as np
import pytest

from clearance_core import storage
from clearance_core.grid import Grid
from clearance_core.schemas import (BatchResults, Fact, FactOrigin, Feasibility, LegFeasibility, Occupancy, Pose2,
                                    Primitive, RobotSpec, SimBatch, Task, TrafficModel, TrafficZone, Twin,
                                    Waypoint)

from agents.investigate.tools import SimAPI

RES = 0.05
H, W = 40, 80  # 2 m x 4 m corridor


def wilson(k, n, z=1.96):
    p = k / n
    d = 1 + z * z / n
    c = (p + z * z / (2 * n)) / d
    h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0.0, c - h), min(1.0, c + h))


def make_twin(thresholds=()) -> Twin:
    return Twin(twin_id="tw_corr", site_id="site_corr",
                occupancy=Occupancy(uri="sites/site_corr/occ.png", resolution_m=RES, width=W, height=H),
                primitives=[Primitive(id="B1", type="box", label="cart", pose=Pose2(xyz=(2.0, 0.55, 0.5)),
                                      size=(0.6, 0.9, 1.0), movable=True)],
                thresholds=list(thresholds),
                waypoints=[Waypoint(id="DOCK", xy=(0.4, 1.0), kind="dock"),
                           Waypoint(id="DEST", xy=(3.6, 1.0), kind="dest")],
                traffic=TrafficModel(zones=[TrafficZone(id="Z1", polygon=[(1.5, 1.0), (2.5, 1.0), (2.5, 2.0),
                                                                           (1.5, 2.0)])]))


def robot(rid, width, verified=True, payload=None, max_threshold=0.02) -> RobotSpec:
    return RobotSpec(robot_id=rid, robot_class="A_service", vendor="V", model=f"M-{rid}",
                     footprint_polygon_m=[(0.3, width / 2), (-0.3, width / 2), (-0.3, -width / 2), (0.3, -width / 2)],
                     verified_by_human=verified, payload_kg=payload, max_threshold_m=max_threshold)


def rasterize(twin, bound="nominal", p90_err_m=None, overrides=None):
    g = np.full((H, W), 255, np.uint8)
    g[0, :] = g[-1, :] = 0
    g[:, 0] = g[:, -1] = 0
    grid = Grid(g, RES, (0.0, 0.0))
    for p in twin.primitives:
        x, y = p.pose.xyz[0], p.pose.xyz[1]
        r0, c0 = grid.world_to_cell(x - p.size[0] / 2, y + p.size[1] / 2)
        r1, c1 = grid.world_to_cell(x + p.size[0] / 2, y - p.size[1] / 2)
        g[max(0, r0):r1 + 1, max(0, c0):c1 + 1] = 0
    return grid


class Sim:
    """bot_a: 0.80 as-is, 0.96 with B1 moved; bot_b: 0.95; feasible if width < limit per bound."""

    LIMIT = {"optimistic": 0.95, "nominal": 0.9, "conservative": 0.85}

    def __init__(self):
        self.calls = []
        self.n = 0

    def check_feasibility(self, twin, rb, task, bound="nominal"):
        w = max(p[1] for p in rb.footprint_polygon_m) * 2
        ok = w < self.LIMIT[bound]
        legs = [LegFeasibility(from_wp=a, to_wp=b, reachable=ok, narrowest_gap_m=self.LIMIT[bound] + 0.05,
                               required_gap_m=w, narrowest_xy=(2.0, 1.5), path_length_m=3.2)
                for a, b in zip(task.sequence, task.sequence[1:])]
        return Feasibility(robot_id=rb.robot_id, twin_id=twin.twin_id, twin_version=twin.version, legs=legs,
                           all_reachable=ok)

    def run_batch(self, twin, rb, task, density, bound, n_episodes, seed_base, *, overrides=None,
                  record_episodes=True, out_prefix=None):
        self.n += 1
        self.calls.append({"robot": rb.robot_id, "density": density, "bound": bound, "n": n_episodes,
                           "overrides": overrides, "seed_base": seed_base})
        ov = overrides or {}
        sr = {"bot_a": 0.80, "bot_b": 0.95}.get(rb.robot_id, 0.5)
        if rb.robot_id == "bot_a" and ("B1" in (ov.get("move") or {}) or "B1" in (ov.get("remove") or [])):
            sr = 0.96
        if density == "peak":
            sr -= 0.1
        k = int(round(sr * n_episodes))
        bid = f"sb_{self.n:04d}"
        bn_uri = None
        if out_prefix and not ov:
            a = np.zeros((H, W), np.float32)
            a[18:21, 48:51] = 5.0  # just east of B1 at y ~1.0
            bn_uri = f"{out_prefix}{bid}.bottleneck.npy"
            np.save(storage.path(bn_uri), a)
        return SimBatch(batch_id=bid, twin_id=twin.twin_id, twin_version=twin.version, robot_id=rb.robot_id,
                        task=task, density=density, geometry_bound=bound, n_episodes=n_episodes, seed_base=seed_base,
                        results=BatchResults(success_rate=k / n_episodes, ci95=wilson(k, n_episodes), n_success=k,
                                             n_episodes=n_episodes), bottleneck_uri=bn_uri, wall_time_s=0.01)


def optimizer_double(twin, rb, task, bottleneck, *, budget=None, seed=0, hint_object_ids=None):
    optimizer_double.hints = hint_object_ids
    b = BatchResults(success_rate=0.8, ci95=(0.7, 0.88), n_episodes=30)
    a = BatchResults(success_rate=0.96, ci95=(0.83, 0.99), n_episodes=30)
    return [{"change": {"move": {"B1": {"dx": 0.0, "dy": -0.05, "dyaw": 0.0}}}, "description": "Move B1 south",
             "before": b, "after": a, "batch_ids": []}]


def build_facts_double(analysis):
    out = []
    for i, bid in enumerate(analysis["batches"]):
        r = analysis["batch_index"][bid]
        out.append(Fact(fact_id=f"f_{i:03d}", value=r["results"]["success_rate"], unit="ratio",
                        label=f"Success rate {r['robot_id']} {r['density']} {r['bound']}",
                        origin=FactOrigin(type="sim_batch", ref=bid)))
    return out


def write_report_double(facts, citations, findings, verdicts, investigation):
    write_report_double.args = (facts, citations, findings, verdicts, investigation)
    return "# Clearance Report\n\n" + "\n".join(f"- {v.robot_id}: {v.label}" for v in verdicts)


def check_report_double(md, facts, tol=0.01):
    return {"ok": True, "errors": [], "clean_md": md}


@pytest.fixture
def data_dir(tmp_path, monkeypatch):
    monkeypatch.setattr(storage, "DATA", tmp_path)
    monkeypatch.setenv("CLEARANCE_USAGE_DB", str(tmp_path / "usage.db"))
    monkeypatch.setenv("CLEARANCE_LLM_MODE", "offline")
    monkeypatch.setenv("CLEARANCE_TAVILY_MODE", "offline")
    return tmp_path


@pytest.fixture
def world(data_dir):
    sim = Sim()
    return {"twin": make_twin(), "task": Task(sequence=["DOCK", "DEST", "DOCK"]), "sim": sim,
            "api": SimAPI(rasterize, sim.run_batch, sim.check_feasibility),
            "robots": [robot("bot_a", 0.5), robot("bot_b", 0.6), robot("bot_wide", 1.2),
                       robot("bot_unreviewed", 0.4, verified=False)],
            "modules": {"optimize_layout": optimizer_double, "build_facts": build_facts_double,
                        "build_citations": lambda a: [], "write_report": write_report_double,
                        "check_report": check_report_double}}


