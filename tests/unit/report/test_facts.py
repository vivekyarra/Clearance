from __future__ import annotations

import json
import random

from clearance_core.schemas import Fact, Verdict

from agents.report.facts import (build_citations, build_facts, collect_findings, derive_verdicts, facts_by_key,
                                 remap_tags, tag_text_numbers)


def _dump(facts):
    return [f.model_dump(mode="json") for f in facts]


def test_ids_sequential_unique_and_valid_facts(analysis):
    facts = build_facts(analysis)
    ids = [f.fact_id for f in facts]
    assert len(ids) == len(set(ids))
    core = [i for i in ids if i.startswith("f_") and i[2:].isdigit()]
    assert core == [f"f_{n:03d}" for n in range(1, len(core) + 1)]
    for f in facts:
        assert isinstance(f, Fact)
        Fact.model_validate(f.model_dump())
        assert f.value is not None


def test_ids_stable_across_runs_and_batch_order(analysis):
    a = _dump(build_facts(analysis))
    b = _dump(build_facts(analysis))
    assert a == b
    shuffled = dict(analysis)
    shuffled["batches"] = list(analysis["batches"])
    random.Random(7).shuffle(shuffled["batches"])
    assert _dump(build_facts(shuffled)) == a


def test_ranges_per_robot_and_density(analysis):
    k = facts_by_key(build_facts(analysis))
    for rid in ("rb_tray", "rb_slim", "rb_cart"):
        for d in ("quiet", "normal", "peak"):
            lo, hi = k[f"sim.success|{rid}|{d}|conservative"], k[f"sim.success|{rid}|{d}|optimistic"]
            assert lo.origin.type == hi.origin.type == "sim_batch"
            assert lo.origin.ref == f"sb_{rid}_{d}_conservative" and hi.origin.ref == f"sb_{rid}_{d}_optimistic"
            assert lo.unit == "ratio" and lo.value <= hi.value
    assert k["sim.success|rb_slim|normal|conservative"].value == 0.71


def test_origin_types_cover_every_kind(analysis):
    types = {f.origin.type for f in build_facts(analysis)}
    assert types == {"sim_batch", "measurement", "source_url", "user_input", "formula", "investigation"}


def test_site_and_gap_facts(analysis):
    k = facts_by_key(build_facts(analysis))
    assert k["site.median_err_m"].value == 0.021 and k["site.median_err_m"].origin.type == "measurement"
    assert k["site.measurement_err|aisle between T1 and T2"].value == 0.02 or \
        abs(k["site.measurement_err|aisle between T1 and T2"].value - 0.02) < 1e-9
    assert k["site.threshold_status|D2"].value == "measure on site"
    assert k["feas.narrowest_gap_m|rb_cart"].value == min(0.74 - 0.03 * i for i in range(4))
    assert "Narrowest gap on route" in k["feas.narrowest_gap_m|rb_cart"].label
    assert k["feas.frac|rb_cart"].value == 0.75
    assert k["robot.price_status|rb_slim"].value == "price on request"
    assert k["robot.verified|rb_cart"].value == "awaiting review"
    assert k["site.geometry_source"].value == "scanned site"


def test_roi_facts(analysis):
    k = facts_by_key(build_facts(analysis))
    assert k["roi.price|rb_slim"].origin.type == "user_input"          # user quote
    assert k["roi.price|rb_tray"].origin.type == "source_url"          # public price
    assert k["roi.status|rb_cart"].value == "enter a quote"
    assert "roi.payback|rb_cart" not in k
    assert k["roi.payback|rb_tray"].unit == "months" and k["roi.payback|rb_tray"].origin.type == "formula"
    # automatable share = nominal normal success x feasible leg fraction
    assert abs(k["roi.share|rb_tray"].value - 0.95 * 1.0) < 1e-9
    assert k["biz.days_per_month"].value == 26.0
    assert k["biz.maintenance_pct_per_year"].label.endswith("(default, editable)")


def test_given_roi_is_used(analysis):
    analysis["roi"] = {"rb_tray": {"monthly_saving": 1000.0, "payback_months": 9.5, "automatable_share": 0.5,
                                   "status": "ok", "inputs": {}}}
    k = facts_by_key(build_facts(analysis))
    assert k["roi.payback|rb_tray"].value == 9.5 and k["roi.saving|rb_tray"].value == 1000.0


