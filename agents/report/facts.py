"""Facts pack builder for the Clearance Report. Every number the report may
print becomes a Fact with a stable id.

`build_facts(analysis)` accepts a hydrated analysis dict (pydantic models or plain dicts):

    {
      "analysis_id": str,
      "site": {"site_id", "name"}?,
      "twin": Twin,                                    # accuracy, thresholds, hazards, traffic, floor
      "measurements": [{"name", "value_m", "twin_value_m"?}]?,   # on-site tape checks
      "business": {...} | "inputs": {"business": {...}},  # ROI inputs (roi.py); per robot:
                                                          #   business["quotes"][robot_id], business["leases"][robot_id]
      "candidates": [RobotSpec | robot_id], "robots": {robot_id: RobotSpec}?,
      "feasibility": {robot_id: Feasibility} | [Feasibility],
      "batches": [SimBatch | batch_id],                 # ids load from analyses/<id>/batches/<bid>.json
      "optimizer": {robot_id: [result]} | [result (+ "robot_id")],
      "verdicts": [Verdict]?,                           # from clearance_core.verdict; derived here if absent
      "investigation": Investigation | "investigations": [...] | "investigation_id",
      "investigation_facts": [Fact] | {id: Fact}?,      # facts registered by the loop (or investigation["facts"])
      "roi": {robot_id: compute_roi(...)}?,             # computed here when absent
      "citations": [{"url", "title", "retrieved_at"}]?,
    }

Ids follow a canonical order (site, business, then each robot in candidate order: spec, feasibility,
simulation by density x bound, optimizer, ROI, verdict; then investigation trail), so the same analysis
always yields the same ids regardless of batch list order. Investigation facts keep their own ids
unless they collide with a core id.
"""
from __future__ import annotations

import math
import re
from typing import Any, Iterable

from clearance_core.schemas import Fact, FactOrigin, LegFeasibility, Verdict
from clearance_core.verdict import leg_blocker

from agents.factcheck.check import UNITS, mask_exempt, tokenize_numbers
from agents.report.roi import ALIASES, DEFAULT_MAINTENANCE_PCT_PER_YEAR, ROIInputError, compute_roi

DENSITY_ORDER = ["none", "quiet", "normal", "peak"]
BOUND_ORDER = ["conservative", "nominal", "optimistic"]
VERDICT_RANK = {"Cleared": 0, "Cleared with changes": 1, "Not cleared": 2}
PRICE_ON_REQUEST = "price on request"
SPEC_FIELDS = [  # (RobotSpec field, key suffix, unit, label)
    ("height_m", "height_m", "m", "height"),
    ("max_speed_mps", "max_speed_mps", "m/s", "max speed (vendor)"),
    ("nominal_speed_mps", "nominal_speed_mps", "m/s", "nominal speed (vendor)"),
    ("max_threshold_m", "max_threshold_m", "m", "max threshold height (vendor)"),
    ("max_slope_deg", "max_slope_deg", "deg", "max slope (vendor)"),
    ("min_aisle_width_vendor_m", "min_aisle_m", "m", "minimum aisle width (vendor)"),
    ("payload_kg", "payload_kg", "kg", "payload (vendor)"),
    ("runtime_h", "runtime_h", "h", "runtime per charge (vendor)"),
    ("safety_stop_distance_m", "safety_stop_m", "m", "safety stop distance (vendor)"),
]
BUSINESS_FIELDS = [  # (canonical key, unit ('{cur}' = currency), label)
    ("staff_cost_per_hour", "{cur}/h", "Staff cost per hour"),
    ("task_hours_per_day", "h/day", "Staff hours per day spent on the task"),
    ("days_per_week", "days/week", "Operating days per week"),
    ("days_per_month", "days/month", "Operating days per month"),
    ("deliveries_per_day", "count/day", "Deliveries per day"),
    ("hours_open", "h/day", "Hours open per day"),
    ("other_monthly_cost", "{cur}/month", "Other running costs per month"),
]


class ReportFact(Fact):
    """A Fact plus a semantic key the template writer uses to find it (e.g. 'sim.success|r1|normal|nominal').
    Validates as a plain Fact; consumers that don't know `key` ignore it."""
    key: str = ""


