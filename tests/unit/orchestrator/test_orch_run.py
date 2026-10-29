import json

import numpy as np
import pytest

from clearance_core import storage
from clearance_core.grid import Grid
from clearance_core.schemas import Investigation, Task, Threshold, Verdict

from agents.investigate.tools import SimAPI
from agents.llm.client import LLM, OfflineLLM
from agents.orchestrator import tavily_tools
from agents.orchestrator.candidates import route_gaps, static_filter
from agents.orchestrator.dispatch import inline_dispatcher, make_spec
from agents.orchestrator.run import STAGES, apply_overrides, normalize_budget, run_analysis

from orch_doubles import make_twin, optimizer_double, rasterize, robot, write_report_double

BUDGET = {"densities": ["normal", "peak"], "bounds": ["nominal", "conservative"], "episodes": 40}


def go(world, budget=None, **kw):
    kw.setdefault("sim", world["api"])
    kw.setdefault("modules", world["modules"])
    return run_analysis("an1", world["twin"], world["robots"], world["task"], {"staff_cost_per_h": 20},
                        budget=budget or BUDGET, **kw)


def test_full_stage_progression(world, data_dir):
    seen = []
    doc = go(world, progress=lambda d: seen.append({k: v["state"] for k, v in d["stages"].items()}))
    assert doc["status"] == "done"
    assert [doc["stages"][s]["state"] for s in STAGES] == ["done"] * len(STAGES)
    # stages ran strictly in order: each one turns "running" only after the previous one finished
    order = []
    for snap in seen:
        for s in STAGES:
            if snap[s] == "running" and s not in order:
                order.append(s)
    assert order == STAGES
    # candidates: code-first filters, only reviewed robots
    assert doc["candidates"] == ["bot_a", "bot_b"]
    rules = {e["robot_id"]: e["rule"] for e in doc["excluded"]}
    assert rules == {"bot_wide": "footprint_vs_route_gap", "bot_unreviewed": "verified_by_human"}
    assert set(doc["robots"]) == {"bot_a", "bot_b"}
    # feasibility per bound, conservative copy for the report
    assert set(doc["feasibility_by_bound"]["bot_a"]) == {"nominal", "conservative"}
    assert doc["feasibility"]["bot_a"]["legs"][0]["reachable"] is True
    # batches = robots x densities x bounds, all with the budget's episodes
    assert len(doc["batches"]) == 2 * 2 * 2
    assert all(storage.read_json(f"analyses/an1/batches/{b}.json")["n_episodes"] == 40 for b in doc["batches"])
    # investigation only for the robot below the success threshold
    assert list(doc["investigation_ids"]) == ["bot_a"]
    inv = Investigation.model_validate(storage.read_json("analyses/an1/investigation.json"))
    assert inv.investigation_id == doc["investigation_id"] and inv.mode == "offline"
    assert inv.findings and inv.findings[0].confirmed_by_probe
    # optimizer starts from the investigation's objects
    assert optimizer_double.hints == ["B1"] and "bot_a" in doc["optimizer"]
    # verdicts from decide_verdict only, with a confirmation batch at normal/conservative
    v = {x["robot_id"]: Verdict.model_validate(x) for x in doc["verdicts"]}
    assert v["bot_b"].label == "Cleared"
    assert v["bot_a"].label == "Cleared with changes" and v["bot_a"].required_changes
    conf = doc["confirmation_batches"]["bot_a"]
    cb = storage.read_json(f"analyses/an1/batches/{conf['batch_id']}.json")
    assert (cb["density"], cb["geometry_bound"], cb["n_episodes"]) == ("normal", "conservative", 40)
    assert conf["change"] == {"move": {"B1": {"dx": 0.0, "dy": -0.05, "dyaw": 0.0}}}
    # verdict success checks are tagged with facts from the facts pack
    succ = next(c for c in v["bot_b"].as_is if "success" in c.rule)
    assert succ.fact_ids and succ.fact_ids[0].startswith("f_")
    # report artifacts; write_report got verdicts + investigation
    facts, cites, findings, verdicts, investigation = write_report_double.args
    assert {f.fact_id for f in facts} >= {f for x in inv.findings for f in x.evidence_fact_ids}
    assert isinstance(investigation, Investigation) and findings == inv.findings
    assert [x.label for x in verdicts] == ["Cleared with changes", "Cleared"]
    assert (data_dir / "analyses/an1/report.md").read_text(encoding="utf-8").startswith("# Clearance Report")
    assert doc["report"]["factcheck"]["ok"] is True
    assert len(storage.read_json("analyses/an1/facts.json")) == len(facts)
    on_disk = storage.read_json("analyses/an1/analysis.json")
    assert on_disk["status"] == "done" and on_disk["verdicts"] == doc["verdicts"]
    # usage: Super call for ordering + sim batches logged
    from agents.llm.usage import summary
    kinds = {r["kind"] for r in summary("an1")["by_kind"]}
    assert {"orchestrate", "sim.batch", "sim.confirm", "sim.probe"} <= kinds


