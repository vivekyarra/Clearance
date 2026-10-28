import json

import numpy as np
import pytest

from clearance_core import storage
from clearance_core.schemas import Investigation

from agents.investigate.loop import ids_for, investigate, load_facts, run_investigation
from agents.investigate.tools import SimAPI, Toolbox, unsupported_numbers
from agents.llm.client import LLM, OfflineLLM

from inv_doubles import SimDouble, make_robot


def sim_api(sim):
    return SimAPI(sim.rasterize_twin, sim.run_batch, None)


def run(scene, **kw):
    kw.setdefault("sim", sim_api(scene["sim"]))
    return run_investigation("a1", scene["twin"], scene["robot"], scene["task"], scene["batches"], **kw)


def toolbox(scene, **kw):
    inv_id, prefix = ids_for("a1", scene["robot"].robot_id)
    return Toolbox("a1", inv_id, prefix, scene["twin"], scene["robot"], scene["task"], scene["batches"],
                   sim_api(scene["sim"]), **kw)


# ---------------- end to end, offline deterministic policy

def test_offline_end_to_end(scene, data_dir):
    r = run(scene)
    inv = r.investigation
    assert inv.mode == "offline" and inv.model_id == "deterministic-policy"
    assert [s.tool for s in inv.steps] == ["get_batch_summary", "get_bottlenecks", "get_objects_near", "get_gap_at",
                                           "run_probe", "record_finding", "finish"]
    assert inv.stop_reason.startswith("finish:")
    (f,) = inv.findings
    assert f.confirmed_by_probe and f.object_ids == ["T1"] and "T1" in f.suggested_change["move"]
    known = {x.fact_id for x in r.facts}
    assert set(f.evidence_fact_ids) <= known
    assert all(x.origin.type == "investigation" and x.origin.ref.startswith(inv.investigation_id) for x in r.facts)
    (e,) = inv.experiments
    assert e.kind == "move_object" and e.outcome == "supported" and e.next_action == "conclude"
    assert e.conclusion == f.claim and set(e.result_fact_ids) <= known
    # every sim probe ran on nominal geometry with <= 30 episodes
    assert all(c["bound"] == "nominal" and c["n"] <= 30 for c in scene["sim"].calls)
    saved = storage.read_json("analyses/a1/investigation.json")
    Investigation.model_validate(saved)
    assert saved["mode"] == "offline" and len(saved["facts"]) == len(r.facts)
    assert [x.fact_id for x in load_facts("a1")] == [x.fact_id for x in r.facts]


def test_offline_is_deterministic(scene, data_dir):
    a = run(scene, persist=False).investigation.model_dump()
    scene["sim"] = SimDouble()
    b = run(scene, persist=False).investigation.model_dump()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_inconclusive_probe_digs_further_and_is_not_confirmed(scene, data_dir):
    scene["sim"] = SimDouble(base=0.5, fixed=0.55, zone_fixed=0.52)
    inv = run(scene).investigation
    tools = [s.tool for s in inv.steps]
    assert tools.count("run_probe") == 2 and len(tools) == 8 and tools[-1] == "finish"
    first, second = inv.experiments
    assert first.outcome == "inconclusive" and first.next_action == "dig_further" and first.conclusion
    assert second.kind == "remove_object" and second.next_action == "conclude"
    assert not inv.findings[0].confirmed_by_probe


def test_no_bottleneck_above_threshold_finishes_early(scene, data_dir):
    r = run(scene, toolbox_kwargs={"min_bottleneck_share": 0.99})
    assert [s.tool for s in r.investigation.steps] == ["get_batch_summary", "get_bottlenecks", "finish"]
    assert r.investigation.findings == [] and scene["sim"].calls == []


def test_max_steps_cap(scene, data_dir):
    r = run(scene, max_steps=3)
    assert len(r.investigation.steps) == 3 and r.investigation.stop_reason == "max_steps"


