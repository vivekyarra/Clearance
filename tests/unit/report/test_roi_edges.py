"""ROI edge cases: zero/negative/huge inputs, leases, currency consistency, and the payback status text."""
from __future__ import annotations

import math

import pytest

from agents.factcheck.check import check_report
from agents.report.facts import build_facts
from agents.report.roi import ROIInputError, compute_roi

BASE = {"staff_cost_per_hour": 20, "task_hours_per_day": 6, "days_per_week": 5, "price": 12000}


@pytest.mark.parametrize("bad,field", [({"staff_cost_per_hour": -1}, "staff_cost_per_hour"),
                                       ({"price": -5}, "price"), ({"task_hours_per_day": 25}, "task_hours_per_day"),
                                       ({"lease_per_month": float("inf")}, "lease_per_month")])
def test_invalid_inputs_name_the_field(bad, field):
    with pytest.raises(ROIInputError) as ei:
        compute_roi(dict(BASE, **bad), 0.9, 1.0)
    assert ei.value.field == field


@pytest.mark.parametrize("big", [{"staff_cost_per_hour": 1e308, "task_hours_per_day": 24},
                                 {"price": 1e308, "maintenance_pct_per_year": 1e308}])
def test_huge_inputs_raise_instead_of_returning_inf(big):
    with pytest.raises(ROIInputError):
        compute_roi(dict(BASE, **big), 1.0, 1.0)


def test_huge_but_finite_inputs_stay_finite():
    r = compute_roi(dict(BASE, staff_cost_per_hour=1e12, price=1e15), 1.0, 1.0)
    assert r["status"] == "ok" and all(math.isfinite(r[k]) for k in ("monthly_saving", "payback_months"))


def test_zero_staff_cost_is_no_payback():
    r = compute_roi(dict(BASE, staff_cost_per_hour=0), 1.0, 1.0)
    assert r["status"] == "no_payback" and r["payback_months"] is None


def test_lease_and_price_both_counted():
    r = compute_roi(dict(BASE, lease_per_month=100), 1.0, 1.0)
    assert math.isclose(r["monthly_running_cost"], 12000 * 0.10 / 12 + 100)


def test_lease_exceeding_saving():
    r = compute_roi({k: v for k, v in BASE.items() if k != "price"} | {"lease_per_month": 1e6}, 1.0, 1.0)
    assert r["status"] == "no_payback" and r["net_monthly_saving"] < 0


def test_currency_normalized():
    assert compute_roi(dict(BASE, currency=" eur "), 1.0, 1.0)["currency"] == "EUR"


def _analysis(biz, price=None, currency="USD"):
    from clearance_core.schemas import BatchResults, Feasibility, LegFeasibility, RobotSpec, SimBatch, Task
    spec = RobotSpec(**{"robot_id": "r1", "class": "A_service", "vendor": "Acme", "model": "Runner 300",
                        "price": {"value": price, "currency": currency, "is_public": price is not None}})
    task = Task(sequence=["A", "B"])
    b = SimBatch(batch_id="sb", twin_id="t", twin_version=1, robot_id="r1", task=task, density="normal",
                 geometry_bound="nominal", n_episodes=100, seed_base=1,
                 results=BatchResults(success_rate=0.9, ci95=(0.8, 0.95), n_episodes=100))
    f = Feasibility(robot_id="r1", twin_id="t", twin_version=1, all_reachable=True,
                    legs=[LegFeasibility(from_wp="A", to_wp="B", reachable=True)])
    return {"analysis_id": "a", "business": biz, "candidates": [spec], "batches": [b], "feasibility": {"r1": f}}


@pytest.mark.parametrize("biz", [{"staff_cost_per_hour": -5, "task_hours_per_day": 6, "days_per_week": 5},
                                 {"staff_cost_per_hour": 20, "task_hours_per_day": 30, "days_per_week": 5},
                                 {"staff_cost_per_hour": 1e308, "task_hours_per_day": 24, "days_per_week": 7,
                                  "price": 1}])
def test_invalid_business_inputs_status_has_no_numbers(biz):
    facts = build_facts(_analysis(biz))
    st = next(f for f in facts if f.key == "roi.status|r1")
    assert st.value.startswith("payback needs") and not any(ch.isdigit() for ch in st.value)
    assert check_report(f"Payback: {st.value} [{st.fact_id}].", facts)["ok"]


def test_public_price_in_other_currency_is_not_mixed():
    facts = build_facts(_analysis({"staff_cost_per_hour": 20, "task_hours_per_day": 6, "days_per_week": 5,
                                   "currency": "usd"}, price=9000, currency="EUR"))
    k = {f.key: f for f in facts}
    assert "roi.price|r1" not in k and k["roi.status|r1"].value == "enter a quote"
    assert k["biz.staff_cost_per_hour"].unit == "USD/h"