def test_tool_trail_records_callers(world, data_dir):
    doc = go(world)
    callers = {(t["caller"], t["tool"]) for t in doc["tool_calls"]}
    assert ("super", "list_candidates") in callers
    assert {("code", "run_feasibility"), ("code", "run_batch"), ("code", "investigate"),
            ("code", "run_optimizer")} <= callers


def test_missing_modules_are_skipped_with_reason(world, data_dir):
    mods = {k: None for k in ("optimize_layout", "build_facts", "write_report", "check_report", "build_citations",
                              "compute_roi")}
    doc = go(world, modules=mods)
    st = {s: doc["stages"][s]["state"] for s in STAGES}
    assert doc["status"] == "done"
    assert st["optimizer"].startswith("skipped: optimize_layout")
    assert st["report"].startswith("skipped: build_facts")
    assert st["verdicts"] == "done"
    v = {x["robot_id"]: x["label"] for x in doc["verdicts"]}
    assert v == {"bot_a": "Not cleared", "bot_b": "Cleared"}
    facts = storage.read_json("analyses/an1/facts.json")
    assert facts and all(f["origin"]["type"] == "investigation" for f in facts)


def test_without_sim_everything_degrades(world, data_dir):
    doc = go(world, sim=SimAPI(source="unavailable: test"))
    st = doc["stages"]
    assert st["candidates"]["state"] == "done"  # gap pre-screen skipped (no grid), filters still applied
    assert st["feasibility"]["state"].startswith("skipped:")
    assert st["batches"]["state"].startswith("skipped:")
    assert st["investigation"]["state"].startswith("skipped:")
    assert {v["label"] for v in doc["verdicts"]} == {"Not cleared"}
    assert "bot_wide" in doc["candidates"]
    assert any("pre-screen skipped" in n for n in doc["notes"])


def test_critical_stage_failure_stops(world, data_dir):
    def failing(specs, on_result=None):
        raise RuntimeError("serverless quota")
    doc = go(world, dispatcher=failing)
    assert doc["status"] == "failed"
    assert doc["stages"]["batches"]["state"].startswith("failed: RuntimeError")
    assert all(doc["stages"][s]["state"] == "skipped: stage 'batches' failed" for s in STAGES[3:])
    assert storage.read_json("analyses/an1/analysis.json")["status"] == "failed"


def test_custom_dispatcher_gets_specs(world, data_dir):
    got = []
    inner = inline_dispatcher(world["sim"].run_batch)

    def dispatcher(specs, on_result=None):
        got.extend(specs)
        return inner(specs)  # ignores on_result: orchestrator must still register results
    doc = go(world, dispatcher=dispatcher, modules={**world["modules"], "optimize_layout": None})
    assert len(doc["batches"]) == 8 and doc["stages"]["batches"]["progress"] == 1.0
    json.dumps(got)  # JSON-serializable for Serverless Jobs
    assert {s["out_prefix"] for s in got} == {"analyses/an1/batches/"}
    assert all(s["seed_base"] == 1000 for s in got)


def test_unreachable_bound_skips_batches(world, data_dir):
    world["robots"] = [robot("bot_c", 0.88)]  # nominal ok (<0.9), conservative blocked (>=0.85)
    doc = go(world)
    bounds = {doc["batch_index"][b]["bound"] for b in doc["batches"]}
    assert bounds == {"nominal"}
    assert {s["bound"] for s in doc["skipped_batches"]} == {"conservative"}
    (v,) = doc["verdicts"]
    assert v["label"] == "Not cleared" and "no path" in v["main_reason"]


