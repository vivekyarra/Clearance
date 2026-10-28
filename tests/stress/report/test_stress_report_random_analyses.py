"""Stress: offline Clearance Reports for many randomized analyses (0-6 robots, missing batches, all-failing
robots, blocked/pending legs, odd business inputs, names with digits) must never crash and must pass the
fact checker on the first pass, with every robot carrying exactly its code verdict."""
from __future__ import annotations

import random
import re

import pytest

from clearance_core.schemas import (Accuracy, BatchResults, Experiment, Fact, FactOrigin, Feasibility, Finding,
                                    Floor, Hazard, Investigation, LegFeasibility, Occupancy, RobotSpec, SimBatch,
                                    Task, Threshold, TrafficModel, Twin)

from agents.factcheck.check import check_report, enforce
from agents.report.facts import build_citations, build_facts, collect_findings, derive_verdicts, investigations
from agents.report.generate import generate_report
from agents.report.writer import write_report

NAMES = [("OTTO Motors", "OTTO 100"), ("Mobile Industrial Robots", "MiR250"), ("Keenon", "T9"),
         ("Northwind", "Tray T2"), ("Eastgate", "Cart C300"), ("Pudu", "BellaBot"), ("Acme", "Runner 300 Pro"),
         ("Five Star", "Courier"), ("Westline", "Slim S"), ("Acme", "Model 9")]
CURRENCIES = ["USD", "EUR", "GBP", "INR", "usd"]
DENS = ["quiet", "normal", "peak"]
BOUNDS = ["conservative", "nominal", "optimistic"]


def _robot(rnd, i):
    vendor, model = NAMES[i % len(NAMES)]
    w, l = rnd.uniform(0.35, 0.9), rnd.uniform(0.4, 1.2)
    price = rnd.choice([None, None, round(rnd.uniform(5e3, 9e4), 2), 0.0])
    return RobotSpec(**{
        "robot_id": f"rb_{i}", "class": rnd.choice(["A_service", "B_amr"]), "vendor": vendor, "model": model,
        "footprint_polygon_m": [(-l / 2, -w / 2), (l / 2, -w / 2), (l / 2, w / 2), (-l / 2, w / 2)],
        "height_m": rnd.choice([None, 1.25]), "max_speed_mps": rnd.choice([None, 1.2, 2.0]),
        "max_threshold_m": rnd.choice([None, 0.02]), "payload_kg": rnd.choice([None, 40, 1500.5]),
        "runtime_h": rnd.choice([None, 8, 12.5]),
        "price": {"value": price, "currency": rnd.choice(["USD", "EUR"]), "is_public": price is not None,
                  "source_url": f"https://example.com/rb_{i}/price" if price is not None else None},
        "fields_sources": {"max_speed_mps": {"url": f"https://example.com/rb_{i}/specs 2", "quote": "1.2 m/s",
                                             "retrieved_at": "2026-10-20T10:00:00Z"}},
        "verified_by_human": rnd.random() < 0.5,
    })


def _legs(rnd, seq, mode):
    out = []
    for i, (a, b) in enumerate(zip(seq, seq[1:])):
        reach = True
        if mode == "blocked" and i == len(seq) - 2:
            reach = False
        if mode == "pending" and i == 0:
            reach = None
        gap = rnd.choice([None, round(rnd.uniform(0.4, 1.5), 3)])
        out.append(LegFeasibility(from_wp=a, to_wp=b, reachable=reach, narrowest_gap_m=gap,
                                  required_gap_m=rnd.choice([None, 0.62]), path_length_m=rnd.uniform(2, 30),
                                  blocking_threshold="D2" if reach is None else rnd.choice([None, "D 7"]),
                                  reason="aisle too narrow" if reach is False else ""))
    return out


