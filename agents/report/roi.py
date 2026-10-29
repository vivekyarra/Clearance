"""Deterministic ROI. No LLM ever touches these numbers.

automatable_share = success_rate (nominal geometry, normal traffic) x feasible_leg_frac
monthly_saving    = staff_cost_per_hour x task_hours_per_day x automatable_share x days_per_month
days_per_month    = days_per_week x 52 / 12   (unless days_per_month is given directly)
running_cost      = price x maintenance_pct_per_year / 100 / 12 + other_monthly_cost (+ lease_per_month)
payback_months    = price / (monthly_saving - running_cost)

Edge cases: price unknown and no lease -> status "enter_quote"; net saving <= 0 -> "no_payback";
lease without price -> "lease" (net monthly result, no payback period).
"""
from __future__ import annotations

import math

WEEKS_PER_MONTH = 52.0 / 12.0
DEFAULT_MAINTENANCE_PCT_PER_YEAR = 10.0  # product default, shown to the user as editable

ALIASES = {
    "staff_cost_per_hour": ("staff_cost_per_hour", "staff_cost_per_h", "staff_hourly_cost", "staff_cost_hour",
                            "hourly_cost"),
    "task_hours_per_day": ("task_hours_per_day", "hours_per_day_on_task", "task_hours", "hours_per_day"),
    "days_per_week": ("days_per_week",),
    "days_per_month": ("days_per_month",),
    "price": ("price", "robot_price", "quote"),
    "lease_per_month": ("lease_per_month", "monthly_lease", "lease_monthly"),
    "maintenance_pct_per_year": ("maintenance_pct_per_year", "maintenance_pct", "maintenance_percent"),
    "other_monthly_cost": ("other_monthly_cost", "other_running_cost_per_month", "running_cost_per_month"),
    "currency": ("currency",),
}

FORMULA = (
    "automatable_share = success_rate x feasible_leg_frac; "
    "monthly_saving = staff_cost_per_hour x task_hours_per_day x automatable_share x days_per_month; "
    "days_per_month = days_per_week x 52 / 12; "
    "monthly_running_cost = price x maintenance_pct_per_year / 100 / 12 + other_monthly_cost + lease_per_month; "
    "payback_months = price / (monthly_saving - monthly_running_cost)"
)


class ROIInputError(ValueError):
    """Invalid ROI input; `field` names the input (no numbers), so callers can explain it in words."""

    def __init__(self, field: str, message: str):
        super().__init__(message)
        self.field = field


def _pick(inputs: dict, key: str):
    for k in ALIASES[key]:
        if k in inputs and inputs[k] is not None and inputs[k] != "":
            return inputs[k]
    return None


def _num(name: str, v, *, required: bool = False, allow_none: bool = True) -> float | None:
    if v is None:
        if required or not allow_none:
            raise ROIInputError(name, f"ROI input '{name}' is required")
        return None
    if isinstance(v, bool):
        raise ROIInputError(name, f"ROI input '{name}' must be a number, got bool")
    try:
        f = float(v)
    except (TypeError, ValueError):
        raise ROIInputError(name, f"ROI input '{name}' must be a number, got {v!r}") from None
    if math.isnan(f) or math.isinf(f):
        raise ROIInputError(name, f"ROI input '{name}' must be finite")
    if f < 0:
        raise ROIInputError(name, f"ROI input '{name}' must be >= 0, got {f}")
    return f


def _fraction(name: str, v) -> float:
    f = _num(name, v, required=True)
    if f > 1.0 + 1e-9:
        raise ROIInputError(name, f"{name} must be a fraction in [0, 1], got {f}")
    return min(f, 1.0)


def compute_roi(inputs: dict, success_rate: float, feasible_leg_frac: float) -> dict:
    """Returns {"monthly_saving", "payback_months", "formula", "inputs", ...}.

    Extra keys: status ("ok" | "enter_quote" | "no_payback" | "lease"), message, automatable_share,
    days_per_month, monthly_running_cost, net_monthly_saving, currency.
    Raises ValueError on invalid inputs (negative, NaN, rates outside [0, 1], missing required fields)."""
    sr = _fraction("success_rate", success_rate)
    fl = _fraction("feasible_leg_frac", feasible_leg_frac)
    staff = _num("staff_cost_per_hour", _pick(inputs, "staff_cost_per_hour"), required=True)
    hours = _num("task_hours_per_day", _pick(inputs, "task_hours_per_day"), required=True)
    if hours > 24:
        raise ROIInputError("task_hours_per_day", f"task_hours_per_day must be <= 24, got {hours}")
    dpm_given = _num("days_per_month", _pick(inputs, "days_per_month"))
    dpw = _num("days_per_week", _pick(inputs, "days_per_week"), required=dpm_given is None)
    if dpw is not None and dpw > 7:
        raise ROIInputError("days_per_week", f"days_per_week must be <= 7, got {dpw}")
    if dpm_given is not None and dpm_given > 31:
        raise ROIInputError("days_per_month", f"days_per_month must be <= 31, got {dpm_given}")
    days_per_month = dpm_given if dpm_given is not None else dpw * WEEKS_PER_MONTH
    price = _num("price", _pick(inputs, "price"))
    lease = _num("lease_per_month", _pick(inputs, "lease_per_month"))
    maint = _pick(inputs, "maintenance_pct_per_year")
    maint = DEFAULT_MAINTENANCE_PCT_PER_YEAR if maint is None else _num("maintenance_pct_per_year", maint)
    other = _num("other_monthly_cost", _pick(inputs, "other_monthly_cost")) or 0.0
    currency = str(_pick(inputs, "currency") or "USD").strip().upper()

    share = sr * fl
    saving = staff * hours * share * days_per_month
    maint_monthly = price * maint / 100.0 / 12.0 if price is not None else None
    running = other + (lease or 0.0) + (maint_monthly or 0.0)
    net = saving - running

    if not all(math.isfinite(x) for x in (saving, running, net)):
        raise ROIInputError("inputs", "ROI inputs are too large: the monthly figures are not finite")

    payback = None
    if price is None and lease is None:
        status = "enter_quote"
        message = "Price not public: enter a quote (or monthly lease) to compute payback."
        running = net = None  # maintenance depends on the unknown price
    elif price is None:  # leased
        status = "lease" if net > 0 else "no_payback"
        message = ("Leased: net monthly saving after lease and running costs." if net > 0 else
                   "No payback: lease and running costs meet or exceed the monthly saving.")
    elif net <= 0:
        status = "no_payback"
        message = "No payback: monthly running costs meet or exceed the monthly saving."
    else:
        status = "ok"
        payback = price / net
        if not math.isfinite(payback):
            raise ROIInputError("inputs", "ROI inputs are too large: the payback period is not finite")
        message = "Payback period computed from the inputs below."

    used = {
        "staff_cost_per_hour": staff, "task_hours_per_day": hours, "days_per_week": dpw,
        "days_per_month": days_per_month, "days_per_month_given": dpm_given is not None,
        "price": price, "lease_per_month": lease, "maintenance_pct_per_year": maint,
        "maintenance_is_default": _pick(inputs, "maintenance_pct_per_year") is None,
        "other_monthly_cost": other, "currency": currency,
        "success_rate": sr, "feasible_leg_frac": fl,
    }
    return {
        "monthly_saving": saving,
        "payback_months": payback,
        "formula": FORMULA,
        "inputs": used,
        "status": status,
        "message": message,
        "automatable_share": share,
        "days_per_month": days_per_month,
        "monthly_maintenance": maint_monthly,
        "monthly_running_cost": running,
        "net_monthly_saving": net,
        "currency": currency,
    }
