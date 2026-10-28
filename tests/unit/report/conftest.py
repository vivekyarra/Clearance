"""Reference analysis used by the report tests: three robots, three densities x three bounds, feasibility,
optimizer results, an investigation trail with its own facts, on-site measurements and business inputs.
Robot names are fictional test names, not catalog entries."""
from __future__ import annotations

import copy

import pytest

from clearance_core.schemas import (BatchResults, Experiment, Fact, FactOrigin, Feasibility, Finding,
                                    Investigation, LegFeasibility, Occupancy, RobotSpec, SimBatch, Task,
                                    Threshold, Twin, TrafficModel, Accuracy, Floor, Hazard)

TASK = Task(sequence=["DOCK", "PICKUP", "T1", "T4", "DOCK"])

SUCCESS = {  # robot -> density -> bound -> success
    "rb_tray": {"quiet": (0.97, 0.99, 1.0), "normal": (0.91, 0.95, 0.98), "peak": (0.78, 0.86, 0.9)},
    "rb_slim": {"quiet": (0.88, 0.93, 0.96), "normal": (0.71, 0.82, 0.93), "peak": (0.55, 0.64, 0.75)},
    "rb_cart": {"quiet": (0.0, 0.0, 0.0), "normal": (0.0, 0.0, 0.0), "peak": (0.0, 0.0, 0.0)},
}


def _robot(rid, vendor, model, w, l, price=None):
    return RobotSpec(**{
        "robot_id": rid, "class": "A_service", "vendor": vendor, "model": model,
        "footprint_polygon_m": [(-l / 2, -w / 2), (l / 2, -w / 2), (l / 2, w / 2), (-l / 2, w / 2)],
        "height_m": 1.2, "max_speed_mps": 1.2, "nominal_speed_mps": 0.7, "max_threshold_m": 0.02,
        "safety_stop_distance_m": 0.5,
        "price": {"value": price, "currency": "USD", "is_public": price is not None,
                  "source_url": f"https://example.com/{rid}/price" if price else None},
        "fields_sources": {"max_speed_mps": {"url": f"https://example.com/{rid}/specs", "quote": "Max speed 1.2 m/s",
                                             "retrieved_at": "2026-10-20T10:00:00Z"},
                           "footprint_polygon_m": {"url": f"https://example.com/{rid}/specs",
                                                   "quote": f"{w} x {l} m", "retrieved_at": "2026-10-20T10:00:00Z"}},
        "verified_by_human": rid != "rb_cart",
    })


def _batches():
    out = []
    for rid, dens in SUCCESS.items():
        for d, vals in dens.items():
            for bound, sr in zip(["conservative", "nominal", "optimistic"], vals):
                n = 100
                out.append(SimBatch(
                    batch_id=f"sb_{rid}_{d}_{bound}", twin_id="twin_cafe", twin_version=2, robot_id=rid, task=TASK,
                    density=d, geometry_bound=bound, n_episodes=n, seed_base=1000,
                    results=BatchResults(success_rate=sr, ci95=(max(0.0, sr - 0.06), min(1.0, sr + 0.04)),
                                         median_mission_s=74.2 if sr else None, near_miss_per_mission=0.31,
                                         stops_per_mission=1.4, min_gap_m=0.12,
                                         fail_reasons={"blocked_timeout": round((1 - sr) * n * 0.6),
                                                       "collision": round((1 - sr) * n * 0.4)} if sr else
                                         {"no_path_static": n},
                                         n_success=round(sr * n), n_episodes=n)))
    return out


def _feasibility():
    def legs(blocked=False, unknown=False):
        seq = TASK.sequence
        out = []
        for i, (a, b) in enumerate(zip(seq, seq[1:])):
            reach = True
            if blocked and i == 2:
                reach = False
            if unknown and i == 3:
                reach = None
            out.append(LegFeasibility(from_wp=a, to_wp=b, reachable=reach, narrowest_gap_m=0.74 - 0.03 * i,
                                      required_gap_m=0.62 if not blocked else 0.81, path_length_m=4.5 + i,
                                      blocking_threshold="D2" if reach is None else None,
                                      reason="aisle too narrow" if reach is False else ""))
        return out
    return {
        "rb_tray": Feasibility(robot_id="rb_tray", twin_id="twin_cafe", twin_version=2, legs=legs(), all_reachable=True),
        "rb_slim": Feasibility(robot_id="rb_slim", twin_id="twin_cafe", twin_version=2, legs=legs(), all_reachable=True),
        "rb_cart": Feasibility(robot_id="rb_cart", twin_id="twin_cafe", twin_version=2, legs=legs(blocked=True),
                               all_reachable=False),
    }


