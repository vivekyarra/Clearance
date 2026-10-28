"""Fact-checker tests: adversarial numbers, units, tables, ranges, verdict labels, enforcement."""
from __future__ import annotations

import pytest

from clearance_core.schemas import Fact, FactOrigin

from agents.factcheck.check import check_report, enforce, mask_exempt, tokenize_numbers
from agents.factcheck.mdparse import parse_blocks, split_sentences


def F(fid, value, unit="", label="x", otype="sim_batch", ref="sb_1", key=None):
    d = {"fact_id": fid, "value": value, "unit": unit, "label": label, "origin": {"type": otype, "ref": ref}}
    if key:
        d["key"] = key
    return d


FACTS = [
    F("f_001", 0.93, "ratio", "Success rate, Robot A, normal"),
    F("f_002", 0.71, "ratio", "Success rate conservative"),
    F("f_003", 0.18, "m", "Move distance"),
    F("f_004", 12000.0, "USD", "Price", "user_input", "business.quotes.a"),
    F("f_005", -300.0, "USD/month", "Net monthly saving", "formula", "roi:a"),
    F("f_006", 74.2, "s", "Median mission time"),
    F("f_007", 14.25, "months", "Payback", "formula", "roi:a"),
    F("f_008", 10.0, "percent", "Maintenance per year", "user_input", "business.maintenance_pct_per_year"),
    F("f_009", 200, "episodes", "Episodes"),
    F("f_010", 0.021, "m", "Median scan error", "measurement", "twin:accuracy"),
    F("f_011", "price on request", "", "Price, Robot B", "source_url", "catalog:b"),
    F("f_012", 4, "count", "Legs feasible"),
    F("f_013", 5, "count", "Legs total"),
    F("f_014", 1.25, "m/s", "Max speed", "source_url", "https://example.com/spec"),
    F("f_015", 90.0, "s", "Wait time"),
]


def errs(md, facts=FACTS, **kw):
    return check_report(md, facts, **kw)["errors"]


def types(md, facts=FACTS, **kw):
    return [e["type"] for e in errs(md, facts, **kw)]


# ---------------------------------------------------------------- accepted
@pytest.mark.parametrize("md", [
    "Robot A succeeds in 93% [f_001] of missions.",
    "Robot A succeeds in 0.93 [f_001] of missions.",
    "Success is 93.0% [f_001].",
    "Success is about 92.6% [f_001].",            # within tol (1% relative)
    "Move T4 by 0.18 m [f_003].",
    "Move T4 by 18 cm [f_003].",
    "Move T4 by 180 mm [f_003].",
    "Move T4 by 18cm [f_003].",
    "The price is $12,000 [f_004].",
    "The price is USD 12,000 [f_004].",
    "The price is 12,000 USD [f_004].",
    "The price is $12k [f_004].",
    "The price is $12.0k [f_004].",
    "Net saving is -$300/month [f_005].",
    "Net saving is −$300 per month [f_005].",
    "Net saving falls short by $300 [f_005].",    # magnitude of a negative fact
    "Median mission 74 s [f_006].",
    "Median mission 1.2 min [f_006].",
    "Payback 14.3 months [f_007].",
    "Payback 14 months [f_007].",
    "Payback 1.2 years [f_007].",
    "Maintenance 10% [f_008] per year.",
    "Maintenance 0.10 [f_008] of price per year.",
    "Based on 200 [f_009] episodes.",
    "n=200 [f_009].",
    "Median error 2.1 cm [f_010].",
    "Median error 21 mm [f_010].",
    "Median error 0.021 m [f_010].",
    "Success 71–93% [f_002][f_001].",
    "Success 71-93% [f_001][f_002].",
    "Success 71% to 93% [f_001][f_002].",
    "Success 0.71–0.93 [f_001][f_002].",
    "Success 71 – 93 % [f_002] [f_001].",
    "Success 93% [f_001, f_002] and 71% [f_002].",
    "Legs feasible 4 [f_012] of 5 [f_013].",
    "Legs 4/5 [f_012][f_013].",
    "Max speed 1.25 m/s [f_014].",
    "Wait 1.5 min [f_015].",
    "Robot A succeeds in 93%. [f_001]",           # tag after the full stop
    "Price for Robot B: price on request [f_011].",
    "Robot A runs a 2D simulation on 1st and 3D views.",   # 2D/1st/3D are not quantities
    "Table `T4` and chair C3 sit by waypoint T1.",          # identifiers
    "Batch `sb_2026_1000` and id f_001 are references.",    # code and ids
    "Retrieved 2026-10-20 and Oct 27, 2026 at 10:30.",      # dates and times
    "See https://example.com/robots/42 for details.",       # URLs
    "See [the spec](https://example.com/spec/7) for details.",
    "Wilson 95% CI around the estimate.",                   # default exempt term
    "",
])
def test_accepted(md):
    assert errs(md) == [], errs(md)