# ---------------------------------------------------------------- helpers
def _d(obj) -> Any:
    if obj is None:
        return None
    if hasattr(obj, "model_dump"):
        return obj.model_dump(mode="json", by_alias=True)
    if isinstance(obj, dict):
        return {k: _d(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return type(obj)(_d(v) for v in obj)
    return obj


def _num(v) -> float | None:
    if v is None or isinstance(v, bool):
        return None
    try:
        f = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(f) or math.isinf(f) else f


def robot_name(spec: dict) -> str:
    name = " ".join(x for x in [spec.get("vendor") or "", spec.get("model") or ""] if x).strip()
    return name or spec.get("robot_id", "robot")


def _polygon_area(poly) -> float | None:
    if not poly or len(poly) < 3:
        return None
    a = 0.0
    for (x1, y1), (x2, y2) in zip(poly, list(poly[1:]) + [poly[0]]):
        a += x1 * y2 - x2 * y1
    return abs(a) / 2.0


class FactBuilder:
    def __init__(self):
        self.facts: list[ReportFact] = []
        self.by_key: dict[str, ReportFact] = {}
        self.ids: set[str] = set()

    def add(self, key: str, value, unit: str, label: str, otype: str, ref: str,
            fact_id: str | None = None) -> ReportFact | None:
        if key in self.by_key:
            return self.by_key[key]
        if value is None:
            return None  # unknowns are stated in words by the writer, never as a number
        if isinstance(value, bool):
            value = "yes" if value else "no"
        if not isinstance(value, str):
            value = _num(value)
            if value is None:
                return None
        fid = fact_id if fact_id and fact_id not in self.ids else self._next_id(fact_id)
        f = ReportFact(fact_id=fid, value=value, unit=unit, label=label,
                       origin=FactOrigin(type=otype, ref=str(ref)), key=key)
        self.facts.append(f)
        self.by_key[key] = f
        self.ids.add(fid)
        return f

    def _next_id(self, wanted: str | None) -> str:
        if wanted:  # an investigation id that collides with a core id
            n = 1
            while f"f_x{n:03d}" in self.ids:
                n += 1
            return f"f_x{n:03d}"
        n = len([i for i in self.ids if re.fullmatch(r"f_\d+", i)]) + 1
        while f"f_{n:03d}" in self.ids:
            n += 1
        return f"f_{n:03d}"

    def add_text_numbers(self, key_prefix: str, text: str, label: str, otype: str, ref: str) -> list[ReportFact]:
        """One fact per number printed in a code-generated sentence (verdict reasons, rule texts), so the
        writer can tag them. Token k of `text` gets key f'{key_prefix}|{k}' (see tag_text_numbers)."""
        out = []
        for k, tok in enumerate(tokenize_numbers(mask_exempt(text)[0])):
            value, unit = token_value(tok)
            f = self.add(f"{key_prefix}|{k}", value, unit, f"{label} (value {k + 1})", otype, ref)
            if f is not None:
                out.append(f)
        return out


def token_value(tok) -> tuple[float, str]:
    if tok.pct:
        return tok.value / 100.0, "ratio"
    if tok.currency:
        return tok.value, tok.currency
    if tok.unit:
        dim, fac = UNITS[tok.unit]
        base = {"length": "m", "time": "s", "speed": "m/s", "area": "m2", "angle": "deg", "mass": "kg",
                "month": "months"}.get(dim)
        if base:
            return tok.value * fac / UNITS[base][1] if base in UNITS else tok.value * fac, base
        return tok.value, tok.unit
    return tok.value, ""


def tag_text_numbers(text: str, by_key: dict, key_prefix: str) -> str:
    """Insert ' [f_xxx]' after each number of `text` using facts made by FactBuilder.add_text_numbers."""
    toks = tokenize_numbers(mask_exempt(text)[0])
    out, last = [], 0
    for k, tok in enumerate(toks):
        f = by_key.get(f"{key_prefix}|{k}")
        out.append(text[last:tok.end])
        if f is not None:
            out.append(f" [{_fid(f)}]")
        last = tok.end
    out.append(text[last:])
    return "".join(out)


def _fid(f) -> str:
    return f["fact_id"] if isinstance(f, dict) else f.fact_id


def _load(key: str):
    try:
        from clearance_core import storage
        return storage.read_json(key)
    except Exception:
        return None


def business_inputs(analysis: dict) -> dict:
    b = analysis.get("business")
    if b is None:
        b = (analysis.get("inputs") or {}).get("business")
    return dict(_d(b) or {})


def candidates(analysis: dict) -> list[dict]:
    robots = {k: _d(v) for k, v in (analysis.get("robots") or {}).items()}
    out, seen = [], set()
    for c in analysis.get("candidates") or []:
        c = _d(c)
        spec = robots.get(c, {"robot_id": c}) if isinstance(c, str) else dict(c)
        if spec.get("robot_id") and spec["robot_id"] not in seen:
            seen.add(spec["robot_id"])
            out.append(spec)
    for rid in sorted(robots):
        if rid not in seen:
            seen.add(rid)
            out.append(dict(robots[rid], robot_id=rid))
    return out


def batches(analysis: dict) -> list[dict]:
    out = []
    for b in analysis.get("batches") or []:
        b = _d(b)
        if isinstance(b, str) and analysis.get("analysis_id"):
            b = _load(f"analyses/{analysis['analysis_id']}/batches/{b}.json")
        if isinstance(b, dict) and b.get("robot_id"):
            out.append(b)
    return out


def feasibility_map(analysis: dict) -> dict[str, dict]:
    f = analysis.get("feasibility") or {}
    if isinstance(f, dict) and "legs" in f and "robot_id" in f:
        f = [f]
    if isinstance(f, dict):
        return {k: _d(v) for k, v in f.items()}
    return {_d(x)["robot_id"]: _d(x) for x in f}


def optimizer_map(analysis: dict, robot_ids: list[str]) -> dict[str, list[dict]]:
    o = analysis.get("optimizer") or {}
    if isinstance(o, dict) and isinstance(o.get("results"), list):
        o = o["results"]
    if isinstance(o, dict):
        return {k: [_d(x) for x in (v or [])] for k, v in o.items()}
    out: dict[str, list[dict]] = {}
    for r in o:
        r = _d(r)
        out.setdefault(r.get("robot_id") or (robot_ids[0] if robot_ids else "robot"), []).append(r)
    return out


def investigations(analysis: dict) -> list[dict]:
    invs = analysis.get("investigations")
    if invs is None:
        inv = analysis.get("investigation")
        if inv is None and analysis.get("investigation_id") and analysis.get("analysis_id"):
            inv = _load(f"analyses/{analysis['analysis_id']}/investigation.json")
        invs = [inv] if inv is not None else []
    elif isinstance(invs, dict) and "investigation_id" not in invs:
        invs = list(invs.values())
    return [_d(i) for i in invs if i is not None]


def reference_density(densities: Iterable[str]) -> str | None:
    ds = set(densities)
    for d in ["normal", "quiet", "peak", "none"]:
        if d in ds:
            return d
    return None


def compass(dx: float, dy: float) -> str:
    if abs(dx) < 1e-9 and abs(dy) < 1e-9:
        return ""
    ang = math.degrees(math.atan2(dy, dx)) % 360
    names = ["east", "north-east", "north", "north-west", "west", "south-west", "south", "south-east"]
    return names[int((ang + 22.5) // 45) % 8]


def _src(spec: dict, field: str, rid: str) -> str:
    fs = _d((spec.get("fields_sources") or {}).get(field)) or {}
    return fs.get("url") or f"catalog:{rid}"


# ---------------------------------------------------------------- verdicts (decided by code only)
def derive_verdicts(analysis: dict) -> list[Verdict]:
    """Verdicts from clearance_core.verdict.decide_verdict, used when the analysis carries none."""
    from clearance_core.verdict import VerdictInputs, decide_verdict

    analysis = _d(analysis) or {}
    twin = _d(analysis.get("twin")) or {}
    flagged_all = [t.get("id") for t in twin.get("thresholds") or [] if t.get("status") == "flagged_manual"]
    feas = feasibility_map(analysis)
    cands = candidates(analysis)
    bts = batches(analysis)
    opt = optimizer_map(analysis, [c["robot_id"] for c in cands])
    out = []
    for spec in cands:
        rid = spec["robot_id"]
        legs = [LegFeasibility.model_validate(l) for l in (feas.get(rid) or {}).get("legs") or []]
        on_route = sorted({l.blocking_threshold for l in legs if l.reachable is None and l.blocking_threshold})
        flagged = on_route or (flagged_all if any(l.reachable is None for l in legs) else [])
        sr = next((_num((b.get("results") or {}).get("success_rate")) for b in bts
                   if b["robot_id"] == rid and b.get("density") == "normal"
                   and b.get("geometry_bound") == "conservative" and b.get("results")), None)
        as_is = VerdictInputs(legs=legs, flagged_thresholds=flagged, success_rate=sr)
        best = None
        for r in opt.get(rid, []):
            a = _num((r.get("after") or {}).get("success_rate"))
            if a is not None and (best is None or a > best[0]):
                best = (a, r)
        with_changes = None
        if best is not None:
            with_changes = VerdictInputs(legs=legs, flagged_thresholds=flagged, success_rate=best[0],
                                         changes=[best[1].get("change") or {}])
        out.append(decide_verdict(rid, as_is, with_changes))
    return out


# ---------------------------------------------------------------- main
def build_facts(analysis: dict) -> list[Fact]:
    analysis = _d(analysis) or {}
    fb = FactBuilder()
    twin = _d(analysis.get("twin")) or {}
    twin_id = twin.get("twin_id", "twin")
    biz = business_inputs(analysis)
    cur = str(biz.get("currency") or "USD").strip().upper()

    _site_facts(fb, analysis, twin, twin_id)
    _business_facts(fb, biz, cur)

    cands = candidates(analysis)
    bts = batches(analysis)
    known = {c["robot_id"] for c in cands}
    for b in bts:
        if b["robot_id"] not in known:
            known.add(b["robot_id"])
            cands.append({"robot_id": b["robot_id"]})
    feas = feasibility_map(analysis)
    opt = optimizer_map(analysis, [c["robot_id"] for c in cands])
    roi_given = {k: _d(v) for k, v in (analysis.get("roi") or {}).items()}
    by_robot: dict[str, dict[tuple[str, str], dict]] = {}
    for b in bts:
        by_robot.setdefault(b["robot_id"], {})[(b.get("density"), b.get("geometry_bound"))] = b
    verdicts = analysis.get("verdicts")
    verdicts = {v["robot_id"]: v for v in (_d(x) for x in verdicts)} if verdicts is not None else \
        {v.robot_id: _d(v) for v in derive_verdicts(analysis)}

    for spec in cands:
        rid = spec["robot_id"]
        name = robot_name(spec)
        _robot_spec_facts(fb, spec, rid, name)
        _feasibility_facts(fb, feas.get(rid), rid, name, twin_id)
        ref_d = _sim_facts(fb, by_robot.get(rid, {}), rid, name)
        _optimizer_facts(fb, opt.get(rid, []), rid, name)
        _roi_facts(fb, spec, rid, name, biz, cur, ref_d, roi_given.get(rid), single=len(cands) == 1)
        if rid in verdicts:
            _verdict_facts(fb, verdicts[rid], rid, name)

    _investigation_facts(fb, analysis, {c["robot_id"]: robot_name(c) for c in cands})
    _clear_zone_facts(fb, analysis, twin, twin_id)  # optional; appended late so core ids stay stable
    site = analysis.get("site")
    site_name = site.get("name") if isinstance(site, dict) else None
    if isinstance(site_name, str) and site_name.strip():  # appended last so core ids stay stable
        fb.add("site.name", site_name.strip(), "", "Site name", "user_input", f"site:{site.get('site_id', '')}")
    return fb.facts


CLEAR_NOTE_RE = re.compile(r"operator-confirmed clear floor:\s*(\d+)\s+zones?,\s*([\d.]+)\s*m²\s*total"
                           r"(?:,\s*confirmed by\s+(.+?))?(?:;|$)")


def clear_zone_summary(analysis: dict, twin: dict) -> dict | None:
    """{"count", "area_m2", "confirmed_by"} of the operator-confirmed clear floor zones (Twin.clear_zones), else
    parsed from the orchestrator note "operator-confirmed clear floor: N zones, A m² total, confirmed by NAME";
    None when there are none (optional field)."""
    zones = [_d(z) for z in twin.get("clear_zones") or []]
    zones = [z for z in zones if len(z.get("polygon") or []) >= 3]
    if zones:
        who = sorted({z["confirmed_by"] for z in zones if z.get("confirmed_by")})
        return {"count": len(zones), "area_m2": round(sum(_polygon_area(z["polygon"]) or 0.0 for z in zones), 3),
                "confirmed_by": ", ".join(who) or None}
    for note in analysis.get("notes") or []:
        m = CLEAR_NOTE_RE.search(str(note))
        if m:
            who = (m.group(3) or "").strip()
            return {"count": int(m.group(1)), "area_m2": float(m.group(2)),
                    "confirmed_by": None if who in ("", "the operator") else who}
    return None


def _clear_zone_facts(fb: FactBuilder, analysis: dict, twin: dict, twin_id: str):
    cz = clear_zone_summary(analysis, twin)
    if not cz or not cz["count"]:
        return
    ref = f"{twin_id}:clear_zones"
    fb.add("site.clear_zones_n", cz["count"], "count", "Unseen floor patches confirmed clear by the operator",
           "user_input", ref)
    fb.add("site.clear_zones_area_m2", cz["area_m2"], "m2", "Area of the floor patches confirmed clear",
           "user_input", ref)
    if cz.get("confirmed_by"):
        fb.add("site.clear_zones_by", cz["confirmed_by"], "", "Clear floor patches confirmed by", "user_input", ref)


def _site_facts(fb: FactBuilder, analysis: dict, twin: dict, twin_id: str):
    acc = twin.get("accuracy") or {}
    if acc.get("method"):
        method = {"tape_check": "tape check on site", "estimated": "estimated from the scan",
                  "reference_ground_truth": "reference geometry (known ground truth)"}.get(acc["method"], acc["method"])
        fb.add("site.accuracy_method", method, "", "Scan accuracy method", "measurement", f"{twin_id}:accuracy")
    fb.add("site.median_err_m", acc.get("median_abs_err_m"), "m", "Scan median absolute error",
           "measurement", f"{twin_id}:accuracy")
    fb.add("site.p90_err_m", acc.get("p90_abs_err_m"), "m",
           "Scan p90 absolute error (sets the conservative/optimistic geometry bounds)",
           "measurement", f"{twin_id}:accuracy")
    for i, m in enumerate(analysis.get("measurements") or []):
        m = _d(m)
        name = m.get("name", f"measurement {i + 1}")
        ref = m.get("ref") or f"{twin_id}:measurement:{name}"
        tape = fb.add(f"site.measurement|{name}", m.get("value_m"), "m", f"On-site measurement: {name}",
                      "measurement", ref)
        tw = fb.add(f"site.measurement_twin|{name}", m.get("twin_value_m"), "m", f"Twin distance: {name}",
                    "measurement", ref)
        if tape is not None and tw is not None:
            fb.add(f"site.measurement_err|{name}", abs(tw.value - tape.value), "m",
                   f"Twin vs on-site difference: {name}", "formula", f"{ref}:abs_diff")
    area = _polygon_area((twin.get("floor") or {}).get("bounds_polygon"))
    fb.add("site.floor_area_m2", area, "m2", "Floor area inside the scanned bounds", "measurement", f"{twin_id}:floor")
    if twin:
        src = twin.get("geometry_source", "scan")
        fb.add("site.geometry_source", "reference site (known ground-truth geometry)" if src == "reference"
               else "scanned site", "", "Geometry source", "measurement", f"{twin_id}:meta")
    traffic = twin.get("traffic") or {}
    if traffic:
        src = traffic.get("source", "user_estimated")
        otype = "measurement" if src == "observed" else "user_input"
        fb.add("site.traffic_source", "observed" if src == "observed" else "user-estimated", "",
               "Pedestrian traffic source", otype, f"{twin_id}:traffic")
        ppm = traffic.get("persons_per_min") or {}
        for dname in DENSITY_ORDER:
            if dname in ppm:
                fb.add(f"site.ppm|{dname}", ppm[dname], "persons/min",
                       f"Pedestrian arrivals per minute, {dname} traffic", otype, f"{twin_id}:traffic:{dname}")
    for th in twin.get("thresholds") or []:
        tid = th.get("id", "?")
        if th.get("status") == "flagged_manual" or th.get("height_m") is None:
            fb.add(f"site.threshold_status|{tid}", "measure on site", "", f"Threshold {tid} height",
                   "measurement", f"{twin_id}:threshold:{tid}")
        else:
            fb.add(f"site.threshold|{tid}", th["height_m"], "m",
                   f"Threshold {tid} height ({th.get('status', 'measured').replace('_', ' ')})",
                   "user_input" if th.get("status") == "user_entered" else "measurement",
                   f"{twin_id}:threshold:{tid}")
    if twin:
        hazards = twin.get("hazards") or []
        fb.add("site.hazard_count", len(hazards), "count", "Hazards tagged in the twin", "measurement",
               f"{twin_id}:hazards")
        for i, h in enumerate(hazards):
            h = _d(h)
            fb.add(f"site.hazard|{i}", h.get("label", "hazard"), "", f"Hazard {i + 1} ({h.get('source', 'vision')})",
                   "measurement", f"{twin_id}:hazard:{i}")
            fb.add(f"site.hazard_conf|{i}", h.get("confidence"), "ratio", f"Hazard {i + 1} detection confidence",
                   "measurement", f"{twin_id}:hazard:{i}")


def _business_facts(fb: FactBuilder, biz: dict, cur: str):
    for key, unit, label in BUSINESS_FIELDS:
        v = next((biz[a] for a in ALIASES.get(key, (key,)) if biz.get(a) is not None), biz.get(key))
        fb.add(f"biz.{key}", v, unit.format(cur=cur), label, "user_input", f"business.{key}")
    if biz:
        maint = biz.get("maintenance_pct_per_year")
        fb.add("biz.maintenance_pct_per_year", DEFAULT_MAINTENANCE_PCT_PER_YEAR if maint is None else maint,
               "percent", "Maintenance per year as % of price" + (" (default, editable)" if maint is None else ""),
               "user_input", "business.maintenance_pct_per_year" + (":default" if maint is None else ""))


def _robot_spec_facts(fb: FactBuilder, spec: dict, rid: str, name: str):
    fb.add(f"robot.name|{rid}", name, "", f"Robot model ({rid})", "source_url", _src(spec, "model", rid))
    if spec.get("class"):
        fb.add(f"robot.class|{rid}", "service robot" if spec["class"] == "A_service" else "indoor AMR", "",
               f"Robot class, {name}", "source_url", f"catalog:{rid}")
    poly = spec.get("footprint_polygon_m")
    if poly and len(poly) >= 3:
        xs, ys = [p[0] for p in poly], [p[1] for p in poly]
        approx = " (bounding box)" if spec.get("footprint_approx") else ""
        src = _src(spec, "footprint_polygon_m", rid)
        fb.add(f"robot.length_m|{rid}", max(xs) - min(xs), "m", f"Footprint length, {name}{approx}", "source_url", src)
        fb.add(f"robot.width_m|{rid}", max(ys) - min(ys), "m", f"Footprint width, {name}{approx}", "source_url", src)
    for field, suffix, unit, label in SPEC_FIELDS:
        fb.add(f"robot.{suffix}|{rid}", spec.get(field), unit, f"{label[0].upper()}{label[1:]}, {name}",
               "source_url", _src(spec, field, rid))
    price = spec.get("price") or {}
    if price.get("value") is not None and price.get("is_public"):
        fb.add(f"robot.price|{rid}", price["value"], price.get("currency", "USD"), f"Public list price, {name}",
               "source_url", price.get("source_url") or _src(spec, "price", rid))
    else:
        fb.add(f"robot.price_status|{rid}", PRICE_ON_REQUEST, "", f"Price, {name}", "source_url",
               price.get("source_url") or f"catalog:{rid}")
    fb.add(f"robot.verified|{rid}", "human-reviewed" if spec.get("verified_by_human") else "awaiting review",
           "", f"Catalog review status, {name}", "source_url", f"catalog:{rid}")


def _feasibility_facts(fb: FactBuilder, f: dict | None, rid: str, name: str, twin_id: str):
    if not f:
        return
    legs = f.get("legs") or []
    ref = f"feasibility:{rid}:{f.get('twin_id', twin_id)}:v{f.get('twin_version', '')}"
    n_ok = sum(1 for l in legs if l.get("reachable") is True)
    n_unk = sum(1 for l in legs if l.get("reachable") is None)
    fb.add(f"feas.legs_total|{rid}", len(legs), "count", f"Task legs, {name}", "measurement", ref)
    fb.add(f"feas.legs_ok|{rid}", n_ok, "count", f"Task legs feasible (static check), {name}", "measurement", ref)
    if n_unk:
        fb.add(f"feas.legs_unknown|{rid}", n_unk, "count", f"Task legs pending an on-site measurement, {name}",
               "measurement", ref)
    fb.add(f"feas.frac|{rid}", n_ok / len(legs) if legs else 0.0, "ratio",
           f"Fraction of task legs feasible, {name} (pending legs count as not feasible)", "formula",
           f"{ref}:feasible/total")
    gaps = [(_num(l.get("narrowest_gap_m")), i) for i, l in enumerate(legs)]
    gaps = [(g, i) for g, i in gaps if g is not None]
    if gaps:
        g, i = min(gaps)
        fb.add(f"feas.narrowest_gap_m|{rid}", g, "m", f"Narrowest gap on route, {name}", "measurement", f"{ref}:leg{i}")
        fb.add(f"feas.narrowest_required_m|{rid}", legs[i].get("required_gap_m"), "m",
               f"Gap the robot needs at the narrowest point, {name}", "measurement", f"{ref}:leg{i}")
    for i, l in enumerate(legs):
        leg = f"{l.get('from_wp')} to {l.get('to_wp')}"
        status = ("feasible" if l.get("reachable") is True else
                  "measure on site" if l.get("reachable") is None else "blocked")
        fb.add(f"feas.leg_status|{rid}|{i}", status, "", f"Leg {leg}, {name}", "measurement", f"{ref}:leg{i}")
        fb.add(f"feas.leg_gap|{rid}|{i}", l.get("narrowest_gap_m"), "m", f"Narrowest gap, leg {leg}, {name}",
               "measurement", f"{ref}:leg{i}")
        fb.add(f"feas.leg_required|{rid}|{i}", l.get("required_gap_m"), "m", f"Gap needed, leg {leg}, {name}",
               "measurement", f"{ref}:leg{i}")
        fb.add(f"feas.leg_len|{rid}|{i}", l.get("path_length_m"), "m", f"Path length, leg {leg}, {name}",
               "measurement", f"{ref}:leg{i}")
        blocker = leg_blocker(l.get("reason") or "") if l.get("reachable") is False else ""
        if blocker:
            fb.add(f"feas.leg_blocker|{rid}|{i}", blocker, "", f"Blocking reason, leg {leg}, {name}",
                   "measurement", f"{ref}:leg{i}")
            fb.add_text_numbers(f"feas.leg_blocker_num|{rid}|{i}", blocker, f"Blocking reason, leg {leg}, {name}",
                                "measurement", f"{ref}:leg{i}")


def _sim_facts(fb: FactBuilder, bts: dict, rid: str, name: str) -> str | None:
    dens = sorted({d for d, _ in bts}, key=lambda d: DENSITY_ORDER.index(d) if d in DENSITY_ORDER else 99)
    for d in dens:
        for bound in BOUND_ORDER:
            b = bts.get((d, bound))
            res = (b or {}).get("results")
            if not res:
                continue
            bid = b["batch_id"]
            sfx = f"{name}, {d} traffic, {bound} geometry (simulation-based estimate)"
            fb.add(f"sim.success|{rid}|{d}|{bound}", res.get("success_rate"), "ratio", f"Success rate, {sfx}",
                   "sim_batch", bid)
            fb.add(f"sim.n|{rid}|{d}|{bound}", res.get("n_episodes") or b.get("n_episodes"), "episodes",
                   f"Episodes simulated, {sfx}", "sim_batch", bid)
            if bound != "nominal":
                continue
            ci = res.get("ci95") or [None, None]
            fb.add(f"sim.ci_lo|{rid}|{d}", ci[0], "ratio", f"Success rate CI95 low, {sfx}", "sim_batch", bid)
            fb.add(f"sim.ci_hi|{rid}|{d}", ci[1], "ratio", f"Success rate CI95 high, {sfx}", "sim_batch", bid)
            fb.add(f"sim.mission_s|{rid}|{d}", res.get("median_mission_s"), "s", f"Median mission time, {sfx}",
                   "sim_batch", bid)
            fb.add(f"sim.near_miss|{rid}|{d}", res.get("near_miss_per_mission"), "per_mission",
                   f"Near-misses per mission, {sfx}", "sim_batch", bid)
            fb.add(f"sim.stops|{rid}|{d}", res.get("stops_per_mission"), "per_mission",
                   f"Safety stops per mission, {sfx}", "sim_batch", bid)
            fb.add(f"sim.min_gap_m|{rid}|{d}", res.get("min_gap_m"), "m",
                   f"Median smallest robot-to-obstacle gap per episode, {sfx}", "sim_batch", bid)
            for reason, cnt in sorted((res.get("fail_reasons") or {}).items()):
                if cnt:
                    fb.add(f"sim.fail|{rid}|{d}|{reason}", cnt, "episodes",
                           f"Unsuccessful episodes ({reason.replace('_', ' ')}), {sfx}", "sim_batch", bid)
    return reference_density(d for d, b in bts if b == "nominal")


def _optimizer_facts(fb: FactBuilder, results: list[dict], rid: str, name: str):
    for i, r in enumerate(results):
        before, after = r.get("before") or {}, r.get("after") or {}
        bids = r.get("batch_ids") or []
        ref_after = bids[-1] if bids else f"optimizer:{rid}:{i}"
        ref_before = bids[0] if len(bids) > 1 else ref_after
        change = r.get("change") or {}
        fb.add(f"opt.desc|{rid}|{i}", r.get("description") or "layout change", "",
               f"Layout change {i + 1}, {name}", "sim_batch", ref_after)
        for oid, mv in sorted((change.get("move") or {}).items()):
            dx, dy = float(mv.get("dx", 0) or 0), float(mv.get("dy", 0) or 0)
            dist = math.hypot(dx, dy)
            if dist > 0:
                fb.add(f"opt.move_m|{rid}|{i}|{oid}", dist, "m", f"Move distance for {oid}, change {i + 1}, {name}",
                       "sim_batch", ref_after)
                fb.add(f"opt.dir|{rid}|{i}|{oid}", compass(dx, dy), "", f"Move direction for {oid}, change {i + 1}, {name}",
                       "sim_batch", ref_after)
            if mv.get("dyaw"):
                fb.add(f"opt.rot_deg|{rid}|{i}|{oid}", abs(math.degrees(float(mv["dyaw"]))), "deg",
                       f"Rotation for {oid}, change {i + 1}, {name}", "sim_batch", ref_after)
        for oid in change.get("remove") or []:
            fb.add(f"opt.remove|{rid}|{i}|{oid}", oid, "", f"Object removed, change {i + 1}, {name}", "sim_batch",
                   ref_after)
        if change.get("add_boxes"):
            fb.add(f"opt.add_n|{rid}|{i}", len(change["add_boxes"]), "count", f"Boxes added, change {i + 1}, {name}",
                   "sim_batch", ref_after)
        fb.add(f"opt.before|{rid}|{i}", before.get("success_rate"), "ratio",
               f"Success before change {i + 1}, {name} (simulation-based estimate)", "sim_batch", ref_before)
        fb.add(f"opt.after|{rid}|{i}", after.get("success_rate"), "ratio",
               f"Success after change {i + 1}, {name} (simulation-based estimate)", "sim_batch", ref_after)
        fb.add(f"opt.n_after|{rid}|{i}", after.get("n_episodes"), "episodes",
               f"Episodes behind the after-change estimate, change {i + 1}, {name}", "sim_batch", ref_after)


def _roi_facts(fb, spec, rid, name, biz, cur, ref_d, given, single=False):
    roi = given
    price_input = None
    quotes = biz.get("quotes") or biz.get("prices") or {}
    leases = biz.get("leases") or {}
    if quotes.get(rid) is not None:
        price_input = ("quote", quotes[rid])
    elif single and biz.get("price") is not None:  # one candidate: a bare business price is its quote
        price_input = ("quote", biz["price"])
    elif (spec.get("price") or {}).get("value") is not None and (spec.get("price") or {}).get("is_public"):
        if (spec["price"].get("currency") or "USD") == cur:
            price_input = ("public", spec["price"]["value"])
    if roi is None:
        sr_f = fb.by_key.get(f"sim.success|{rid}|{ref_d}|nominal") if ref_d else None
        fr_f = fb.by_key.get(f"feas.frac|{rid}")
        if not biz or sr_f is None:
            return
        inputs = {k: v for k, v in biz.items() if k not in ("quotes", "prices", "leases", "price")}
        if price_input:
            inputs["price"] = price_input[1]
        if leases.get(rid) is not None:
            inputs["lease_per_month"] = leases[rid]
        elif biz.get("lease_per_month") is not None and single:
            inputs["lease_per_month"] = biz["lease_per_month"]
        try:
            roi = compute_roi(inputs, sr_f.value, fr_f.value if fr_f is not None else 1.0)
        except ValueError as e:  # explained in words: the status text carries no numbers
            field = getattr(e, "field", None) if isinstance(e, ROIInputError) else None
            text = (f"payback needs a valid {field.replace('_', ' ')} input" if field and field != "inputs"
                    else "payback needs valid business inputs")
            fb.add(f"roi.status|{rid}", text, "", f"Payback status, {name}", "formula", f"roi:{rid}")
            return
    if price_input:
        quote = price_input[0] == "quote"
        fb.add(f"roi.price|{rid}", price_input[1], cur,
               f"Robot price ({'user-entered quote' if quote else 'public list price'}), {name}",
               "user_input" if quote else "source_url",
               f"business.quotes.{rid}" if quote else ((spec.get("price") or {}).get("source_url") or f"catalog:{rid}"))
    lease = (roi.get("inputs") or {}).get("lease_per_month")
    if lease is not None:
        fb.add(f"roi.lease|{rid}", lease, f"{cur}/month", f"Monthly lease (user input), {name}", "user_input",
               f"business.leases.{rid}")
    ref = f"roi:{rid}"
    fb.add(f"roi.share|{rid}", roi.get("automatable_share"), "ratio",
           f"Automatable share of the task, {name} (success rate x feasible leg fraction)", "formula", ref)
    fb.add(f"roi.saving|{rid}", roi.get("monthly_saving"), f"{cur}/month", f"Monthly staff-time saving, {name}",
           "formula", ref)
    fb.add(f"roi.maint|{rid}", roi.get("monthly_maintenance"), f"{cur}/month", f"Monthly maintenance, {name}",
           "formula", ref)
    fb.add(f"roi.running|{rid}", roi.get("monthly_running_cost"), f"{cur}/month", f"Monthly running costs, {name}",
           "formula", ref)
    fb.add(f"roi.net|{rid}", roi.get("net_monthly_saving"), f"{cur}/month", f"Net monthly saving, {name}",
           "formula", ref)
    if roi.get("payback_months") is not None:
        fb.add(f"roi.payback|{rid}", roi["payback_months"], "months", f"Payback period, {name}", "formula", ref)
    status = roi.get("status", "ok")
    text = {"enter_quote": "enter a quote", "no_payback": "no payback", "lease": "leased (no upfront price)",
            "ok": "payback computed"}.get(status, status)
    fb.add(f"roi.status|{rid}", text, "", f"Payback status, {name}", "formula", ref)
    if roi.get("days_per_month") is not None:
        fb.add("biz.days_per_month", roi["days_per_month"], "days/month",
               "Operating days per month (days per week x 52 / 12)", "formula", "roi:days_per_month")


def _verdict_facts(fb: FactBuilder, v: dict, rid: str, name: str):
    ref = f"verdict:{rid}"
    fb.add(f"verdict|{rid}", v["label"], "", f"Verdict, {name}", "formula", ref)
    fb.add(f"verdict.reason|{rid}", v.get("main_reason", ""), "", f"Verdict main reason, {name}", "formula", ref)
    fb.add_text_numbers(f"verdict.reason_num|{rid}", v.get("main_reason", ""), f"Verdict main reason, {name}",
                        "formula", ref)
    for phase in ("as_is", "with_changes"):
        for i, c in enumerate(v.get(phase) or []):
            c = _d(c)
            k = f"{rid}|{phase}|{i}"
            fb.add_text_numbers(f"verdict.rule_num|{k}", c.get("rule", ""), f"Verdict rule, {name}", "formula",
                                f"{ref}:{phase}:{i}")
            val = c.get("value")
            if isinstance(val, str):
                fb.add_text_numbers(f"verdict.value_num|{k}", val, f"Verdict check value, {name}", "formula",
                                    f"{ref}:{phase}:{i}")
            elif val is not None:
                fb.add(f"verdict.value|{k}", val, "ratio" if c.get("threshold") is not None and abs(val) <= 1 else "",
                       f"Verdict check value ({phase.replace('_', '-')}), {name}", "formula", f"{ref}:{phase}:{i}")
            if c.get("threshold") is not None:
                fb.add(f"verdict.threshold|{k}", c["threshold"], "ratio" if abs(c["threshold"]) <= 1 else "",
                       f"Verdict rule threshold, {name}", "formula", f"{ref}:{phase}:{i}")


_PARAM_UNITS = {"dx": "m", "dy": "m", "ped_scale": "ratio", "height_m": "m", "scale": "ratio"}


def _flatten(d, prefix=""):
    if isinstance(d, dict):
        for k, v in sorted(d.items()):
            yield from _flatten(v, f"{prefix}.{k}" if prefix else str(k))
    elif isinstance(d, list):
        for i, v in enumerate(d):
            yield from _flatten(v, f"{prefix}[{i}]")
    else:
        yield prefix, d


def _investigation_facts(fb: FactBuilder, analysis: dict, names: dict[str, str]):
    invs = investigations(analysis)
    default_inv = invs[0].get("investigation_id", "inv") if len(invs) == 1 else None
    pending: list[tuple[str, dict]] = []
    top = analysis.get("investigation_facts") or []
    top = list(top.values()) if isinstance(top, dict) else list(top)
    for f in top:
        f = _d(f)
        inv_id = default_inv or (f.get("origin") or {}).get("ref", "inv").split("#")[0].split(":")[0]
        pending.append((inv_id, f))
    for inv in invs:
        fs = inv.get("facts") or []
        fs = list(fs.values()) if isinstance(fs, dict) else list(fs)
        pending += [(inv.get("investigation_id", "inv"), _d(f)) for f in fs]
    for inv_id, f in pending:
        if f.get("fact_id"):
            o = f.get("origin") or {}
            fb.add(f"inv|{inv_id}|{f['fact_id']}", f.get("value"), f.get("unit", ""), f.get("label", f["fact_id"]),
                   "investigation", o.get("ref") or inv_id, fact_id=f["fact_id"])
    for inv in invs:
        inv_id = inv.get("investigation_id", "inv")
        for e in inv.get("experiments") or []:
            e = _d(e)
            eid = e.get("experiment_id", "exp")
            name = names.get(e.get("robot_id"), e.get("robot_id", "robot"))
            for path, val in _flatten(e.get("change") or {}):
                if _num(val) is None:
                    continue
                leaf = path.split(".")[-1]
                unit = _PARAM_UNITS.get(leaf, "")
                value = float(val)
                if leaf == "dyaw":
                    value, unit = abs(math.degrees(value)), "deg"
                fb.add(f"exp.param|{inv_id}|{eid}|{path}", value, unit, f"Experiment {eid} setting {path}, {name}",
                       "investigation", f"{inv_id}:{eid}" + (f":{e['batch_id']}" if e.get("batch_id") else ""))


# ---------------------------------------------------------------- citations / findings / trail text
def build_citations(analysis: dict) -> list[dict]:
    """Unique sources in first-appearance order: catalog spec quotes, public prices, market-check results."""
    analysis = _d(analysis) or {}
    cites: dict[str, dict] = {}

    def add(url, title=None, retrieved_at=None, used_for=None, quote=None):
        if not url or not str(url).startswith(("http://", "https://")):
            return
        c = cites.setdefault(url, {"id": f"c_{len(cites) + 1:02d}", "url": url, "title": title or url,
                                   "retrieved_at": retrieved_at or "", "used_for": [], "quotes": []})
        if title and c["title"] == url:
            c["title"] = title
        if retrieved_at and not c["retrieved_at"]:
            c["retrieved_at"] = retrieved_at
        if used_for and used_for not in c["used_for"]:
            c["used_for"].append(used_for)
        if quote and quote not in c["quotes"]:
            c["quotes"].append(quote)

    for spec in candidates(analysis):
        name = robot_name(spec)
        for field, src in sorted((spec.get("fields_sources") or {}).items()):
            src = _d(src) or {}
            add(src.get("url"), None, src.get("retrieved_at"), f"{name}: {field}", src.get("quote"))
        add((spec.get("price") or {}).get("source_url"), None, None, f"{name}: price")
    for c in analysis.get("citations") or []:
        c = _d(c)
        add(c.get("url"), c.get("title"), c.get("retrieved_at"), c.get("used_for") or "market check")
    return list(cites.values())


TAG_IN_TEXT = re.compile(r"\[(f_[A-Za-z0-9_]+)\]")


def _key(f) -> str:
    return (f.get("key") if isinstance(f, dict) else getattr(f, "key", "")) or ""


def remap_tags(text: str, facts: list, inv_id: str | None) -> str:
    """Rewrite investigation-local [f_xxx] tags to report fact ids. Unknown tags become '[f_xxx?]',
    which the fact checker rejects."""
    ids, remap = set(), {}
    for f in facts:
        f = _d(f)
        ids.add(f["fact_id"])
        k = _key(f)
        if k.startswith("inv|"):
            _, fi, orig = k.split("|", 2)
            if inv_id is None or fi == inv_id:
                remap.setdefault(orig, f["fact_id"])
    return TAG_IN_TEXT.sub(lambda m: f"[{remap.get(m.group(1)) or (m.group(1) if m.group(1) in ids else m.group(1) + '?')}]",
                           text or "")


def remap_ids(fids: list, facts: list, inv_id: str | None) -> list[str]:
    out = []
    for fid in fids or []:
        t = remap_tags(f"[{fid}]", facts, inv_id)[1:-1]
        if not t.endswith("?"):
            out.append(t)
    return out


def collect_findings(analysis: dict, facts: list) -> list[dict]:
    out = []
    for inv in investigations(_d(analysis) or {}):
        inv_id = inv.get("investigation_id", "inv")
        for fd in inv.get("findings") or []:
            fd = dict(_d(fd))
            fd["claim"] = remap_tags(fd.get("claim", ""), facts, inv_id)
            fd["evidence_fact_ids"] = remap_ids(fd.get("evidence_fact_ids"), facts, inv_id)
            out.append(fd)
    return out


def facts_by_key(facts: list) -> dict[str, Any]:
    return {_key(f): f for f in facts if _key(f)}