def _investigation():
    inv_id = "inv_cafe_slim"
    facts = [
        Fact(fact_id="f_r1_001", value=0.82, unit="ratio", label="Success rate, baseline probe",
             origin=FactOrigin(type="investigation", ref=f"{inv_id}#step1")),
        Fact(fact_id="f_r1_002", value=0.93, unit="ratio", label="Success rate, probe with fewer pedestrians",
             origin=FactOrigin(type="investigation", ref=f"{inv_id}#step2")),
        Fact(fact_id="f_001", value=0.58, unit="m", label="Gap at the counter corridor",
             origin=FactOrigin(type="investigation", ref=f"{inv_id}#step3")),  # collides with a core id on purpose
    ]
    exps = [
        Experiment(experiment_id="e1", robot_id="rb_slim", hypothesis="Fails at the counter corridor at peak traffic.",
                   kind="reduce_pedestrians_zone", change={"zone_id": "Z2", "ped_scale": 0.5},
                   batch_id="pb_slim_1", baseline_fact_ids=["f_r1_001"], result_fact_ids=["f_r1_002"],
                   outcome="supported", next_action="dig_further",
                   conclusion="Halving pedestrians in Z2 lifts success to 93% [f_r1_002]."),
        Experiment(experiment_id="e2", robot_id="rb_slim", hypothesis="Chair C3 narrows the corridor.",
                   kind="move_object", change={"move": {"C3": {"dx": 0.15, "dy": -0.05, "dyaw": 0.0}}},
                   batch_id="pb_slim_2", baseline_fact_ids=["f_001"], result_fact_ids=[],
                   outcome="inconclusive", next_action="conclude",
                   conclusion="The corridor gap is 0.58 m [f_001], so the chair is a secondary factor."),
    ]
    findings = [Finding(finding_id="fd_1", robot_id="rb_slim",
                        claim="Pedestrians at the counter corridor cause most stops; success reaches 93% [f_r1_002] "
                              "with half the flow.", evidence_fact_ids=["f_r1_002"], object_ids=["C3"],
                        confirmed_by_probe=True)]
    inv = Investigation(investigation_id=inv_id, analysis_id="an_cafe", model_id="nemotron-super", mode="offline",
                        experiments=exps, findings=findings)
    return inv, facts


def make_analysis():
    twin = Twin(twin_id="twin_cafe", site_id="site_cafe", version=2,
                accuracy=Accuracy(method="tape_check", median_abs_err_m=0.021, p90_abs_err_m=0.038),
                floor=Floor(bounds_polygon=[(0, 0), (8, 0), (8, 6), (0, 6)]),
                occupancy=Occupancy(uri="sites/site_cafe/twin_v2/occ.png"),
                thresholds=[Threshold(id="D1", segment=((0, 0), (1, 0)), height_m=0.015),
                            Threshold(id="D2", segment=((2, 0), (3, 0)), status="flagged_manual")],
                hazards=[Hazard(label="cable on floor near the counter", xy=(3, 2), confidence=0.7)],
                traffic=TrafficModel(source="user_estimated"))
    inv, inv_facts = _investigation()
    return {
        "analysis_id": "an_cafe",
        "twin": twin,
        "measurements": [{"name": "aisle between T1 and T2", "value_m": 0.82, "twin_value_m": 0.80},
                         {"name": "counter corridor", "value_m": 0.95, "twin_value_m": 0.97}],
        "business": {"staff_cost_per_hour": 18.5, "task_hours_per_day": 6, "days_per_week": 6, "currency": "USD",
                     "quotes": {"rb_slim": 24000}},
        "candidates": [_robot("rb_tray", "Northwind", "Tray T2", 0.5, 0.55, price=19000),
                       _robot("rb_slim", "Northwind", "Slim S", 0.45, 0.5),
                       _robot("rb_cart", "Eastgate", "Cart C300", 0.8, 1.0)],
        "feasibility": _feasibility(),
        "batches": _batches(),
        "optimizer": {"rb_slim": [{"change": {"move": {"T4": {"dx": 0.18, "dy": 0.0, "dyaw": 0.0}}},
                                   "description": "Move T4 0.18 m toward wall (east)",
                                   "before": BatchResults(success_rate=0.71, ci95=(0.62, 0.79), n_episodes=200),
                                   "after": BatchResults(success_rate=0.93, ci95=(0.89, 0.96), n_episodes=200),
                                   "batch_ids": ["sb_opt_before", "sb_opt_after"]}]},
        "investigation": inv,
        "investigation_facts": inv_facts,
        "citations": [{"url": "https://example.com/market/robots-2026", "title": "Service robot market check 2026",
                       "retrieved_at": "2026-10-25"}],
    }


@pytest.fixture
def analysis():
    return copy.deepcopy(make_analysis())


@pytest.fixture(autouse=True)
def _no_report_meta_files(monkeypatch):
    """Live-mode writer tests keep report metrics in memory unless a test opts in."""
    monkeypatch.setenv("CLEARANCE_REPORT_META", "0")
