"""Hybrid writer: code renders the required blocks, a scripted model writes the narrative sections, each section is
fact-checked on its own."""
from __future__ import annotations

import json
import re
import threading

import pytest

from agents.factcheck.check import check_report
from agents.report.facts import build_citations, build_facts, collect_findings, derive_verdicts, investigations
from agents.report.writer import (SECTIONS, _Ctx, ft, narrative_specs, narrative_text, offline_narratives,
                                  required_blocks, section_messages, write_report)

NAMES = {"rb_tray": "Northwind Tray T2", "rb_slim": "Northwind Slim S", "rb_cart": "Eastgate Cart C300"}


def _inputs(analysis):
    facts = build_facts(analysis)
    return facts, build_citations(analysis), collect_findings(analysis, facts), derive_verdicts(analysis), \
        investigations(analysis)


class SectionLLM:
    """Test double for agents.llm.client.LLM: answers per report section (meta["section"]). Each entry is a list
    of answers, used in order (the last one repeats); an Exception entry is raised."""

    def __init__(self, answers: dict, default="Explained without numbers."):
        self.answers = {k: list(v) for k, v in answers.items()}
        self.default = default
        self.calls = []
        self.lock = threading.Lock()

    def chat(self, role, messages, **kw):
        key = kw["meta"]["section"]
        with self.lock:
            self.calls.append((role, key, messages, kw))
            seq = self.answers.get(key)
            item = (seq.pop(0) if len(seq) > 1 else seq[0]) if seq else self.default
        if isinstance(item, Exception):
            raise item
        if not item.lstrip().startswith("{") and not item.startswith("RAW:"):
            item = json.dumps({"markdown": item})
        return type("Resp", (), {"content": item.removeprefix("RAW:"), "tokens_out": 10})()


def _sec(md: str, title: str) -> str:
    return md.split(title, 1)[1].split("\n## ", 1)[0]


def _keys(facts):
    return {f.key: f for f in facts}


def test_required_blocks_present_offline_and_live(analysis):
    facts, *rest = _inputs(analysis)
    c = _Ctx(facts, *rest)
    for md in (write_report(facts, *rest, mode="offline"),
               write_report(facts, *rest, mode="live", llm=SectionLLM({}, default=RuntimeError("unavailable")))):
        assert all(required_blocks(md, c).values()), required_blocks(md, c)
        assert all(md.meta["required_blocks"].values())
        assert re.findall(r"^## \d+\. (.+)$", md, re.M) == SECTIONS
        assert check_report(md, facts)["ok"]
        assert md.count("| Rule check | Result |") >= 3 and "awaiting review" in md
        comp = _sec(md, "Robot comparison (simulation-based estimates)")
        assert len([ln for ln in comp.splitlines() if ln.startswith("| ") and not ln.startswith("| Robot |")]) == 3


def test_failing_model_falls_back_per_section_and_is_recorded(analysis):
    facts, *rest = _inputs(analysis)
    md = write_report(facts, *rest, mode="live", llm=SectionLLM({}, default=RuntimeError("unavailable")))
    off = offline_narratives(_Ctx(facts, *rest))
    assert off["summary"] in md and off["tradeoffs"] in md
    t = md.meta["totals"]
    assert set(t["fallbacks"]) == {"summary", "why|rb_tray", "why|rb_slim", "why|rb_cart", "investigation", "tradeoffs"}
    assert all(m["fallback"].startswith("model call failed") for m in md.meta["sections"].values())


