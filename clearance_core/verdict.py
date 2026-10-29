"""Deterministic Clearance verdicts. Nemotron explains verdicts; it never sets or changes them.

Rules (thresholds from config/sim_defaults.yaml, key `verdict`):
  Cleared              - every task leg feasible on the conservative bound, no unresolved flagged_manual
                         threshold on the route, success at normal traffic (conservative) >= success_min.
  Cleared with changes - not Cleared as-is, but the Cleared rule holds after the optimizer's recommended
                         layout changes and/or once the user measures the flagged thresholds.
  Not cleared          - everything else, with the main reason.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Optional

import math

import yaml

from clearance_core.schemas import LegFeasibility, Verdict, VerdictCheck

CONFIG = Path(__file__).resolve().parent.parent / "config" / "sim_defaults.yaml"


DEFAULTS = {"success_min": 0.90, "density": "normal", "bound": "conservative"}  # built-in defaults
EPS = 1e-9  # float noise in success rates (0.7 + 0.2 < 0.9) never flips a verdict


@lru_cache(maxsize=1)
def _file_config() -> dict:
    try:
        data = yaml.safe_load(CONFIG.read_text(encoding="utf-8")) or {}
    except FileNotFoundError:
        data = {}
    return dict(data.get("verdict") or {})


def load_config() -> dict:
    """Verdict thresholds: config/sim_defaults.yaml `verdict`, with built-in defaults for missing keys."""
    return {**DEFAULTS, **_file_config()}


def resolve_config(config: Optional[dict] = None) -> dict:
    cfg = {**load_config(), **{k: v for k, v in (config or {}).items() if v is not None}}
    try:
        smin = float(cfg["success_min"])
    except (TypeError, ValueError):
        raise ValueError(f"verdict.success_min must be a number, got {cfg['success_min']!r}") from None
    if not math.isfinite(smin) or not 0.0 <= smin <= 1.0:
        raise ValueError(f"verdict.success_min must be a fraction in [0, 1], got {smin!r}")
    cfg["success_min"] = smin
    return cfg


def clean_rate(x) -> Optional[float]:
    """A success rate usable by the rules: finite and within [0, 1] (tiny float overshoot clamped), else None."""
    if x is None or isinstance(x, bool):
        return None
    try:
        f = float(x)
    except (TypeError, ValueError):
        return None
    if not math.isfinite(f) or f < -EPS or f > 1 + EPS:
        return None
    return min(max(f, 0.0), 1.0)


def format_pct(x: float, threshold: Optional[float] = None) -> str:
    """Percent text that never appears to cross `threshold`: 0.8995 vs 0.90 prints 89.9%, not 90%."""
    for d in range(0, 5):
        shown = round(x * 100, d)
        if threshold is None or (shown / 100 >= threshold - EPS) == (x >= threshold - EPS):
            return f"{shown:.{d}f}%"
    shown = math.floor(x * 100 * 1e4) / 1e4
    return f"{shown:.4f}%"


def format_fraction(x: float) -> str:
    """0.9 -> '0.90'; values that need more digits keep them (0.905 -> '0.905')."""
    return f"{x:.2f}" if abs(round(x, 2) - x) < 1e-12 else f"{x:.6g}"


@dataclass
class VerdictInputs:
    """Evidence for one robot in one layout (as-is, or with changes applied)."""
    legs: list[LegFeasibility]                       # feasibility on the conservative bound
    flagged_thresholds: list[str] = field(default_factory=list)  # flagged_manual threshold ids on the route
    success_rate: Optional[float] = None             # normal traffic, conservative bound; None = not simulated
    fact_ids: dict[str, list[str]] = field(default_factory=dict)  # keys: "legs", "thresholds", "success"
    changes: list[dict] = field(default_factory=list)  # optimizer changes applied (with-changes only)


def _pct(x: float, threshold: Optional[float] = None) -> str:
    return format_pct(x, threshold)


def _checks(inp: VerdictInputs, success_min: float, *, assume_thresholds_measured: bool = False) -> list[VerdictCheck]:
    sr = clean_rate(inp.success_rate)
    blocked = [leg for leg in inp.legs if leg.reachable is False]
    unknown = [leg for leg in inp.legs if leg.reachable is None]
    if blocked:
        legs_ok = False
    elif unknown and not assume_thresholds_measured:
        legs_ok = None
    else:
        legs_ok = bool(inp.legs)
    checks = [
        VerdictCheck(rule="every task leg feasible on the conservative geometry bound",
                     passed=legs_ok, value=f"{len(inp.legs) - len(blocked) - len(unknown)}/{len(inp.legs)} legs",
                     fact_ids=inp.fact_ids.get("legs", [])),
        VerdictCheck(rule="no unresolved flagged_manual thresholds on the route",
                     passed=True if (not inp.flagged_thresholds or assume_thresholds_measured) else None,
                     value=", ".join(inp.flagged_thresholds) or "none",
                     fact_ids=inp.fact_ids.get("thresholds", [])),
        VerdictCheck(rule=f"simulated success at normal traffic (conservative bound) >= {format_fraction(success_min)}",
                     passed=None if sr is None else sr >= success_min - EPS,
                     value=sr, threshold=success_min, fact_ids=inp.fact_ids.get("success", [])),
    ]
    return checks


def _cleared(checks: list[VerdictCheck]) -> bool:
    return all(c.passed is True for c in checks)


def leg_blocker(reason: str) -> str:
    """The first clause of a leg's blocking reason, e.g. 'stop point DOCK is too close to a wall or scanned
    obstacle for this robot: 0.19 m from it'. Used when the route is wide enough but the leg is still blocked
    (a stop pose or a turn that the footprint does not fit)."""
    r = (reason or "").strip()
    if r.startswith("no path:"):
        r = r[len("no path:"):].strip()
    for cut in (", while ", "; ", " ("):
        i = r.find(cut)
        if i > 0:
            r = r[:i]
    return r.rstrip(".")


def _main_reason(inp: VerdictInputs, checks: list[VerdictCheck], success_min: float) -> str:
    for leg in inp.legs:
        if leg.reachable is False:
            if leg.blocking_threshold:
                return f"threshold {leg.blocking_threshold} on {leg.from_wp}->{leg.to_wp} exceeds the robot's limit"
            short = leg_blocker(leg.reason)
            gap, need = leg.narrowest_gap_m, leg.required_gap_m
            if gap is not None and need is not None and (gap < need - EPS or not short):
                return (f"no path {leg.from_wp}->{leg.to_wp}: narrowest gap {gap:.2f} m "
                        f"vs {need:.2f} m needed")
            return f"no path {leg.from_wp}->{leg.to_wp}" + (f": {short}" if short else "")
    if inp.flagged_thresholds:
        return f"threshold {', '.join(inp.flagged_thresholds)} must be measured manually"
    if any(leg.reachable is None for leg in inp.legs):
        return "route feasibility unknown"
    sr = clean_rate(inp.success_rate)
    if sr is None:
        if inp.success_rate is not None:
            return "no valid success rate at normal traffic on the conservative bound"
        return "not simulated at normal traffic on the conservative bound"
    if sr < success_min - EPS:
        return f"success {_pct(sr, success_min)} at normal traffic (needs {_pct(success_min)})"
    return "meets every rule"


def decide_verdict(robot_id: str, as_is: VerdictInputs, with_changes: Optional[VerdictInputs] = None,
                   config: Optional[dict] = None) -> Verdict:
    cfg = resolve_config(config)
    smin = float(cfg["success_min"])
    as_is_checks = _checks(as_is, smin)
    if _cleared(as_is_checks):
        return Verdict(robot_id=robot_id, label="Cleared", main_reason="meets every rule as-is",
                       as_is=as_is_checks, config=cfg)

    # Candidate fixes: layout changes (if evaluated) and/or measuring flagged thresholds.
    candidates: list[tuple[VerdictInputs, bool]] = []
    if as_is.flagged_thresholds:
        candidates.append((as_is, True))
    if with_changes is not None:
        candidates.append((with_changes, False))
        if with_changes.flagged_thresholds:
            candidates.append((with_changes, True))
    for inp, measured in candidates:
        checks = _checks(inp, smin, assume_thresholds_measured=measured)
        if _cleared(checks):
            required = list(inp.changes) if inp is with_changes else []
            if measured:
                required += [{"measure_threshold": t} for t in inp.flagged_thresholds]
            parts = []
            if inp is with_changes and inp.changes:
                parts.append(f"{len(inp.changes)} layout change(s)")
            if measured:
                parts.append("measuring threshold " + ", ".join(inp.flagged_thresholds)
                             + " (holds only if it is within the robot's limit)")
            return Verdict(robot_id=robot_id, label="Cleared with changes",
                           main_reason="as-is: " + _main_reason(as_is, as_is_checks, smin)
                           + "; cleared after " + " and ".join(parts),
                           as_is=as_is_checks, with_changes=checks, required_changes=required, config=cfg)

    wc_checks = _checks(with_changes, smin) if with_changes is not None else []
    return Verdict(robot_id=robot_id, label="Not cleared", main_reason=_main_reason(as_is, as_is_checks, smin),
                   as_is=as_is_checks, with_changes=wc_checks, config=cfg)
