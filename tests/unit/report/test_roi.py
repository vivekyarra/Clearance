from __future__ import annotations

import math

import pytest

from agents.report.roi import DEFAULT_MAINTENANCE_PCT_PER_YEAR, WEEKS_PER_MONTH, compute_roi

BASE = {"staff_cost_per_hour": 20.0, "task_hours_per_day": 5.0, "days_per_week": 6, "price": 20000.0,
        "maintenance_pct_per_year": 12.0}


def test_basic_formula():
    r = compute_roi(BASE, 0.9, 0.8)
    share = 0.9 * 0.8
    dpm = 6 * WEEKS_PER_MONTH
    saving = 20 * 5 * share * dpm
    running = 20000 * 12 / 100 / 12
    assert r["automatable_share"] == pytest.approx(share)
    assert r["days_per_month"] == pytest.approx(26.0)
    assert r["monthly_saving"] == pytest.approx(saving)
    assert r["monthly_running_cost"] == pytest.approx(running)
    assert r["net_monthly_saving"] == pytest.approx(saving - running)
    assert r["payback_months"] == pytest.approx(20000 / (saving - running))
    assert r["status"] == "ok"
    assert "payback_months = price / (monthly_saving - monthly_running_cost)" in r["formula"]
    for k in ("staff_cost_per_hour", "task_hours_per_day", "days_per_week", "price", "maintenance_pct_per_year",
              "success_rate", "feasible_leg_frac"):
        assert k in r["inputs"]


def test_price_unknown_asks_for_quote():
    inp = {k: v for k, v in BASE.items() if k != "price"}
    r = compute_roi(inp, 0.9, 1.0)
    assert r["status"] == "enter_quote" and r["payback_months"] is None
    assert "quote" in r["message"] and r["monthly_saving"] > 0
    assert r["net_monthly_saving"] is None and r["monthly_running_cost"] is None


def test_price_none_value_same_as_missing():
    assert compute_roi(dict(BASE, price=None), 0.9, 1.0)["status"] == "enter_quote"


def test_saving_below_running_costs_no_payback():
    r = compute_roi(dict(BASE, task_hours_per_day=0.2, other_monthly_cost=500), 0.9, 1.0)
    assert r["status"] == "no_payback" and r["payback_months"] is None and r["net_monthly_saving"] < 0


def test_saving_exactly_equal_running_costs_no_payback():
    saving = 20 * 5 * 1.0 * 26.0
    r = compute_roi(dict(BASE, days_per_week=None, days_per_month=26.0, price=0.0, other_monthly_cost=saving),
                    1.0, 1.0)
    assert r["status"] == "no_payback"


def test_zero_success_no_payback():
    r = compute_roi(BASE, 0.0, 1.0)
    assert r["monthly_saving"] == 0 and r["status"] == "no_payback"


def test_zero_price_pays_back_immediately():
    r = compute_roi(dict(BASE, price=0.0), 0.9, 1.0)
    assert r["status"] == "ok" and r["payback_months"] == 0.0


def test_lease_without_price():
    inp = {k: v for k, v in BASE.items() if k != "price"}
    r = compute_roi(dict(inp, lease_per_month=800), 0.9, 1.0)
    assert r["status"] == "lease" and r["payback_months"] is None
    assert r["net_monthly_saving"] == pytest.approx(r["monthly_saving"] - 800)
    r2 = compute_roi(dict(inp, lease_per_month=1e6), 0.9, 1.0)
    assert r2["status"] == "no_payback"


def test_default_maintenance_applied_and_flagged():
    inp = {k: v for k, v in BASE.items() if k != "maintenance_pct_per_year"}
    r = compute_roi(inp, 0.9, 1.0)
    assert r["inputs"]["maintenance_pct_per_year"] == DEFAULT_MAINTENANCE_PCT_PER_YEAR
    assert r["inputs"]["maintenance_is_default"] is True
    assert r["monthly_maintenance"] == pytest.approx(20000 * DEFAULT_MAINTENANCE_PCT_PER_YEAR / 100 / 12)


def test_aliases_accepted():
    r = compute_roi({"staff_hourly_cost": 20, "hours_per_day_on_task": 5, "days_per_week": 6, "robot_price": 20000,
                     "maintenance_pct": 12}, 0.9, 0.8)
    assert r["payback_months"] == pytest.approx(compute_roi(BASE, 0.9, 0.8)["payback_months"])


def test_days_per_month_direct():
    r = compute_roi(dict(BASE, days_per_week=None, days_per_month=22), 1.0, 1.0)
    assert r["days_per_month"] == 22 and r["monthly_saving"] == pytest.approx(20 * 5 * 22)


@pytest.mark.parametrize("bad", [
    dict(BASE, staff_cost_per_hour=-1), dict(BASE, price=-5), dict(BASE, task_hours_per_day=25),
    dict(BASE, days_per_week=8), dict(BASE, staff_cost_per_hour=float("nan")), dict(BASE, price="abc"),
    dict(BASE, staff_cost_per_hour=True), {k: v for k, v in BASE.items() if k != "staff_cost_per_hour"},
    {k: v for k, v in BASE.items() if k != "days_per_week"}, dict(BASE, price=float("inf")),
])
def test_invalid_inputs_raise(bad):
    with pytest.raises(ValueError):
        compute_roi(bad, 0.9, 1.0)


@pytest.mark.parametrize("sr,fl", [(1.2, 1.0), (-0.1, 1.0), (0.5, 1.5), (None, 1.0), (0.5, float("nan"))])
def test_invalid_rates_raise(sr, fl):
    with pytest.raises(ValueError):
        compute_roi(BASE, sr, fl)


def test_deterministic_and_finite_over_grid():
    seen = set()
    for sr in [0, 0.25, 0.5, 0.99, 1.0]:
        for fl in [0, 0.5, 1.0]:
            for price in [None, 0, 5000, 1e6]:
                r1 = compute_roi(dict(BASE, price=price), sr, fl)
                r2 = compute_roi(dict(BASE, price=price), sr, fl)
                assert r1 == r2
                assert math.isfinite(r1["monthly_saving"])
                if r1["payback_months"] is not None:
                    assert r1["payback_months"] >= 0 and r1["status"] == "ok"
                seen.add(r1["status"])
    assert {"ok", "no_payback", "enter_quote"} <= seen


def test_console_business_inputs_are_read():
    # the web console sends staff_cost_per_h, robot_price and monthly_lease
    inp = {"staff_cost_per_h": 150, "task_hours_per_day": 2, "days_per_week": 6, "price_mode": "public_price",
           "robot_price": None, "monthly_lease": None, "maintenance_pct_per_year": 10, "currency": "USD"}
    r = compute_roi(inp, 0.99, 1.0)
    assert r["status"] == "enter_quote"
    assert r["monthly_saving"] == pytest.approx(150 * 2 * 0.99 * 6 * WEEKS_PER_MONTH)
