"""Property-style tests of the code verdict: monotonicity, invalid success rates, config handling."""
from __future__ import annotations

import math
import random

import pytest

from clearance_core.schemas import LegFeasibility
from clearance_core.verdict import VerdictInputs, decide_verdict, format_pct, resolve_config

RANK = {"Cleared": 0, "Cleared with changes": 1, "Not cleared": 2}
CFG = {"success_min": 0.90}


def _leg(i, reach):
    return LegFeasibility(from_wp=f"W{i}", to_wp=f"W{i + 1}", reachable=reach, narrowest_gap_m=0.7,
                          required_gap_m=0.62, blocking_threshold="D1" if reach is None else None)


def _random_case(rnd):
    legs = [_leg(i, rnd.choice([True, True, True, None, False])) for i in range(rnd.randint(0, 4))]
    flagged = ["D1"] if rnd.random() < 0.4 else []
    sr = rnd.choice([None, 0.0, 0.5, 0.8995, 0.9, 0.95, 1.0, rnd.random()])
    wc = None
    if rnd.random() < 0.6:
        wlegs = [l if l.reachable is not False or rnd.random() < 0.5 else _leg(i, True) for i, l in enumerate(legs)]
        wc = VerdictInputs(legs=wlegs, flagged_thresholds=list(flagged) if rnd.random() < 0.5 else [],
                           success_rate=rnd.choice([None, rnd.random(), 0.92]), changes=[{"remove": ["C3"]}])
    return legs, flagged, sr, wc


def _label(legs, flagged, sr, wc):
    return decide_verdict("r", VerdictInputs(legs=legs, flagged_thresholds=flagged, success_rate=sr), wc,
                          config=CFG).label


@pytest.mark.parametrize("seed", range(400))
def test_higher_success_never_worsens(seed):
    rnd = random.Random(seed)
    legs, flagged, sr, wc = _random_case(rnd)
    lo = rnd.random() if sr is None else sr
    hi = min(1.0, lo + rnd.random() * (1 - lo))
    assert RANK[_label(legs, flagged, hi, wc)] <= RANK[_label(legs, flagged, lo, wc)]
    if wc is not None and wc.success_rate is not None:  # same for the with-changes success
        better = VerdictInputs(legs=wc.legs, flagged_thresholds=wc.flagged_thresholds, changes=wc.changes,
                               success_rate=min(1.0, wc.success_rate + 0.05))
        assert RANK[_label(legs, flagged, lo, better)] <= RANK[_label(legs, flagged, lo, wc)]


@pytest.mark.parametrize("seed", range(400))
def test_adding_a_blocked_leg_never_improves(seed):
    rnd = random.Random(seed)
    legs, flagged, sr, wc = _random_case(rnd)
    blocked = _leg(9, False)
    wc2 = None if wc is None else VerdictInputs(legs=wc.legs + [blocked], flagged_thresholds=wc.flagged_thresholds,
                                                success_rate=wc.success_rate, changes=wc.changes)
    before = _label(legs, flagged, sr, wc)
    assert RANK[_label(legs + [blocked], flagged, sr, wc)] >= RANK[before]
    assert _label(legs + [blocked], flagged, sr, wc2) == "Not cleared"


@pytest.mark.parametrize("seed", range(200))
def test_adding_a_flagged_threshold_never_improves(seed):
    """With every leg decided, an extra flagged threshold can only add a pending check. (A pending leg with no
    flagged id stays Not cleared; naming its threshold is what makes "measure on site" a valid fix.)"""
    legs, flagged, sr, wc = _random_case(random.Random(seed))
    legs = [l if l.reachable is not None else _leg(i, True) for i, l in enumerate(legs)]
    if wc is not None:
        wc = VerdictInputs(legs=[l if l.reachable is not None else _leg(i, True) for i, l in enumerate(wc.legs)],
                           flagged_thresholds=wc.flagged_thresholds, success_rate=wc.success_rate, changes=wc.changes)
    assert RANK[_label(legs, flagged + ["D9"], sr, wc)] >= RANK[_label(legs, flagged, sr, wc)]


@pytest.mark.parametrize("bad", [float("nan"), float("inf"), -float("inf"), 1.5, -0.2, "0.95", True])
def test_invalid_success_rate_is_never_cleared(bad):
    legs = [_leg(0, True)]
    inp = VerdictInputs(legs=legs, success_rate=bad)
    v = decide_verdict("r", inp, config=CFG)
    if bad == "0.95":  # a numeric string is a usable number
        assert v.label == "Cleared"
        return
    assert v.label == "Not cleared"
    assert v.main_reason != "meets every rule" and "nan" not in v.main_reason.lower()
    assert v.as_is[2].passed is None and v.as_is[2].value is None


def test_nan_with_changes_does_not_clear():
    legs = [_leg(0, True)]
    v = decide_verdict("r", VerdictInputs(legs=legs, success_rate=0.5),
                       VerdictInputs(legs=legs, success_rate=float("nan"), changes=[{}]), config=CFG)
    assert v.label == "Not cleared"


def test_float_noise_at_threshold_does_not_flip():
    legs = [_leg(0, True)]
    assert decide_verdict("r", VerdictInputs(legs=legs, success_rate=0.7 + 0.2), config=CFG).label == "Cleared"


@pytest.mark.parametrize("sr", [0.8995, 0.895, 0.89999, 0.8999999])
def test_reason_never_shows_success_at_the_threshold_when_below(sr):
    v = decide_verdict("r", VerdictInputs(legs=[_leg(0, True)], success_rate=sr), config=CFG)
    assert v.label == "Not cleared"
    shown = float(v.main_reason.split("success ")[1].split("%")[0])
    assert shown < 90.0, v.main_reason


def test_format_pct():
    assert format_pct(0.93) == "93%" and format_pct(0.8995, 0.9) in ("89.9%", "89.95%") and format_pct(0.9, 0.9) == "90%"
    assert format_pct(0.904, 0.9) == "90%" and format_pct(0.0, 0.9) == "0%"


@pytest.mark.parametrize("cfg", [{}, None, {"density": "normal"}, {"success_min": None}])
def test_config_missing_key_uses_default(cfg):
    v = decide_verdict("r", VerdictInputs(legs=[_leg(0, True)], success_rate=0.95), config=cfg)
    assert v.label == "Cleared" and v.config["success_min"] == 0.90


@pytest.mark.parametrize("cfg", [{"success_min": float("nan")}, {"success_min": 1.5}, {"success_min": "high"}])
def test_config_invalid_threshold_raises(cfg):
    with pytest.raises(ValueError):
        decide_verdict("r", VerdictInputs(legs=[_leg(0, True)], success_rate=0.95), config=cfg)


def test_rule_text_keeps_non_round_threshold():
    v = decide_verdict("r", VerdictInputs(legs=[_leg(0, True)], success_rate=0.95), config={"success_min": 0.905})
    assert v.as_is[2].rule.endswith(">= 0.905") and resolve_config({"success_min": 0.905})["success_min"] == 0.905
    assert math.isclose(v.as_is[2].threshold, 0.905)


def test_measured_threshold_reason_has_no_verdict_label():
    legs = [_leg(0, True), _leg(1, None)]
    v = decide_verdict("r", VerdictInputs(legs=legs, flagged_thresholds=["D1"], success_rate=0.95), config=CFG)
    assert v.label == "Cleared with changes"
    assert "cleared only" not in v.main_reason.lower()