def test_wrong_numbers_are_corrected_or_removed_only_in_their_section(analysis):
    facts, *rest = _inputs(analysis)
    k = _keys(facts)
    cons = k["sim.success|rb_slim|normal|conservative"]           # 71%
    wrong = f"Slim S succeeds in 88% [{cons.fact_id}] of runs at normal traffic. It waits at the counter."
    good_tray = f"The tray robot keeps {ft(k['sim.success|rb_tray|normal|conservative'])} success."
    trade = "The tray robot is the steadier choice; the slim one needs the layout change."
    llm = SectionLLM({"why|rb_slim": [wrong, wrong], "why|rb_tray": [good_tray], "tradeoffs": [trade]})
    md = write_report(facts, *rest, mode="live", llm=llm)
    assert check_report(md, facts)["ok"]
    slim = md.split("### Northwind Slim S: ")[1].split("\n### ")[0]
    assert "88%" not in md and "It waits at the counter." in slim          # only the failing sentence dropped
    assert good_tray in md and trade in md                                # other sections untouched
    s = md.meta["sections"]
    assert s["why|rb_slim"]["first_pass_errors"] == 1 and s["why|rb_slim"]["regenerated"]
    assert s["why|rb_slim"]["errors_after_regeneration"] == 1 and s["why|rb_slim"]["sentences_removed"] == 1
    assert s["why|rb_slim"]["llm_calls"] == 2 and s["why|rb_slim"]["fallback"] is None
    for key in ("why|rb_tray", "tradeoffs", "summary"):
        assert s[key]["first_pass_errors"] == 0 and not s[key]["regenerated"] and s[key]["sentences_removed"] == 0
    assert md.meta["totals"]["regenerations"] == 1 and md.meta["totals"]["sentences_removed"] == 1
    # the regeneration request carried the error list of that section only
    regen = [c for c in llm.calls if c[1] == "why|rb_slim"][1]
    assert "88%" in regen[2][-1]["content"] and "write_as" in regen[2][-1]["content"]


def test_regeneration_fixes_the_section(analysis):
    facts, *rest = _inputs(analysis)
    k = _keys(facts)
    cons = k["sim.success|rb_slim|normal|conservative"]
    llm = SectionLLM({"why|rb_slim": [f"Success is 88% [{cons.fact_id}].", f"Success is {ft(cons)} on the conservative bound."]})
    md = write_report(facts, *rest, mode="live", llm=llm)
    assert f"Success is {ft(cons)} on the conservative bound." in md
    m = md.meta["sections"]["why|rb_slim"]
    assert m["regenerated"] and m["errors_after_regeneration"] == 0 and m["sentences_removed"] == 0


def test_verdict_labels_never_change(analysis):
    facts, cites, fds, verdicts, invs = _inputs(analysis)
    lies = ("Northwind Tray T2 is Not cleared. Eastgate Cart C300 is Cleared. "
            "Northwind Slim S is Cleared with changes.")
    llm = SectionLLM({"summary": [lies, lies], "tradeoffs": ["Eastgate Cart C300 is Cleared with changes here."] * 2})
    md = write_report(facts, cites, fds, verdicts, invs, mode="live", llm=llm)
    assert "Northwind Tray T2 is Not cleared" not in md and "Eastgate Cart C300 is Cleared" not in md
    assert "Northwind Slim S is Cleared with changes." in md
    for v in verdicts:
        assert re.findall(rf"^### {re.escape(NAMES[v.robot_id])}: (.+?) \[f_\d+\]$", md, re.M) == [v.label]
        row = next(ln for ln in md.splitlines() if ln.startswith(f"| {NAMES[v.robot_id]} | "))
        assert row.split(" | ")[1] == v.label
    assert md.meta["sections"]["tradeoffs"]["fallback"] == "every sentence failed the fact check"
    assert offline_narratives(_Ctx(facts, cites, fds, verdicts, invs))["tradeoffs"] in md