def make_random_analysis(seed: int) -> dict:
    rnd = random.Random(seed)
    n = rnd.randint(0, 6)
    seq = rnd.choice([["DOCK", "PICKUP", "T1", "DOCK"], ["Dock 1", "Table 3", "Dock 1"], ["A", "B"]])
    task = Task(sequence=seq)
    robots = [_robot(rnd, i) for i in rnd.sample(range(len(NAMES)), n)]
    all_failing = rnd.random() < 0.2
    batches, feas, opt = [], {}, {}
    for r in robots:
        mode = rnd.choice(["ok", "ok", "blocked", "pending", "missing"])
        if mode != "missing":
            feas[r.robot_id] = Feasibility(robot_id=r.robot_id, twin_id="tw", twin_version=1,
                                           legs=_legs(rnd, seq, mode), all_reachable=mode == "ok")
        for d in DENS:
            if rnd.random() < 0.25:
                continue  # missing density
            base = 0.0 if all_failing else rnd.choice([0.0, rnd.uniform(0.3, 1.0), 0.8995, 0.895, 0.9, 1.0])
            for k, bound in enumerate(BOUNDS):
                if rnd.random() < 0.15:
                    continue  # missing bound
                sr = min(1.0, max(0.0, base + (k - 1) * rnd.uniform(0, 0.05)))
                ne = rnd.choice([30, 100, 1000])
                res = BatchResults(success_rate=sr, ci95=(max(0, sr - 0.05), min(1, sr + 0.05)),
                                   median_mission_s=rnd.choice([None, rnd.uniform(20, 900)]),
                                   near_miss_per_mission=rnd.uniform(0, 3), stops_per_mission=rnd.uniform(0, 9),
                                   min_gap_m=rnd.choice([None, 0.05, 0.31]),
                                   fail_reasons={"blocked_timeout": int((1 - sr) * ne)} if sr < 1 else {},
                                   n_success=int(sr * ne), n_episodes=ne)
                batches.append(SimBatch(batch_id=f"sb_{r.robot_id}_{d}_{bound}", twin_id="tw", twin_version=1,
                                        robot_id=r.robot_id, task=task, density=d, geometry_bound=bound,
                                        n_episodes=ne, seed_base=7,
                                        results=None if rnd.random() < 0.05 else res))
        if rnd.random() < 0.4:
            opt[r.robot_id] = [{"change": {"move": {"T 4": {"dx": rnd.uniform(-0.5, 0.5), "dy": 0.1, "dyaw": 0.3}},
                                           "remove": ["C3"] if rnd.random() < 0.5 else []},
                                "description": "Move table 4 by 0.2 m",
                                "before": BatchResults(success_rate=rnd.random(), ci95=(0.0, 1.0), n_episodes=200),
                                "after": BatchResults(success_rate=rnd.random(), ci95=(0.0, 1.0), n_episodes=200),
                                "batch_ids": ["sb_b", "sb_a"]}]
    biz_kind = rnd.choice(["none", "ok", "ok", "negative", "huge", "lease", "zero"])
    cur = rnd.choice(CURRENCIES)
    biz = {}
    if biz_kind != "none":
        biz = {"staff_cost_per_hour": 18.5, "task_hours_per_day": 6, "days_per_week": 6, "currency": cur}
        if biz_kind == "negative":
            biz["staff_cost_per_hour"] = -5
        if biz_kind == "huge":
            biz["staff_cost_per_hour"] = 1e300
            biz["task_hours_per_day"] = 24
        if biz_kind == "zero":
            biz["staff_cost_per_hour"] = 0
        if biz_kind == "lease" and robots:
            biz["leases"] = {robots[0].robot_id: 1200}
        if robots and rnd.random() < 0.5:
            biz["quotes"] = {robots[-1].robot_id: rnd.choice([24000, 0, 1e7])}
    inv = None
    inv_facts = []
    if robots and rnd.random() < 0.5:
        rid = robots[0].robot_id
        inv_facts = [Fact(fact_id="f_r1_001", value=0.82, unit="ratio", label="Success rate, baseline probe",
                          origin=FactOrigin(type="investigation", ref="inv_1#step1"))]
        inv = Investigation(investigation_id="inv_1", analysis_id=f"an_{seed}", model_id="m", mode="offline",
                            experiments=[Experiment(
                                experiment_id="e1", robot_id=rid, hypothesis="Ninety percent of stops are at zone 2.",
                                kind="reduce_pedestrians_zone", change={"zone_id": "Z 2", "ped_scale": 0.5},
                                batch_id="pb_1", baseline_fact_ids=["f_r1_001"], result_fact_ids=[],
                                outcome="supported", next_action="conclude",
                                conclusion="Success was 82% [f_r1_001] at baseline, about 99% after.")],
                            findings=[Finding(finding_id="fd_1", robot_id=rid, claim="Two of three stops are at Z2.",
                                              evidence_fact_ids=[], object_ids=["C3"], confirmed_by_probe=False)])
    twin = Twin(twin_id="tw", site_id="site", version=1,
                accuracy=Accuracy(method=rnd.choice(["tape_check", "estimated"]),
                                  median_abs_err_m=rnd.choice([None, 0.021]), p90_abs_err_m=rnd.choice([None, 0.04])),
                floor=Floor(bounds_polygon=rnd.choice([[], [(0, 0), (8, 0), (8, 6), (0, 6)]])),
                occupancy=Occupancy(uri="occ.png"),
                thresholds=[Threshold(id="D1", segment=((0, 0), (1, 0)), height_m=0.015),
                            Threshold(id="D2", segment=((2, 0), (3, 0)), status="flagged_manual")],
                hazards=[Hazard(label=rnd.choice(["cable on floor", "step of 3 cm near door 2"]), confidence=0.7)],
                traffic=TrafficModel(source=rnd.choice(["observed", "user_estimated"])))
    return {
        "analysis_id": f"an_{seed}", "site": {"site_id": "s1", "name": rnd.choice(["Cafe 5", "Harbor Kitchen"])},
        "twin": twin,
        "measurements": [{"name": "aisle 3", "value_m": 0.82, "twin_value_m": 0.8}] if rnd.random() < 0.5 else [],
        "business": biz, "candidates": robots, "feasibility": feas, "batches": batches, "optimizer": opt,
        "investigation": inv, "investigation_facts": inv_facts,
        "citations": [{"url": "https://example.com/market/2026", "title": "Market check 2026",
                       "retrieved_at": "2026-10-25"}],
    }