def test_without_sim_the_loop_still_concludes(scene, data_dir):
    r = run(scene, sim=SimAPI(source="unavailable: test"))
    steps = {s.tool: s for s in r.investigation.steps}
    assert steps["get_gap_at"].result_summary["ok"] is False  # no grid, no PNG
    assert steps["run_probe"].result_summary["ok"] is False
    assert r.investigation.findings and not r.investigation.findings[0].confirmed_by_probe


# ---------------- Super via tool calling (scripted OfflineLLM)

def test_llm_policy_with_citation_enforcement(scene, data_dir):
    inv_id, prefix = ids_for("a1", scene["robot"].robot_id)
    f1 = f"{prefix}001"  # success rate of sb_nn (first fact of get_batch_summary)
    script = [
        {"content": "Look at results first.", "tool_calls": [{"name": "get_batch_summary", "arguments": {}}]},
        {"tool_calls": [{"name": "record_finding", "arguments": {
            "claim": "Fails at the counter [f_made_up_999].", "evidence_fact_ids": ["f_made_up_999"]}}]},
        {"tool_calls": [{"name": "record_finding", "arguments": {
            "claim": "Success is only 42% here.", "evidence_fact_ids": [f1]}}]},
        {"tool_calls": [{"name": "record_finding", "arguments": {
            "claim": f"Success is only 50% at normal traffic [{f1}].", "evidence_fact_ids": [f1]}}]},
        {"tool_calls": [{"name": "finish", "arguments": {"reason": "explained"}}]},
    ]
    llm = LLM("offline", offline=OfflineLLM({"super": script}))
    r = run(scene, llm=llm, policy="llm")
    inv = r.investigation
    res = [s.result_summary for s in inv.steps]
    assert res[1]["ok"] is False and "unknown fact ids" in res[1]["error"]
    assert res[2]["ok"] is False and "42%" in res[2]["error"]
    assert res[3]["ok"] is True
    assert len(inv.findings) == 1 and inv.findings[0].evidence_fact_ids == [f1]
    assert inv.model_id.startswith("offline:") and inv.stop_reason == "finish: explained"
    assert inv.steps[0].thought == "Look at results first."
    # the model saw tool results with fact ids
    sent = llm.offline.calls[1]["messages"]
    assert sent[-1]["role"] == "tool" and f1 in sent[-1]["content"]


def test_llm_policy_stops_when_no_new_bottleneck(scene, data_dir):
    script = [{"tool_calls": [{"name": "get_bottlenecks", "arguments": {"top_k": 3}}]}] * 3
    r = run(scene, llm=LLM("offline", offline=OfflineLLM({"super": script})), policy="llm",
            toolbox_kwargs={"min_bottleneck_share": 0.99})
    assert r.investigation.stop_reason == "no_new_bottleneck_above_threshold"


def test_llm_policy_text_only_replies_stop(scene, data_dir):
    r = run(scene, llm=LLM("offline", offline=OfflineLLM({"super": ["hmm", "still thinking"]})), policy="llm")
    assert r.investigation.stop_reason == "model_stopped_without_finish" and len(r.investigation.steps) == 2


def test_llm_unknown_tool_and_bad_args(scene, data_dir):
    script = [{"tool_calls": [{"name": "delete_twin", "arguments": {}}, {"name": "get_gap_at", "arguments": {}},
                              {"name": "finish", "arguments": {"reason": "x"}}]}]
    r = run(scene, llm=LLM("offline", offline=OfflineLLM({"super": script})), policy="llm")
    res = [s.result_summary for s in r.investigation.steps]
    assert "unknown tool" in res[0]["error"] and "bad arguments" in res[1]["error"]


def test_contract_entry_point(scene, data_dir):
    inv = investigate("a1", scene["twin"], scene["robot"], scene["task"], scene["batches"],
                      sim=sim_api(scene["sim"]), other_robots=[make_robot("bot_b")])
    assert isinstance(inv, Investigation) and inv.experiments


# ---------------- tools