def test_allowed_facts_are_section_scoped_with_rendered_values(analysis):
    facts, *rest = _inputs(analysis)
    c = _Ctx(facts, *rest)
    specs = {s.key: s for s in narrative_specs(c)}
    assert set(specs) == {"summary", "why|rb_tray", "why|rb_slim", "why|rb_cart", "investigation", "tradeoffs"}
    k = _keys(facts)
    payload = json.loads(section_messages(specs["why|rb_tray"], c)[1]["content"])
    ids = {f["id"] for f in payload["ALLOWED_FACTS"]}
    assert k["sim.success|rb_tray|normal|conservative"].fact_id in ids
    assert k["sim.success|rb_slim|normal|conservative"].fact_id not in ids
    entry = next(f for f in payload["ALLOWED_FACTS"] if f["id"] == k["feas.narrowest_gap_m|rb_tray"].fact_id)
    assert entry["write_as"] == ft(k["feas.narrowest_gap_m|rb_tray"])
    assert any(str(v.get("verdict")) == "Cleared" for v in payload["VERDICTS"])
    inv = json.loads(section_messages(specs["investigation"], c)[1]["content"])
    assert "f_r1_002" in {f["id"] for f in inv["ALLOWED_FACTS"]} and inv["CONTEXT"]["trail"]
    assert len(payload["ALLOWED_FACTS"]) < len(facts) / 2


def test_model_calls_are_attributed_and_metrics_saved(analysis, tmp_path, monkeypatch):
    from clearance_core import storage
    monkeypatch.setattr(storage, "DATA", tmp_path)
    monkeypatch.setenv("CLEARANCE_REPORT_META", "1")
    facts, *rest = _inputs(analysis)
    llm = SectionLLM({})
    md = write_report(facts, *rest, mode="live", llm=llm)
    assert {c[0] for c in llm.calls} == {"ultra"}
    assert all(c[3]["meta"]["analysis_id"] == "an_cafe" for c in llm.calls)
    assert len(llm.calls) == md.meta["totals"]["llm_calls"] == 6
    saved = json.loads((tmp_path / "analyses" / "an_cafe" / "report_meta.json").read_text(encoding="utf-8"))
    assert saved["totals"]["first_pass_errors"] == 0 and saved["final_check"]["ok"] and saved["writer"] == "hybrid"


def test_empty_answer_gets_one_retry_then_template(analysis):
    facts, *rest = _inputs(analysis)
    llm = SectionLLM({"summary": ['RAW:{"markdown": ""}'], "tradeoffs": ['RAW:{"markdown": ""}', "Steady versus fast."]})
    md = write_report(facts, *rest, mode="live", llm=llm)
    assert md.meta["sections"]["summary"]["fallback"] == "empty answer"
    assert md.meta["sections"]["tradeoffs"]["fallback"] is None and "Steady versus fast." in md


def test_narrative_text_parsing():
    assert narrative_text('<think>x</think>```json\n{"markdown": "A.\\n\\n## Heading\\n| a | b |\\nB."}\n```') == "A.\n\nB."
    assert narrative_text("Plain text answer.") == "Plain text answer."
    assert narrative_text('{"markdown": "- one item\\n- two"}') == "- one item\n- two"
    assert narrative_text('{"broken": ') == ""


@pytest.mark.parametrize("seed", range(4))
def test_scripted_noise_never_breaks_the_report(analysis, seed):
    import random
    rnd = random.Random(seed)
    facts, *rest = _inputs(analysis)
    ids = [f.fact_id for f in facts]
    noise = lambda: " ".join(rnd.choice([f"{rnd.randint(1, 99)}% [{rnd.choice(ids)}]", "Cleared.", "is Not cleared",
                                         "| a | b |", "## H", "ninety percent", "Fine sentence.", "`T4` moved."])
                             for _ in range(12))
    llm = SectionLLM({k: [noise(), noise()] for k in ("summary", "why|rb_tray", "why|rb_slim", "why|rb_cart",
                                                      "investigation", "tradeoffs")})
    md = write_report(facts, *rest, mode="live", llm=llm)
    assert check_report(md, facts)["ok"] and all(md.meta["required_blocks"].values())


