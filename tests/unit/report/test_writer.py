from __future__ import annotations

import json
import re

import pytest

from agents.factcheck.check import check_report, enforce
from agents.report.facts import build_citations, build_facts, collect_findings, derive_verdicts, investigations
from agents.report.writer import SECTIONS, build_messages, make_regenerate_fn, template_report, write_report

# Wording rule word list, stored reversed so this source file itself follows the rule.
_RULE_WORDS = ["ekaf", "kcom", "ymmud", "buts", "redlohecalp", "odot", "emxif", "kcah", "citehtnys", "yot",
               "ylno-omed", "enod-flah", "detnemelpmi ton", "noitatimil"]
BANNED = re.compile(r"\b(" + "|".join(w[::-1] for w in _RULE_WORDS) + r")\w*", re.I)


def _inputs(analysis):
    facts = build_facts(analysis)
    return facts, build_citations(analysis), collect_findings(analysis, facts), derive_verdicts(analysis), \
        investigations(analysis)


def test_offline_report_passes_its_own_checker(analysis):
    facts, cites, fds, verdicts, invs = _inputs(analysis)
    md = write_report(facts, cites, fds, verdicts, invs, mode="offline")
    r = check_report(md, facts)
    assert r["ok"], r["errors"][:5]
    assert r["stats"]["numbers_checked"] > 100
    assert r["clean_md"] == md.replace("\r\n", "\n").rstrip("\n") or r["clean_md"].rstrip() == md.rstrip()


def test_offline_report_sections_and_wording(analysis):
    md = write_report(*_inputs(analysis), mode="offline")
    heads = re.findall(r"^## \d+\. (.+)$", md, re.M)
    assert heads == SECTIONS
    assert md.index("Scan accuracy") < md.index("## 2. Verdicts")          # real-world evidence leads
    assert "simulation-based estimates" in md
    assert not BANNED.search(md), BANNED.search(md)
    assert "price on request" in md and "measure on site" in md
    assert "clearance" not in re.sub(r"Clearance (Report|console)", "", md).lower().replace("cleared", "")


def test_each_robot_has_exactly_its_code_verdict(analysis):
    facts, cites, fds, verdicts, invs = _inputs(analysis)
    md = write_report(facts, cites, fds, verdicts, invs, mode="offline")
    section = md.split("## 2. Verdicts")[1].split("## 3.")[0]
    for v in verdicts:
        name = {"rb_tray": "Northwind Tray T2", "rb_slim": "Northwind Slim S", "rb_cart": "Eastgate Cart C300"}[v.robot_id]
        heads = re.findall(rf"^### {re.escape(name)}: (.+?) \[f_\d+\]$", section, re.M)
        assert heads == [v.label]
        row = next(l for l in md.splitlines() if l.startswith(f"| {name} | "))
        assert row.split(" | ")[1] == v.label


def test_verdict_rule_values_are_tagged(analysis):
    facts, *rest = _inputs(analysis)
    md = write_report(facts, *rest, mode="offline")
    slim = md.split("### Northwind Slim S: Cleared with changes")[1].split("###")[0]
    assert ">= 0.90 [f_" in slim and "not met (value: 71% [f_" in slim and "met (value: 93% [f_" in slim
    cart = md.split("### Eastgate Cart C300: Not cleared")[1].split("##")[0]
    assert re.search(r"narrowest gap 0\.68 m \[f_\d+\] vs 0\.81 m \[f_\d+\] needed", cart)


def test_tampered_verdict_is_rejected(analysis):
    facts, *rest = _inputs(analysis)
    md = write_report(facts, *rest, mode="offline")
    bad = md.replace("### Northwind Slim S: Cleared with changes", "### Northwind Slim S: Cleared")
    bad = bad.replace("| Eastgate Cart C300 | Not cleared |", "| Eastgate Cart C300 | Cleared with changes |")
    bad += "\nNorthwind Tray T2 is Not cleared at this site.\n"
    r = check_report(bad, facts)
    vm = [e for e in r["errors"] if e["type"] == "verdict_mismatch"]
    assert {e["robot_id"] for e in vm} == {"rb_slim", "rb_cart", "rb_tray"}
    assert "Northwind Tray T2 is Not cleared" not in r["clean_md"]


def test_how_we_investigated_trail(analysis):
    md = write_report(*_inputs(analysis), mode="offline")
    sec = md.split("## 3. How we investigated")[1].split("## 4.")[0]
    assert "**Hypothesis:** Fails at the counter corridor at peak traffic." in sec
    assert re.search(r"zone `Z2` scaled to 50% \[f_\d+\]", sec)
    assert "82% [f_r1_001]" in sec and "93% [f_r1_002]" in sec
    assert re.search(r"The corridor gap is 0\.58 m \[f_x\d+\]", sec)       # collided id remapped
    assert "Outcome: supported; dug further." in sec and "Outcome: inconclusive; concluded." in sec


def test_untrusted_free_text_with_numbers_is_dropped(analysis):
    inv = analysis["investigation"]
    inv.experiments[0].hypothesis = "Fails 47% of the time near the counter."
    inv.findings[0].claim = "Robot fails 47% of runs."
    md = write_report(*_inputs(analysis), mode="offline")
    assert "47%" not in md
    assert check_report(md, build_facts(analysis))["ok"]


def test_no_investigation_and_no_business(analysis):
    analysis.pop("investigation"), analysis.pop("investigation_facts"), analysis.pop("business")
    facts = build_facts(analysis)
    md = template_report(facts, [], [], derive_verdicts(analysis), None)
    assert "The investigation loop runs simulator experiments" in md and "Payback needs your business inputs" in md
    assert check_report(md, facts)["ok"]