def test_experiment_cap_and_probe_limits(scene, data_dir):
    tb = toolbox(scene, max_experiments=2)
    r1, _ = tb.call("run_probe", {"hypothesis": "h1", "overrides": {"move": {"T1": {"dx": 0.1}}},
                                  "n_episodes": 500}, 1)
    assert r1["ok"] and r1["n_episodes"]["value"] == 30
    r2, _ = tb.call("run_probe", {"hypothesis": "h2", "overrides": {"remove": ["T1"]}}, 2)
    r3, _ = tb.call("run_probe", {"hypothesis": "h3", "overrides": {"remove": ["C9"]}}, 3)
    assert r2["ok"] and not r3["ok"] and "cap" in r3["error"]
    assert len(tb.experiments) == 2 and tb.experiments[0].next_action == "dig_further"
    assert all(c["bound"] == "nominal" for c in scene["sim"].calls)


def test_probe_validation(scene, data_dir):
    tb = toolbox(scene)
    cases = [({"hypothesis": "h", "overrides": {"remove": ["S1"]}}, "not movable"),
             ({"hypothesis": "h", "overrides": {"move": {"NOPE": {"dx": 0.1}}}}, "unknown object"),
             ({"hypothesis": "h", "overrides": {"move": {"T1": {"dx": 3.0}}}}, "too large"),
             ({"hypothesis": "h", "overrides": {"teleport": {}}}, "unknown override"),
             ({"hypothesis": "", "overrides": {"remove": ["T1"]}}, "hypothesis"),
             ({"hypothesis": "h"}, "needs a change"),
             ({"hypothesis": "h", "zone_id": "Z_NONE", "ped_scale": 0.5}, "unknown traffic zone"),
             ({"hypothesis": "h", "zone_id": "Z_COUNTER", "ped_scale": 1.5}, "ped_scale"),
             ({"hypothesis": "h", "zone_id": "Z_COUNTER"}, "go together"),
             ({"hypothesis": "h", "robot_id": "bot_zz"}, "unknown robot_id"),
             ({"hypothesis": "h", "density": "rush"}, "density"),
             ({"hypothesis": "success will reach 99%", "overrides": {"remove": ["T1"]}}, "99%")]
    for args, msg in cases:
        out, _ = tb.call("run_probe", args, 1)
        assert not out["ok"] and msg in out["error"], (args, out)
    assert scene["sim"].calls == [] and tb.experiments == []


def test_zone_density_and_robot_swap_experiments(scene, data_dir):
    tb = toolbox(scene, other_robots=[make_robot("bot_b")])
    z, _ = tb.call("run_probe", {"hypothesis": "peds at counter", "zone_id": "Z_COUNTER", "ped_scale": 0.25}, 1)
    assert z["ok"] and z["kind"] == "reduce_pedestrians_zone"
    assert scene["sim"].calls[-1]["overrides"] == {"ped_zone_scale": {"Z_COUNTER": 0.25}}
    d, _ = tb.call("run_probe", {"hypothesis": "peak traffic", "density": "peak"}, 2)
    assert d["ok"] and d["kind"] == "change_density" and d["ran_baseline_probe"] is True
    assert tb.sim_runs == 3  # zone probe + baseline probe at peak + peak probe
    s, _ = tb.call("run_probe", {"hypothesis": "a narrower robot fits", "robot_id": "bot_b"}, 3)
    assert s["ok"] and s["kind"] == "swap_robot" and s["outcome"] == "supported"
    assert scene["sim"].calls[-1]["robot"] == "bot_b"
    assert [e.kind for e in tb.experiments] == ["reduce_pedestrians_zone", "change_density", "swap_robot"]
    # a swapped-robot probe never confirms an object change for this robot
    f, _ = tb.call("record_finding", {"claim": "x", "evidence_fact_ids": [s["success_rate"]["fact_id"]],
                                      "suggested_change": {"remove": "T1"}}, 4)
    assert f["ok"] and f["confirmed_by_probe"] is False


