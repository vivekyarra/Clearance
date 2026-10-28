from clearance_core.schemas import LegFeasibility
from clearance_core.verdict import VerdictInputs, decide_verdict, load_config

CFG = {"success_min": 0.90}


def leg(a, b, ok=True, gap=None, need=None, thr=None):
    return LegFeasibility(from_wp=a, to_wp=b, reachable=ok, narrowest_gap_m=gap, required_gap_m=need,
                          blocking_threshold=thr)


LEGS_OK = [leg("DOCK", "PICKUP"), leg("PICKUP", "T1"), leg("T1", "DOCK")]


def test_config_default_threshold():
    assert load_config()["success_min"] == 0.90


def test_cleared():
    v = decide_verdict("r", VerdictInputs(legs=LEGS_OK, success_rate=0.95, fact_ids={"success": ["f_001"]}), config=CFG)
    assert v.label == "Cleared"
    assert all(c.passed for c in v.as_is)
    assert v.as_is[2].fact_ids == ["f_001"]


def test_cleared_at_exact_threshold():
    assert decide_verdict("r", VerdictInputs(legs=LEGS_OK, success_rate=0.90), config=CFG).label == "Cleared"


def test_low_success_not_cleared_reason():
    v = decide_verdict("r", VerdictInputs(legs=LEGS_OK, success_rate=0.64), config=CFG)
    assert v.label == "Not cleared"
    assert v.main_reason == "success 64% at normal traffic (needs 90%)"


def test_no_path_reason_uses_gap():
    legs = [leg("DOCK", "PICKUP"), leg("PICKUP", "T4", ok=False, gap=0.62, need=0.70)]
    v = decide_verdict("r", VerdictInputs(legs=legs, success_rate=None), config=CFG)
    assert v.label == "Not cleared"
    assert v.main_reason == "no path PICKUP->T4: narrowest gap 0.62 m vs 0.70 m needed"


def test_cleared_with_layout_changes():
    as_is = VerdictInputs(legs=LEGS_OK, success_rate=0.71)
    change = {"move": {"T4": {"dx": 0.18, "dy": 0.0, "dyaw": 0.0}}}
    wc = VerdictInputs(legs=LEGS_OK, success_rate=0.93, changes=[change])
    v = decide_verdict("r", as_is, wc, config=CFG)
    assert v.label == "Cleared with changes"
    assert v.required_changes == [change]
    assert "success 71%" in v.main_reason


def test_cleared_with_changes_after_measuring_threshold():
    legs = [leg("DOCK", "PICKUP"), LegFeasibility(from_wp="PICKUP", to_wp="T1", reachable=None)]
    v = decide_verdict("r", VerdictInputs(legs=legs, flagged_thresholds=["D1"], success_rate=0.95), config=CFG)
    assert v.label == "Cleared with changes"
    assert {"measure_threshold": "D1"} in v.required_changes


def test_changes_that_do_not_reach_threshold_stay_not_cleared():
    v = decide_verdict("r", VerdictInputs(legs=LEGS_OK, success_rate=0.5),
                       VerdictInputs(legs=LEGS_OK, success_rate=0.85, changes=[{"remove": ["C3"]}]), config=CFG)
    assert v.label == "Not cleared"
    assert v.with_changes[2].passed is False


def test_hard_block_is_not_fixed_by_measuring():
    legs = [leg("DOCK", "T1", ok=False, thr="D2")]
    v = decide_verdict("r", VerdictInputs(legs=legs, flagged_thresholds=["D1"], success_rate=0.99), config=CFG)
    assert v.label == "Not cleared"
    assert "threshold D2" in v.main_reason


def test_unsimulated_is_never_cleared():
    v = decide_verdict("r", VerdictInputs(legs=LEGS_OK, success_rate=None), config=CFG)
    assert v.label == "Not cleared"
    assert "not simulated" in v.main_reason


def test_empty_route_is_not_cleared():
    assert decide_verdict("r", VerdictInputs(legs=[], success_rate=1.0), config=CFG).label == "Not cleared"