# ---------------------------------------------------------------- rejected
@pytest.mark.parametrize("md,kind", [
    ("Robot A succeeds in 93% of missions.", "untagged_number"),
    ("Robot A succeeds in 95% [f_001] of missions.", "value_mismatch"),
    ("Robot A succeeds in 93 [f_001] of missions.", "value_mismatch"),        # ratio printed without %
    ("Maintenance 0.10% [f_008].", "value_mismatch"),                         # percent fact printed as tiny %
    ("Move T4 by 18 m [f_003].", "value_mismatch"),
    ("Move T4 by 18 [f_003].", "value_mismatch"),                             # cm without unit
    ("Move T4 by 0.18 s [f_003].", "unit_mismatch"),
    ("Move T4 by 18% [f_003].", "unit_mismatch"),
    ("The price is €12,000 [f_004].", "currency_mismatch"),
    ("The price is $13,000 [f_004].", "value_mismatch"),
    ("The price is $12.5k [f_004].", "value_mismatch"),
    ("Median mission 74 m [f_006].", "unit_mismatch"),
    ("Net saving is $300 [f_004].", "value_mismatch"),
    ("Success is -93% [f_001].", "value_mismatch"),                           # sign flip
    ("Payback 14 [f_011] months.", "non_numeric_fact"),                       # tagged with a text fact
    ("Success 71–95% [f_002][f_001].", "value_mismatch"),
    ("Success 93% [f_999].", "unknown_fact"),
    ("Success 93% [f_001?].", "unknown_fact"),
    ("Median error 21 cm [f_010].", "value_mismatch"),
    ("Success 93% [f_001] and 3 extra robots.", "value_mismatch"),
    ("About 1.2 million dollars [f_004].", "value_mismatch"),
    ("Payback 1.5 years [f_007].", "value_mismatch"),
])
def test_rejected(md, kind):
    assert kind in types(md), errs(md)


def test_tag_in_other_sentence_does_not_count():
    md = "Robot A succeeds in 93% of missions. Its rate is high [f_001]."
    assert types(md) == ["untagged_number"]


def test_each_number_needs_a_matching_fact():
    t = types("Success 93% and 71% [f_001].")
    assert t == ["value_mismatch"]


def test_negative_number_untagged():
    assert types("Net saving is -300 per month.") == ["untagged_number"]


def test_mixed_units_in_one_sentence():
    md = "Gap 18 cm [f_003], mission 1.2 min [f_006], payback 14 months [f_007]."
    assert errs(md) == []
    md_bad = "Gap 18 cm [f_003], mission 1.2 h [f_006], payback 14 months [f_007]."
    assert types(md_bad) == ["value_mismatch"]


# ---------------------------------------------------------------- tables
TABLE = """| Robot | Success | Price |
|---|---|---|
| Robot A | 71–93% [f_002][f_001] | $12,000 [f_004] |
| Robot B | 88% | price on request [f_011] |
| Robot C | 93% [f_001] | $12,000 [f_001] |
"""


def test_table_cells_checked_per_cell():
    r = check_report(TABLE, FACTS)
    bad = [(e["line"], e["token"], e["type"]) for e in r["errors"]]
    assert (4, "88%", "untagged_number") in bad
    assert (5, "12,000", "unit_mismatch") in bad  # money tagged with a ratio fact
    assert len(bad) == 2
    lines = r["clean_md"].splitlines()
    assert lines[2].startswith("| Robot A | 71–93%")
    assert lines[3] == "| Robot B | — | price on request [f_011] |"
    assert lines[4] == "| Robot C | 93% [f_001] | — |"


def test_tag_in_neighbour_cell_does_not_count():
    md = "| Robot | Success |\n|---|---|\n| Robot A [f_001] | 93% |\n"
    assert types(md) == ["untagged_number"]


def test_table_header_numbers_checked():
    md = "| Robot | Peak (14 persons/min) |\n|---|---|\n| A | x |\n"
    assert types(md) == ["untagged_number"]


# ---------------------------------------------------------------- exemptions
def test_section_numbers_and_ordinals_exempt():
    md = "## 2. Verdicts\n\n### 2.1 Detail\n\n1. First item.\n2) Second item.\n10. Tenth item.\n"
    assert errs(md) == []


