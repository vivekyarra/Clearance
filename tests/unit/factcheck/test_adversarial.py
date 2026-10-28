"""Fact-checker regression tests from the adversarial review: every way a writer could slip an unverified
number or a wrong verdict past the checker, plus termination and markdown validity of the repair."""
from __future__ import annotations

import pytest

from agents.factcheck.check import check_report, enforce, tokenize_numbers
from agents.factcheck.mdparse import parse_blocks


def F(fid, value, unit="", label="x", otype="sim_batch", ref="sb_1", key=None):
    d = {"fact_id": fid, "value": value, "unit": unit, "label": label, "origin": {"type": otype, "ref": ref}}
    if key:
        d["key"] = key
    return d


FACTS = [
    F("f_001", 0.93, "ratio"), F("f_002", 0.71, "ratio"), F("f_004", 12000.0, "USD"),
    F("f_005", -300.0, "USD/month"), F("f_006", 0.18, "m"), F("f_012", 4, "count"), F("f_020", 0.895, "ratio"),
    F("f_021", 0.5, "ratio"), F("f_022", 1200.0, "AUD"),
    F("f_060", "OTTO Motors OTTO 100", "", "Robot model (otto)", key="robot.name|otto"),
    F("f_061", "Robot Beta", "", "Robot model (r_b)", key="robot.name|r_b"),
    F("f_062", "Cleared", "", "Verdict, OTTO", "formula", "verdict:otto", key="verdict|otto"),
    F("f_063", "Not cleared", "", "Verdict, Robot Beta", "formula", "verdict:r_b", key="verdict|r_b"),
    F("f_064", "Northwind Tray T2", "", "Robot model (tray)", key="robot.name|tray"),
    F("f_065", "Cleared with changes", "", "Verdict, tray", "formula", "verdict:tray", key="verdict|tray"),
    F("f_066", "Mobile Industrial Robots MiR250", "", "Robot model (mir)", key="robot.name|mir"),
    F("f_067", "Cafe 5", "", "Site name", "user_input", "site:s1", key="site.name"),
]


def types(md, facts=FACTS, **kw):
    return [e["type"] for e in check_report(md, facts, **kw)["errors"]]


# ---------------------------------------------------------------- numbers that used to pass unchecked
@pytest.mark.parametrize("md", [
    "Robot A succeeds ninety percent of the time.",
    "Robot A succeeds nine times out of ten.",
    "Robot A succeeds ninety-three percent [f_002] of the time.",   # word number, wrong fact
    "Zero collisions were seen.",
    "Robot A succeeds ⁹⁹% of the time.",                            # superscript digits
    "Robot A succeeds in ½ of missions.",                           # vulgar fraction
    "Robot A succeeds 10² times.",
    "Robot A succeeds `99%` of the time.",                          # quantity in inline code
    "```\nSuccess 99%\n```",                                       # quantity in a code fence
    "Robot A 71% [f_001], Robot B 93% [f_002].",                   # tags swapped between numbers
    "Success 4% [f_012].",                                         # a count printed as a percent
    "A gain of 5 pp [f_012].",
    "Robot A succeeds 99pct of the time.",
    "Robot A succeeds about93% of the time.",
    "The price is 12,000 € [f_004].",                              # symbol after the number
    "The price is A$12,000 [f_004].",                              # other dollar
    "The price is 12000USD [f_005].",
    "Success is –93% [f_001].",                                    # en-dash minus
    "Success is 9.3e1% [f_002].",                                  # scientific notation
    "Price 1.2e4 dollars.",
    "## References\n\nRobot A succeeds 99% of the time.\n",        # prose under a sources heading
    "| A | B |\n|---|---|\n| Robot A | 93% [f_001] |\n| Robot B | 50% |\n",
    "Robot | Success\n---|---\nA | 93% [f_001]\nB | 50%\n",         # table without leading pipes
])
def test_slipped_numbers_rejected(md):
    assert types(md), md


@pytest.mark.parametrize("md", [
    "Robot A succeeds ninety-three percent [f_001] of the time.",
    "Robot A succeeds 93％ [f_001].",                              # fullwidth percent sign
    "Robot A succeeds ٩٣% [f_001].",                               # Arabic-Indic digits
    "Robot A succeeds 93% [f_001][^1].",                           # footnote marker
    "[^1]: Wilson score interval.",
    "Robot A - 93% [f_001] success.",                               # a dash, not a sign
    "Success between 71 and 93% [f_002][f_001].",
    "Success 90% [f_020].",                                         # 0.895 rounds to 90%
    "OTTO 100 reaches 93% [f_001].",                                # model name with a standalone number
    "otto 100 reaches 93% [f_001].",
    "MiR250 and Tray T2 reach 93% [f_001].",
    "One of the robots reaches 93% [f_001].",                       # "one" as a pronoun
    "Floor area is given in m² and CO₂ is not measured.",           # exponents after letters are units
    "The price is A$1,200 [f_022].",
    "The price is 12,000 USD [f_004].",
    "Net saving is –$300 [f_005].",
    "Run `sb_2026_1000` and batch `pb-slim-2` with `x = 42`.",
    "# Clearance Report: Cafe 5",                                   # site name from the facts pack
    "Robot | Success\n---|---\nA | 93% [f_001]\nB | 71% [f_002]\n",
])
def test_legitimate_forms_accepted(md):
    assert types(md) == [], check_report(md, FACTS)["errors"]