def test_baseline_probe_needs_room_under_cap(scene, data_dir):
    tb = toolbox(scene, max_experiments=1)
    out, _ = tb.call("run_probe", {"hypothesis": "peak", "density": "peak"}, 1)
    assert not out["ok"] and "cap" in out["error"] and scene["sim"].calls == []


def test_confirmation_requires_exact_counterfactual(scene, data_dir):
    tb = toolbox(scene)
    p, _ = tb.call("run_probe", {"hypothesis": "h", "overrides": {"move": {"T1": {"dx": 0.3, "dy": 0.0}},
                                                                  "remove": ["C9"]}}, 1)
    ev = [p["improvement"]["fact_id"]]
    f, _ = tb.call("record_finding", {"claim": "c", "evidence_fact_ids": ev,
                                      "suggested_change": {"object_id": "T1", "dx": 0.3, "dy": 0.0}}, 2)
    assert f["ok"] and not f["confirmed_by_probe"]  # probe changed two objects
    p2, _ = tb.call("run_probe", {"hypothesis": "h", "overrides": {"move": {"T1": {"dx": 0.3, "dy": 0.0}}}}, 3)
    f2, _ = tb.call("record_finding", {"claim": "c", "evidence_fact_ids": ev,
                                       "suggested_change": {"object_id": "T1", "dx": 0.1, "dy": 0.0}}, 4)
    assert not f2["confirmed_by_probe"]  # different move than the one probed
    f3, _ = tb.call("record_finding", {"claim": "c", "evidence_fact_ids": ev,
                                       "suggested_change": {"move": {"T1": {"dx": 0.3, "dy": 0.0}}}}, 5)
    assert f3["confirmed_by_probe"] and f3["probe_id"] == p2["probe_id"]
    assert p2["improvement"]["fact_id"] in f3["evidence_fact_ids"]
    assert tb.findings[-1].suggested_change == {"move": {"T1": {"dx": 0.3, "dy": 0.0, "dyaw": 0.0}}}


def test_record_finding_validation(scene, data_dir):
    tb = toolbox(scene)
    s, _ = tb.call("get_batch_summary", {}, 1)
    fid = s["batches"][0]["success_rate"]["fact_id"]
    bad = [({"claim": "", "evidence_fact_ids": [fid]}, "non-empty"),
           ({"claim": "c", "evidence_fact_ids": []}, "at least one"),
           ({"claim": "c", "evidence_fact_ids": [fid], "object_ids": ["ZZ"]}, "unknown object"),
           ({"claim": "c", "evidence_fact_ids": [fid], "suggested_change": {"remove": "S1"}}, "not movable"),
           ({"claim": "c", "evidence_fact_ids": [fid], "experiment_id": "ex_nope"}, "unknown experiment"),
           ({"claim": "c", "evidence_fact_ids": [fid], "location_xy": ["a", 1]}, "xy")]
    for args, msg in bad:
        out, _ = tb.call("record_finding", args, 2)
        assert not out["ok"] and msg in out["error"], (args, out)
    ok, _ = tb.call("record_finding", {"claim": f"Table T1 and waypoint T7 matter; success 0.50 [{fid}]",
                                       "evidence_fact_ids": []}, 3)
    assert ok["ok"] and ok["evidence_fact_ids"] == [fid]


def test_gap_and_objects_tools(scene, data_dir):
    tb = toolbox(scene)
    g, fids = tb.call("get_gap_at", {"xy": [1.0, 1.5]}, 1)
    assert g["ok"] and abs(g["gap_m"]["value"] - 2 * g["nearest_obstacle_m"]["value"]) < 1e-9
    assert g["robot_width_m"]["value"] == 0.5 and len(fids) == 4
    inside, _ = tb.call("get_gap_at", {"xy": [2.0, 1.75]}, 2)
    assert inside["in_obstacle"] and inside["gap_m"]["value"] == 0
    out, _ = tb.call("get_gap_at", {"xy": [50, 50]}, 3)
    assert not out["ok"] and "outside" in out["error"]
    o, _ = tb.call("get_objects_near", {"xy": [2.0, 2.2], "r": 0.5}, 4)
    assert [x["id"] for x in o["objects"]] == ["T1"] and o["zone_ids"] == ["Z_COUNTER"]
    bad, _ = tb.call("get_objects_near", {"xy": [2.0, 2.2], "r": 99}, 5)
    assert not bad["ok"]