def test_heading_number_beyond_section_prefix_checked():
    assert types("## 2. Robot A at 93%\n") == ["untagged_number"]


def test_sources_section_exempt_but_others_not():
    md = "## 12. Sources\n\n- [Datasheet v3 2024](https://x.com/a), retrieved 2026-10-20; 300 kg payload\n"
    assert errs(md) == []
    assert types("## 11. Scope\n\n- 300 kg payload\n") == ["untagged_number"]


def test_robot_model_names_whitelisted_from_facts():
    facts = FACTS + [F("f_050", "Acme Runner 300", "", "Robot model (acme_runner)", "source_url", "catalog:acme",
                       key="robot.name|acme_runner")]
    assert errs("Acme Runner 300 reaches 93% [f_001].", facts) == []
    assert types("Acme Runner 300 reaches 93%.", facts) == ["untagged_number"]
    assert types("A Runner 300 reaches 93% [f_001].") == ["value_mismatch"]


def test_explicit_whitelist():
    assert errs("The Model 9 robot reaches 93% [f_001].", whitelist=["Model 9"]) == []
    assert types("The Model 9 robot reaches 93% [f_001].") == ["value_mismatch"]


def test_code_fence_skipped():
    assert errs("```\nx = 42\n```\n") == []


def test_soft_wrapped_paragraph_joins_lines():
    md = "Robot A succeeds in 93%\nof missions [f_001].\n"
    assert errs(md) == []


def test_abbreviation_does_not_split():
    assert errs("Success 93% vs. 71% [f_001][f_002].") == []
    assert errs("Approx. 93% [f_001] succeed.") == []


# ---------------------------------------------------------------- verdicts
VFACTS = FACTS + [
    F("f_060", "Robot Alpha", "", "Robot model (r_a)", "source_url", "catalog:r_a", key="robot.name|r_a"),
    F("f_061", "Robot Beta", "", "Robot model (r_b)", "source_url", "catalog:r_b", key="robot.name|r_b"),
    F("f_062", "Cleared", "", "Verdict, Robot Alpha", "formula", "verdict:r_a", key="verdict|r_a"),
    F("f_063", "Not cleared", "", "Verdict, Robot Beta", "formula", "verdict:r_b", key="verdict|r_b"),
]


@pytest.mark.parametrize("md", [
    "Robot Alpha is Cleared.",
    "Robot Beta is Not cleared.",
    "Robot Alpha is Cleared, while Robot Beta is Not cleared.",
    "### Robot Alpha: Cleared [f_062]",
    "Cleared means every leg is feasible.",           # no robot mentioned
    "| Robot | Verdict |\n|---|---|\n| Robot Alpha | Cleared |\n| Robot Beta | Not cleared |\n",
])
def test_verdict_labels_accepted(md):
    assert errs(md, VFACTS) == []


@pytest.mark.parametrize("md", [
    "Robot Beta is Cleared.",
    "Robot Alpha is Not cleared.",
    "Robot Beta is Cleared with changes.",
    "Robot Alpha is Cleared, while Robot Beta is Cleared too.",
    "### Robot Beta: Cleared",
    "We rate `r_b` as Cleared.",
    "| Robot | Verdict |\n|---|---|\n| Robot Beta | Cleared |\n",
])
def test_verdict_label_mismatch_rejected(md):
    assert "verdict_mismatch" in types(md, VFACTS)


def test_verdict_mismatch_sentence_removed():
    md = "Robot Alpha is Cleared. Robot Beta is Cleared as well. Both were simulated."
    r = check_report(md, VFACTS)
    assert r["clean_md"] == "Robot Alpha is Cleared. Both were simulated."


def test_verdict_override_argument():
    assert "verdict_mismatch" in types("Robot Alpha is Cleared.", VFACTS, verdicts={"r_a": "Not cleared"})


# ---------------------------------------------------------------- clean_md / enforce
def test_clean_md_removes_only_failing_sentences():
    md = "# Title\n\nGood 93% [f_001]. Bad 55%. Also good 0.18 m [f_003].\n\n- bullet 71%\n- fine 71% [f_002]\n"
    r = check_report(md, FACTS)
    assert not r["ok"]
    assert "Bad 55%" not in r["clean_md"]
    assert "Good 93% [f_001]. Also good 0.18 m [f_003]." in r["clean_md"]
    assert "- bullet" not in r["clean_md"] and "- fine 71% [f_002]" in r["clean_md"]
    assert check_report(r["clean_md"], FACTS)["ok"]