def test_clear_zone_line_in_methodology(analysis):
    from clearance_core.schemas import ClearZone
    from agents.report.writer import template_report
    plain = template_report(*_inputs(analysis))
    assert "unseen floor" not in plain
    analysis["twin"].clear_zones = [
        ClearZone(id="cz1", polygon=[(0, 0), (1, 0), (1, 1), (0, 1)], confirmed_by="Asha"),
        ClearZone(id="cz2", polygon=[(2, 0), (2.5, 0), (2.5, 1), (2, 1)], confirmed_by="Asha")]
    facts, *rest = _inputs(analysis)
    md = template_report(facts, *rest)
    k = _keys(facts)
    line = next(ln for ln in md.splitlines() if "unseen floor" in ln)
    assert line.startswith(f"- Operator confirmed 2 [{k['site.clear_zones_n'].fact_id}] unseen floor patches clear "
                           f"(1.5 m² [{k['site.clear_zones_area_m2'].fact_id}], by Asha [{k['site.clear_zones_by'].fact_id}])")
    assert md.index(line) > md.index("## 11. Methodology") and check_report(md, facts)["ok"]
    # core fact ids do not move when the optional facts are present
    assert k["site.median_err_m"].fact_id == _keys(build_facts(make_plain(analysis)))["site.median_err_m"].fact_id


def make_plain(analysis):
    import copy
    a = copy.deepcopy(analysis)
    a["twin"].clear_zones = []
    return a


def test_clear_zone_line_from_orchestrator_note(analysis):
    from agents.report.writer import template_report
    analysis["notes"] = ["operator-confirmed clear floor: 1 zone, 0.80 m² total, confirmed by the operator; unseen "
                         "cells inside count as free floor, measured obstacles are unchanged"]
    facts, *rest = _inputs(analysis)
    md = template_report(facts, *rest)
    line = next(ln for ln in md.splitlines() if "unseen floor" in ln)
    assert "1 [f_" in line and "patch clear (0.8 m² [f_" in line and "by " not in line
    assert check_report(md, facts)["ok"]


def test_analysis_id_found_from_saved_facts_without_investigation(analysis, tmp_path, monkeypatch):
    from clearance_core import storage
    monkeypatch.setattr(storage, "DATA", tmp_path)
    analysis.pop("investigation"), analysis.pop("investigation_facts")
    facts = build_facts(analysis)
    storage.write_json("analyses/an_other/facts.json", [f.model_dump(mode="json") for f in facts[:-1]])
    storage.write_json("analyses/an_x/facts.json", [f.model_dump(mode="json") for f in facts])
    llm = SectionLLM({})
    md = write_report(facts, build_citations(analysis), [], derive_verdicts(analysis), None, mode="live", llm=llm)
    assert md.meta["analysis_id"] == "an_x" and all(c[3]["meta"]["analysis_id"] == "an_x" for c in llm.calls)


def test_free_text_follows_the_wording_rule(analysis):
    from agents.report.writer import apply_wording
    verb = "gnivorpmi"[::-1]
    analysis["investigation"].experiments[0].hypothesis = f"Moving the chair widens the aisle, {verb} success."
    facts, *rest = _inputs(analysis)
    llm = SectionLLM({"tradeoffs": [f"The tray robot is steadier, {verb} uptime. A {'kcom'[::-1]}-up layout helps."]})
    md = write_report(facts, *rest, mode="live", llm=llm)
    assert verb not in md.lower() and "raising success" in md and "raising uptime" in md
    assert "kcom"[::-1] not in md.lower()
    assert apply_wording("Plain text.") == "Plain text."


def test_runaway_draft_gets_a_length_note(analysis):
    facts, *rest = _inputs(analysis)
    k = _keys(facts)
    cons = k["sim.success|rb_slim|normal|conservative"]
    runaway = " ".join([f"Success is 88% [{cons.fact_id}]."] * 200)
    llm = SectionLLM({"tradeoffs": [runaway, "Steady versus fast."]})
    md = write_report(facts, *rest, mode="live", llm=llm)
    m = md.meta["sections"]["tradeoffs"]
    assert m["first_pass_words"] == 800 and m["first_pass_errors"] == 200 and m["regenerated"]
    retry = [c for c in llm.calls if c[1] == "tradeoffs"][1][2]
    assert "the section allows" in retry[-1]["content"] and len(retry[-2]["content"]) < len(runaway)
    assert "Steady versus fast." in md and "88%" not in md
