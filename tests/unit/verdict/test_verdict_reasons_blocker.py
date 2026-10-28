from clearance_core.schemas import LegFeasibility
from clearance_core.verdict import VerdictInputs, decide_verdict, leg_blocker

STOP = ("stop point DOCK is too close to a wall or scanned obstacle for this robot: 0.19 m from it, while the "
        "footprint reaches at least 0.25 m from the robot center at every heading (free width there 0.55 m)")


def _leg(gap, need, reason):
    return LegFeasibility(from_wp="DOCK", to_wp="PICKUP", reachable=False, narrowest_gap_m=gap,
                          required_gap_m=need, reason=reason)


def test_wide_route_reports_the_real_blocker():
    v = decide_verdict("r", VerdictInputs(legs=[_leg(0.55, 0.50, STOP)], success_rate=None))
    assert v.label == "Not cleared"
    assert v.main_reason == ("no path DOCK->PICKUP: stop point DOCK is too close to a wall or scanned obstacle "
                             "for this robot: 0.19 m from it")


def test_narrow_route_reports_the_gap():
    v = decide_verdict("r", VerdictInputs(legs=[_leg(0.52, 0.62, "no path: narrowest gap 0.52 m; turning")],
                                          success_rate=None))
    assert v.main_reason == "no path DOCK->PICKUP: narrowest gap 0.52 m vs 0.62 m needed"


def test_wide_route_without_reason_keeps_gap_text():
    v = decide_verdict("r", VerdictInputs(legs=[_leg(0.55, 0.50, "")], success_rate=None))
    assert v.main_reason == "no path DOCK->PICKUP: narrowest gap 0.55 m vs 0.50 m needed"


def test_leg_blocker_first_clause():
    assert leg_blocker("no path: narrowest gap 0.52 m; turning this footprint there needs 0.62 m") == \
        "narrowest gap 0.52 m"
    assert leg_blocker("") == ""