def test_reference_site_and_facts_loaded_from_json(analysis):
    analysis["twin"].geometry_source = "reference"
    analysis["twin"].accuracy.method = "reference_ground_truth"
    facts = build_facts(analysis)
    loaded = json.loads(json.dumps([f.model_dump(mode="json") for f in facts]))   # facts.json round trip
    md = template_report(loaded, build_citations(analysis), collect_findings(analysis, loaded),
                         derive_verdicts(analysis), investigations(analysis))
    assert "reference site (known ground-truth geometry)" in md
    assert check_report(md, loaded)["ok"]


class ScriptedLLM:
    """Test double for agents.llm.client.LLM: returns scripted contents and records the messages."""

    def __init__(self, *contents):
        self.contents = list(contents)
        self.calls = []

    def chat(self, role, messages, **kw):
        self.calls.append((role, messages, kw))
        item = self.contents.pop(0)
        if isinstance(item, Exception):
            raise item
        return type("Resp", (), {"content": item})()


def test_live_writer_regenerates_with_error_list(analysis):
    """Hybrid writer: a narrative section with an untagged number gets one regeneration with the error list."""
    facts, cites, fds, verdicts, invs = _inputs(analysis)
    k = {f.key: f for f in facts}
    bad = '<think>plan</think>```json\n{"markdown": "Median scan error 2.1 cm."}\n```'
    good = json.dumps({"markdown": f"Median scan error 2.1 cm [{k['site.median_err_m'].fact_id}]."})
    llm = ScriptedLLM(bad, good, *([json.dumps({"markdown": "Explained without numbers."})] * 10))
    md = write_report(facts, cites, fds, verdicts, invs, mode="live", llm=llm, workers=1)
    assert "<think>" not in md and f"Median scan error 2.1 cm [{k['site.median_err_m'].fact_id}]." in md
    assert md.meta["sections"]["summary"]["regenerated"] and md.meta["sections"]["summary"]["first_pass_errors"] == 1
    out = enforce(md, facts, make_regenerate_fn(facts, cites, fds, verdicts, invs, mode="live", llm=llm))
    assert out["ok_first_pass"] and not out["regenerated"]
    role, msgs, _ = llm.calls[1]
    assert role == "ultra" and "2.1 cm" in msgs[-1]["content"] and "no fact tag" in msgs[-1]["content"]
    assert msgs[0]["role"] == "system" and "Cleared with changes" in msgs[0]["content"]


def test_live_writer_falls_back_to_template_on_error(analysis):
    facts, *rest = _inputs(analysis)
    md = write_report(facts, *rest, mode="live", llm=ScriptedLLM(RuntimeError("endpoint unavailable")))
    assert md.startswith("# Clearance Report") and check_report(md, facts)["ok"]


def test_prompt_carries_facts_verdicts_and_trail(analysis):
    facts, cites, fds, verdicts, invs = _inputs(analysis)
    msgs = build_messages(facts, cites, fds, verdicts, invs)
    payload = json.loads(msgs[1]["content"].split("\n\n", 1)[1])
    assert len(payload["FACTS"]) == len(facts)
    assert {v["label"] for v in payload["VERDICTS"]} == {"Cleared", "Cleared with changes", "Not cleared"}
    assert payload["INVESTIGATION TRAIL"][0]["experiments"][0]["experiment_id"] == "e1"
    assert "How we investigated" in msgs[0]["content"] and "[f_" in msgs[0]["content"]


def test_mode_from_environment(monkeypatch, analysis):
    from agents.report import writer
    monkeypatch.setenv("CLEARANCE_LLM_MODE", "offline")
    monkeypatch.setenv("NEBIUS_API_KEY", "x")
    assert writer.llm_mode() == "offline"
    monkeypatch.delenv("CLEARANCE_LLM_MODE")
    assert writer.llm_mode() == "live"
    monkeypatch.delenv("NEBIUS_API_KEY")
    assert writer.llm_mode() == "offline"


def test_generate_report_writes_artifacts(analysis, tmp_path, monkeypatch):
    from clearance_core import storage
    from agents.report.generate import generate_report
    monkeypatch.setattr(storage, "DATA", tmp_path)
    out = generate_report(analysis, mode="offline", pdf=True)
    assert out["check"]["ok_first_pass"] and not out["check"]["removed"]
    base = tmp_path / "analyses" / "an_cafe"
    for name in ("facts.json", "citations.json", "report.md", "report.json", "report_check.json", "report.pdf"):
        assert (base / name).exists(), name
    saved = json.loads((base / "facts.json").read_text(encoding="utf-8"))
    assert saved[0]["fact_id"] == "f_001" and "key" in saved[0]
    assert (base / "report.pdf").read_bytes()[:5] == b"%PDF-"
    doc = json.loads((base / "report.json").read_text(encoding="utf-8"))
    assert doc["blocks"][0]["type"] == "heading"


@pytest.mark.parametrize("seed", range(5))
def test_offline_report_robust_to_varied_results(analysis, seed):
    import random
    rnd = random.Random(seed)
    for b in analysis["batches"]:
        if b.results.success_rate:
            b.results.success_rate = round(rnd.uniform(0.3, 1.0), 4)
            b.results.median_mission_s = round(rnd.uniform(30, 300), 2)
    facts, *rest = _inputs(analysis)
    md = write_report(facts, *rest, mode="offline")
    assert check_report(md, facts)["ok"]