def test_verdict_facts_match_code_verdicts(analysis):
    k = facts_by_key(build_facts(analysis))
    labels = {v.robot_id: v.label for v in derive_verdicts(analysis)}
    assert labels == {"rb_tray": "Cleared", "rb_slim": "Cleared with changes", "rb_cart": "Not cleared"}
    for rid, label in labels.items():
        assert k[f"verdict|{rid}"].value == label and k[f"verdict|{rid}"].origin.ref == f"verdict:{rid}"
    # every check value has a fact: success value, threshold and the numbers in "x/y legs"
    assert k["verdict.value|rb_slim|as_is|2"].value == 0.71
    assert k["verdict.threshold|rb_slim|as_is|2"].value == 0.9
    assert k["verdict.value|rb_slim|with_changes|2"].value == 0.93
    assert [k[f"verdict.value_num|rb_cart|as_is|0|{i}"].value for i in (0, 1)] == [3, 4]
    # numbers in the main reason ("narrowest gap 0.68 m vs 0.81 m needed") are facts too
    assert [k[f"verdict.reason_num|rb_cart|{i}"].value for i in (0, 1)] == [0.68, 0.81]


def test_analysis_verdicts_take_precedence(analysis):
    analysis["verdicts"] = [Verdict(robot_id="rb_tray", label="Not cleared", main_reason="success 64% at normal traffic",
                                    as_is=[])]
    k = facts_by_key(build_facts(analysis))
    assert k["verdict|rb_tray"].value == "Not cleared"
    assert k["verdict.reason_num|rb_tray|0"].value == 0.64
    assert "verdict|rb_slim" not in k


def test_tag_text_numbers_roundtrip(analysis):
    facts = build_facts(analysis)
    k = facts_by_key(facts)
    v = next(v for v in derive_verdicts(analysis) if v.robot_id == "rb_cart")
    text = tag_text_numbers(v.main_reason, k, "verdict.reason_num|rb_cart")
    assert text.count("[f_") == 2
    from agents.factcheck.check import check_report
    assert check_report(text, facts)["ok"]


def test_investigation_facts_kept_or_renumbered_on_collision(analysis):
    facts = build_facts(analysis)
    ids = {f.fact_id for f in facts}
    k = facts_by_key(facts)
    assert "f_r1_001" in ids and "f_r1_002" in ids               # no collision: id kept
    moved = k["inv|inv_cafe_slim|f_001"]                          # collided with core f_001
    assert moved.fact_id.startswith("f_x") and moved.value == 0.58 and moved.origin.type == "investigation"
    assert remap_tags("gap 0.58 m [f_001]", facts, "inv_cafe_slim") == f"gap 0.58 m [{moved.fact_id}]"
    assert remap_tags("x [f_zzz]", facts, "inv_cafe_slim") == "x [f_zzz?]"


def test_experiment_change_parameters_become_facts(analysis):
    k = facts_by_key(build_facts(analysis))
    assert k["exp.param|inv_cafe_slim|e1|ped_scale"].value == 0.5
    assert k["exp.param|inv_cafe_slim|e1|ped_scale"].unit == "ratio"
    assert k["exp.param|inv_cafe_slim|e2|move.C3.dy"].value == -0.05


def test_findings_remapped(analysis):
    facts = build_facts(analysis)
    fds = collect_findings(analysis, facts)
    assert fds[0]["claim"].count("[f_r1_002]") == 1 and fds[0]["evidence_fact_ids"] == ["f_r1_002"]


def test_investigation_facts_inside_investigation_record(analysis):
    inv = analysis["investigation"].model_dump(mode="json")
    inv["facts"] = [f.model_dump(mode="json") for f in analysis.pop("investigation_facts")]
    analysis["investigation"] = inv
    ids = {f.fact_id for f in build_facts(analysis)}
    assert {"f_r1_001", "f_r1_002"} <= ids


def test_batch_ids_loaded_from_storage(analysis, tmp_path, monkeypatch):
    from clearance_core import storage
    monkeypatch.setattr(storage, "DATA", tmp_path)
    ids = []
    for b in analysis["batches"]:
        storage.write_json(f"analyses/an_cafe/batches/{b.batch_id}.json", b)
        ids.append(b.batch_id)
    expected = _dump(build_facts(analysis))
    analysis["batches"] = ids + ["sb_missing"]
    assert _dump(build_facts(analysis)) == expected


def test_minimal_and_empty_inputs():
    assert build_facts({}) == []
    facts = build_facts({"candidates": [{"robot_id": "r1"}]})
    k = facts_by_key(facts)
    assert k["robot.name|r1"].value == "r1" and k["robot.price_status|r1"].value == "price on request"
    assert k["verdict|r1"].value == "Not cleared"   # no evidence -> code verdict says not cleared


def test_dict_and_model_inputs_equivalent(analysis):
    as_dicts = json.loads(json.dumps(analysis, default=lambda o: o.model_dump(mode="json", by_alias=True)))
    assert _dump(build_facts(as_dicts)) == _dump(build_facts(analysis))


def test_citations(analysis):
    cites = build_citations(analysis)
    urls = [c["url"] for c in cites]
    assert len(urls) == len(set(urls))
    assert "https://example.com/rb_tray/specs" in urls and "https://example.com/market/robots-2026" in urls
    spec = next(c for c in cites if c["url"] == "https://example.com/rb_tray/specs")
    assert spec["retrieved_at"].startswith("2026-10-20") and spec["quotes"]