def test_bottlenecks_ranked_and_examined(scene, data_dir):
    tb = toolbox(scene)
    b, _ = tb.call("get_bottlenecks", {"top_k": 5}, 1)
    spots = b["bottlenecks"]
    assert len(spots) == 2 and spots[0]["share_of_total"]["value"] > spots[1]["share_of_total"]["value"]
    assert abs(sum(s["share_of_total"]["value"] for s in spots) - 1.0) < 1e-3
    assert "T1" in spots[0]["nearby_object_ids"] and b["batches_used"] == ["sb_nn"]
    fid = spots[0]["share_of_total"]["fact_id"]
    tb.call("record_finding", {"claim": "c", "evidence_fact_ids": [fid], "location_xy": spots[0]["xy"]}, 2)
    b2, _ = tb.call("get_bottlenecks", {"top_k": 5}, 3)
    assert b2["bottlenecks"][0]["examined"] and not b2["bottlenecks"][1]["examined"]
    assert not tb.no_new_bottleneck


def test_bottleneck_map_missing_or_empty(scene, data_dir):
    for b in scene["batches"]:
        b.bottleneck_uri = None
    tb = toolbox(scene)
    out, _ = tb.call("get_bottlenecks", {}, 1)
    assert not out["ok"]
    np.save(storage.path("analyses/a1/zero.npy"), np.zeros((60, 80), np.float32))
    scene["batches"][0].bottleneck_uri = "analyses/a1/zero.npy"
    tb = toolbox(scene)
    out, _ = tb.call("get_bottlenecks", {}, 1)
    assert out["ok"] and out["bottlenecks"] == [] and tb.no_new_bottleneck


def test_episode_events(scene, data_dir):
    tb = toolbox(scene)
    e, _ = tb.call("get_episode_events", {"batch_id": "sb_nn", "type": "stop", "limit": 1}, 1)
    assert e["count"]["value"] == 2 and e["episodes_with_event"]["value"] == 2 and len(e["sample"]) == 1
    assert e["per_mission"]["value"] == 0.02
    nb, _ = tb.call("get_episode_events", {"batch_id": "sb_pc", "type": "stop"}, 2)
    assert not nb["ok"] and "no recorded episodes" in nb["error"]
    unk, _ = tb.call("get_episode_events", {"batch_id": "sb_x", "type": "stop"}, 3)
    assert not unk["ok"]


def test_batch_summary_detail(scene, data_dir):
    tb = toolbox(scene)
    d, _ = tb.call("get_batch_summary", {"batch_id": "sb_nn"}, 1)
    row = d["batches"][0]
    assert row["success_rate"]["value"] == 0.5 and "near_miss_per_mission" in row
    assert row["fail_reasons"]["blocked_timeout"]["value"] == 50
    assert len(set(tb.facts)) == len(tb.facts)


@pytest.mark.parametrize("text,values,ok", [
    ("success 71% [f_a]", [0.71], True),
    ("success 0.71", [0.7134], True),
    ("success 0.73", [0.7134], False),
    ("gap 0.62 m vs 0.70 m", [0.62, 0.7], True),
    ("moved T4 by 18 cm", [0.18], False),
    ("robots bot_a2 and sb_0012 at T7", [], True),
    ("-0.15 change", [-0.15], True),
])
def test_unsupported_numbers(text, values, ok):
    bad = unsupported_numbers(text, values, {"T4", "T7"})
    assert (bad == []) == ok, bad