def test_pp_does_not_crash_and_matches_percent():
    assert types("A change of 50 pp [f_021].") == []


def test_word_numbers_tokenized_with_units():
    toks = tokenize_numbers("twelve thousand dollars and ninety-three percent and five metres")
    vals = [(t.value, t.currency, t.pct, t.unit) for t in toks]
    assert (12000.0, "USD", False, None) in vals and (93.0, None, True, None) in vals and (5.0, None, False, "metres") in vals


# ---------------------------------------------------------------- verdict phrasing
@pytest.mark.parametrize("md", [
    "Tray T2 is Cleared.",                                  # model name without the vendor
    "Robot Beta is **Cleared**.",
    "OTTO 100 is **not** cleared.",
    "OTTO 100 is not  cleared.",
    "OTTO 100 isn't cleared.",
    "OTTO 100 cannot be cleared.",
    "OTTO 100 is _not_ cleared.",
    "OTTO 100 is uncleared.",
    "OTTO 100 is cleared with modifications.",
    "OTTO 100 is CLEARED WITH CHANGES.",
    "Northwind Tray T2 is not cleared.",
    "| | Robot Beta |\n|---|---|\n| Verdict | Cleared |\n",   # robots as columns
    "| Robot | Verdict |\n|---|---|\n| **Robot Beta** | *cleared* |\n",
])
def test_rephrased_wrong_verdicts_rejected(md):
    assert "verdict_mismatch" in types(md), md


@pytest.mark.parametrize("md", [
    "Northwind Tray T2 is cleared with changes.",
    "Tray T2 is Cleared with changes after the recommended layout changes.",
    "OTTO 100 is **Cleared**.",
    "Robot Beta is *not cleared*.",
    "Robot Beta is not yet cleared.",
    "| | OTTO 100 | Robot Beta |\n|---|---|---|\n| Verdict | Cleared | Not cleared |\n",
])
def test_rephrased_right_verdicts_accepted(md):
    assert types(md) == [], check_report(md, FACTS)["errors"]


# ---------------------------------------------------------------- repair: termination and valid markdown
def test_enforce_rejects_non_text_regeneration():
    out = enforce("Bad 55%. Keep 93% [f_001].", FACTS, lambda prev, e: {"md": prev})
    assert not out["regenerated"] and out["md"] == "Keep 93% [f_001]." and out["ok_final"]


def test_enforce_calls_regeneration_once_even_if_it_keeps_failing():
    calls = []
    out = enforce("Bad 55%.", FACTS, lambda prev, e: calls.append(1) or "Still bad 56%.")
    assert calls == [1] and out["ok_final"] and check_report(out["md"], FACTS)["ok"]


def test_removal_never_turns_prose_into_other_blocks():
    md = "Bad 55%. | a | b |\n\nBad 56%. # Heading\n\nBad 57%. - item\n\nBad 58%. 1. item\n"
    out = enforce(md, FACTS)
    assert out["ok_final"]
    kinds = [b.kind for b in parse_blocks(out["md"]) if b.kind != "blank"]
    assert kinds == ["paragraph"] * 4, (kinds, out["md"])


def test_pipe_less_table_keeps_rows_and_shape():
    md = "Robot | Success | Price\n---|---|---\nA | 93% [f_001] | $12,000 [f_004]\nB | 50% | $13,000 [f_004]\n"
    r = check_report(md, FACTS)
    rows = [b for b in parse_blocks(r["clean_md"]) if b.kind == "table_row"]
    assert [len(b.cells) for b in rows] == [3, 3, 3]
    assert rows[1].cells[1] == "93% [f_001]" and rows[2].cells[1:] == ["—", "—"]


def test_enforce_terminates_on_large_adversarial_report():
    para = " ".join(f"Robot {i} at {i}% and ninety percent [f_001]." for i in range(300))
    table = "| a | b |\n|---|---|\n" + "\n".join(f"| r{i} | {i}% |" for i in range(300))
    md = "\n\n".join(["# T", para, table, "```\nSuccess 99%\n```", "## Sources", "Prose 99%."] * 3)
    out = enforce(md, FACTS, lambda prev, e: prev)
    assert out["ok_final"] and check_report(out["md"], FACTS)["ok"]
    rows = [b for b in parse_blocks(out["md"]) if b.kind == "table_row"]
    assert rows and all(len(b.cells) == 2 for b in rows)


def test_unclosed_fence_and_none_input():
    assert check_report(None, FACTS)["ok"]
    assert enforce(None, FACTS)["md"] == ""
    assert "untagged_number" in types("```\nSuccess 99%\n")


@pytest.mark.parametrize("text", ["1," * 20000, "-1 " * 20000, "$1 " * 20000, "Robot Beta is cleared. " * 3000],
                         ids=["commas", "signs", "dollars", "verdicts"])
def test_long_lines_check_in_linear_time(text):
    import time
    t = time.perf_counter()
    check_report(text, FACTS)
    assert time.perf_counter() - t < 10.0


def test_render_of_many_brackets_is_fast():
    import time
    from agents.factcheck.render import render_html
    t = time.perf_counter()
    render_html("[" * 20000 + "](x)", [])
    assert time.perf_counter() - t < 2.0