def test_budget_rules(world, data_dir):
    b, notes = normalize_budget({"max_robots": 50, "densities": ["quiet", "bogus"], "bounds": ["optimistic"],
                                 "investigation": {"max_experiments": 99, "probe_episodes": 500}})
    assert b["max_robots"] == 6 and b["densities"] == ["quiet", "normal"] and b["bounds"] == ["optimistic",
                                                                                               "conservative"]
    assert b["investigation"]["max_experiments"] == 6 and b["investigation"]["probe_episodes"] == 30
    assert len(notes) == 2
    world["robots"] = [robot(f"r{i}", 0.4 + i * 0.01) for i in range(8)]
    doc = go(world, budget={**BUDGET, "max_total_episodes": 120})
    assert len(doc["candidates"]) == 6
    assert sum(1 for e in doc["excluded"] if e["rule"] == "budget") == 2
    assert doc["episodes_per_batch"] == 120 // (6 * 4)


def test_llm_ordering_and_number_free_explanations(world, data_dir):
    reply = json.dumps({"order": ["bot_b", "bot_a"], "explanations": {
        "bot_b": "Narrower body suits the corridor.", "bot_a": "Reaches 3 tables quickly."}})
    off = OfflineLLM({"super": [{"tool_calls": [{"name": "list_candidates", "arguments": {}},
                                                {"name": "run_batch", "arguments": {"robot_id": "bot_a"}}]}, reply]})
    doc = go(world, llm=LLM("offline", offline=off))
    assert doc["candidates"] == ["bot_b", "bot_a"]
    assert doc["candidate_explanations"] == {"bot_b": "Narrower body suits the corridor."}
    assert any("dropped" in n for n in doc["notes"])
    tool_msgs = [m for m in off.calls[1]["messages"] if m["role"] == "tool"]
    assert "not available while ordering" in tool_msgs[1]["content"]


def test_llm_invalid_order_falls_back_to_code(world, data_dir):
    off = OfflineLLM({"super": [json.dumps({"order": ["bot_a", "bot_zz"]})]})
    doc = go(world, llm=LLM("offline", offline=off))
    assert doc["candidates"] == ["bot_a", "bot_b"]
    assert any("permutation" in n for n in doc["notes"])


def test_tavily_offline_and_market_tool(world, data_dir):
    assert tavily_tools.tavily_search("x")["results"] == []
    off = OfflineLLM({"super": [{"tool_calls": [{"name": "tavily_search", "arguments": {"query": "bot_a price"}}]},
                                json.dumps({"order": ["bot_a", "bot_b"]})]})
    doc = go(world, llm=LLM("offline", offline=off))
    assert any(t["tool"] == "tavily_search" and t["result"]["mode"] == "offline" for t in doc["tool_calls"])
    assert doc["citations"] == []


# ---------------- static filters

def corridor_grid(gap_cells):
    g = np.zeros((40, 60), np.uint8)
    g[10:10 + gap_cells, :] = 255
    return Grid(g, 0.05, (0.0, 0.0))