@pytest.mark.parametrize("seed", range(120))
def test_random_offline_report_passes_first_time(seed):
    analysis = make_random_analysis(seed)
    facts = build_facts(analysis)
    verdicts = derive_verdicts(analysis)
    md = write_report(facts, build_citations(analysis), collect_findings(analysis, facts), verdicts,
                      investigations(analysis), mode="offline", title="Clearance Report: " + analysis["site"]["name"])
    r = check_report(md, facts)
    assert r["ok"], (seed, r["errors"][:4])
    for v in verdicts:  # the comparison row of each robot carries the code verdict
        name = next(f.value for f in facts if getattr(f, "key", "") == f"robot.name|{v.robot_id}")
        row = next(l for l in md.splitlines() if l.startswith(f"| {name} | "))
        assert row.split(" | ")[1] == v.label
    # no number written as a word, no scientific notation anywhere in code-written text
    assert not re.search(r"\d[eE][+-]?\d", md)


@pytest.mark.parametrize("seed", range(0, 120, 7))
def test_random_generate_report_end_to_end(seed):
    out = generate_report(make_random_analysis(seed), save=False)
    assert out["check"]["ok_first_pass"] and out["check"]["ok_final"]
    assert check_report(out["md"], out["facts"])["ok"]


@pytest.mark.parametrize("seed", range(40))
def test_tampered_random_report_is_repaired(seed):
    """An adversarial rewrite of a valid report (numbers changed, words, swapped tags, wrong labels) is always
    repaired by enforce into markdown that passes, keeps tables rectangular and ends."""
    rnd = random.Random(seed)
    analysis = make_random_analysis(seed)
    facts = build_facts(analysis)
    md = write_report(facts, build_citations(analysis), collect_findings(analysis, facts), derive_verdicts(analysis),
                      investigations(analysis), mode="offline")
    lines = md.splitlines()
    for _ in range(25):
        i = rnd.randrange(len(lines))
        op = rnd.randrange(6)
        if op == 0:
            lines[i] = re.sub(r"\d+", lambda m: str(int(m.group(0)) + 7), lines[i], count=1)
        elif op == 1:  # inside the last cell of a table row, appended to prose otherwise
            lines[i] = (lines[i].rstrip()[:-1].rstrip() + " about ninety percent |"
                        if lines[i].rstrip().endswith("|") else lines[i] + " About ninety percent succeed.")
        elif op == 2:
            lines[i] = lines[i].replace("Not cleared", "Cleared").replace("Cleared with changes", "**not** cleared")
        elif op == 3:
            lines[i] = re.sub(r"\[f_\d+\]", "[f_001]", lines[i])
        elif op == 4 and not lines[i].startswith("|"):
            lines.insert(i, "## References\n\nEvery robot succeeds 99% of the time.")
        else:
            lines[i] = lines[i].replace("%", "％")
    bad = "\n".join(lines)
    calls = []
    out = enforce(bad, facts, lambda prev, errs: calls.append(1) or prev)
    assert len(calls) <= 1 and out["ok_final"]
    assert check_report(out["md"], facts)["ok"]
    from agents.factcheck.mdparse import parse_blocks
    widths, blocks = [], parse_blocks(out["md"])
    for b in blocks:
        if b.kind == "table_row":
            widths.append(len(b.cells))
        elif b.kind != "table_sep" and widths:
            assert len(set(widths)) == 1, widths
            widths = []