def test_enforce_passes_clean_report_without_regeneration():
    calls = []
    out = enforce("Fine 93% [f_001].", FACTS, lambda md, e: calls.append(1) or md)
    assert out["ok_first_pass"] and not out["regenerated"] and not calls and out["md"] == "Fine 93% [f_001]."


def test_enforce_regenerates_once_with_errors():
    seen = []

    def regen(prev, errors):
        seen.append(errors)
        return "Fixed 93% [f_001]."
    out = enforce("Broken 93%.", FACTS, regen)
    assert len(seen) == 1 and seen[0][0]["type"] == "untagged_number"
    assert out["regenerated"] and out["ok_after_regeneration"] and out["md"] == "Fixed 93% [f_001]."


def test_enforce_removes_and_logs_when_regeneration_still_fails(caplog):
    out = enforce("Keep 93% [f_001]. Drop 55%.", FACTS, lambda prev, e: prev)
    assert not out["ok_after_regeneration"]
    assert out["md"] == "Keep 93% [f_001]."
    assert out["removed"] and "Drop 55%" in out["removed"][0]["segment"]
    assert any("fact-check removed" in r.message for r in caplog.records)
    assert check_report(out["md"], FACTS)["ok"]


def test_enforce_survives_regeneration_error():
    def boom(prev, e):
        raise RuntimeError("network down")
    out = enforce("Drop 55%. Keep 93% [f_001].", FACTS, boom)
    assert not out["regenerated"] and out["md"] == "Keep 93% [f_001]."


# ---------------------------------------------------------------- tokenizer / parser units
def test_tokenizer_details():
    toks = tokenize_numbers(mask_exempt("from $1,200–1,500 and 3x faster, 5 km away, 12k, 1,2345")[0])
    vals = [(t.value, t.currency, t.unit) for t in toks]
    assert (1200.0, "USD", None) in vals and (1500.0, "USD", None) in vals
    assert (3.0, None, "x") in vals and (5.0, None, "km") in vals and (12000.0, None, None) in vals
    assert any(t.value == 2345 for t in toks)  # "1,2345" is not a thousands group: both parts are numbers


def test_split_sentences_roundtrip():
    text = "One is 0.93. Two [f_001]. Three! Four? e.g. five."
    parts = split_sentences(text)
    assert "".join(parts) == text
    assert parts[0] == "One is 0.93. "


def test_parse_blocks_kinds():
    md = "# T\n\npara\nwrapped\n\n- a\n  - b\n1. c\n| x | y |\n|---|---|\n| 1 | 2 |\n---\n> q\n"
    kinds = [b.kind for b in parse_blocks(md) if b.kind != "blank"]
    assert kinds == ["heading", "paragraph", "list_item", "list_item", "list_item", "table_row", "table_sep",
                     "table_row", "hr", "quote"]


def test_many_numbers_stress():
    facts = [F(f"f_{i:03d}", i * 0.5, "m") for i in range(1, 400)]
    md = " ".join(f"Gap {i * 0.5:.1f} m [f_{i:03d}]." for i in range(1, 400))
    r = check_report(md, facts)
    assert r["ok"] and r["stats"]["numbers_checked"] == 399
    bad = md.replace("[f_200]", "[f_300]")
    assert [e["type"] for e in check_report(bad, facts)["errors"]] == ["value_mismatch"]


def test_verdict_claims_checked_inside_sources_section():
    md = "## 12. Sources\n\n- [Spec 2024](https://x.com/a)\n\nRobot Beta is Cleared.\n"
    r = check_report(md, VFACTS)
    assert [e["type"] for e in r["errors"]] == ["verdict_mismatch"]
    assert "Robot Beta is Cleared" not in r["clean_md"] and "Spec 2024" in r["clean_md"]


def test_bare_integer_is_not_a_rounded_ratio():
    from clearance_core.schemas import Fact as _F, FactOrigin as _O
    fs = [_F(fact_id="f_1", value=0.78, unit="ratio", label="Success", origin=_O(type="formula", ref="r")),
          _F(fact_id="f_2", value=1.0, unit="ratio", label="Success", origin=_O(type="formula", ref="r"))]
    assert not check_report("Layout change 1 lifts success to 78% [f_1].", fs)["ok"]
    assert check_report("Success is 78% [f_1], or 0.78 [f_1] as a ratio.", fs)["ok"]
    assert check_report("Success ratio 1 [f_2].", fs)["ok"]