@pytest.mark.parametrize("cells", [10, 16, 24])
def test_route_gap_matches_corridor_width(cells):
    grid = corridor_grid(cells)
    twin = make_twin()
    y = grid.cell_to_world(10 + cells // 2, 0)[1]
    twin.waypoints[0].xy, twin.waypoints[1].xy = (0.3, y), (2.7, y)
    (leg,) = route_gaps(grid, twin, Task(sequence=["DOCK", "DEST"]))
    assert abs(leg["gap_m"] - cells * 0.05) <= 0.1


def test_static_filter_rules(data_dir):
    twin = make_twin([Threshold(id="D1", segment=((3.0, 0.0), (3.0, 2.0)), height_m=0.04),
                      Threshold(id="D2", segment=((1.0, 0.0), (1.0, 0.1)), status="flagged_manual")])
    grid = rasterize(twin)
    rs = [robot("ok_high_step", 0.5, max_threshold=0.05), robot("low_step", 0.5, max_threshold=0.02),
          robot("heavy", 0.5, payload=5, max_threshold=0.05), robot("nofoot", 0.5), robot("wide", 1.4)]
    rs[3].footprint_polygon_m = None
    out = static_filter(twin, rs, Task(sequence=["DOCK", "DEST"]), {"payload_kg": 10}, grid=grid)
    rules = {e["robot_id"]: e["rule"] for e in out["excluded"]}
    assert rules == {"low_step": "threshold", "heavy": "payload", "nofoot": "footprint",
                     "wide": "footprint_vs_route_gap"}
    (c,) = out["candidates"]
    assert c["robot_id"] == "ok_high_step" and any("D2" in n for n in c["notes"])
    assert any("payload not published" in n for n in c["notes"])
    assert 0.8 <= c["route_gap_m"] <= 1.0


def test_missing_waypoint_and_class_filter(data_dir):
    twin = make_twin()
    out = static_filter(twin, [robot("a", 0.5)], Task(sequence=["DOCK", "NOWHERE"]), {"robot_class": "B_amr"},
                        grid=rasterize(twin))
    assert out["excluded"][0]["rule"] == "class"
    assert out["route_gaps"][0]["note"] == "waypoint missing in twin"


def test_apply_overrides():
    twin = make_twin()
    t2 = apply_overrides(twin, {"move": {"B1": {"dx": 0.1, "dy": -0.2, "dyaw": 0.5}}})
    assert t2.primitives[0].pose.xyz[:2] == pytest.approx((2.1, 0.35)) and t2.primitives[0].pose.yaw == 0.5
    assert twin.primitives[0].pose.xyz[0] == 2.0
    assert apply_overrides(twin, {"remove": ["B1"]}).primitives == []
    added = apply_overrides(twin, {"add_boxes": [twin.primitives[0].model_dump()]})
    assert len(added.primitives) == 2


def test_make_spec_round_trip(world):
    rb = world["robots"][0]
    spec = make_spec(world["twin"], rb, world["task"], "normal", "nominal", 5, 7, overrides={"remove": ["B1"]})
    out = inline_dispatcher(world["sim"].run_batch)([json.loads(json.dumps(spec))])
    assert out[0].n_episodes == 5 and world["sim"].calls[-1]["overrides"] == {"remove": ["B1"]}


def test_real_report_modules_integrate(world, data_dir):
    pytest.importorskip("agents.report.facts")
    pytest.importorskip("agents.report.writer")
    pytest.importorskip("agents.factcheck.check")
    doc = go(world, modules={"optimize_layout": optimizer_double})
    assert doc["status"] == "done"
    assert doc["stages"]["report"]["state"] == "done", doc["stages"]["report"]
    md = (data_dir / "analyses/an1/report.md").read_text(encoding="utf-8")
    assert "[f_" in md


def test_awaiting_review_robots_only_with_explicit_option(world, data_dir):
    doc = go(world)
    assert "bot_unreviewed" not in doc["candidates"]
    assert doc["inputs"]["include_awaiting_review"] is False
    doc = go(world, include_awaiting_review=True)
    assert "bot_unreviewed" in doc["candidates"]
    assert doc["inputs"]["include_awaiting_review"] is True
    det = {c["robot_id"]: c for c in doc["candidate_details"]}
    assert det["bot_unreviewed"]["spec_status"] == "awaiting review"
    assert det["bot_a"]["spec_status"] == "human-reviewed"
    assert "catalog specs awaiting review" in det["bot_unreviewed"]["notes"]
    assert any("awaiting review" in n for n in doc["notes"])
    assert not any(e["robot_id"] == "bot_unreviewed" for e in doc["excluded"])


def test_static_pinch_seeds_optimizer_for_statically_blocked_robot(world, data_dir):
    from agents.orchestrator.run import Orchestrator
    from clearance_core.schemas import Feasibility, LegFeasibility
    orch = Orchestrator("an_pinch", world["twin"], world["robots"], world["task"], {}, BUDGET, sim=world["api"],
                        modules=world["modules"])
    leg = LegFeasibility(from_wp="A", to_wp="B", reachable=False, narrowest_gap_m=0.3, required_gap_m=0.5,
                         narrowest_xy=(1.0, 1.0))
    orch.feas["bot_a"] = {"nominal": Feasibility(robot_id="bot_a", twin_id="t", twin_version=1, legs=[leg],
                                                 all_reachable=False)}
    bn = orch._bottleneck("bot_a")
    grid = orch._grid()
    assert bn is not None and bn.shape == grid.data.shape and bn.sum() == 1.0
    r, c = np.unravel_index(int(np.argmax(bn)), bn.shape)
    x, y = grid.cell_to_world(r, c)
    assert abs(x - 1.0) <= grid.res and abs(y - 1.0) <= grid.res
    assert orch.doc["pinch_seed"]["bot_a"][0]["from"] == "A"
