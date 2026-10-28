"""Field extraction from one page, then unit conversion + verbatim validation by code.

extract_raw(...)  -> the model-shaped dict (prompts.RESPONSE_SCHEMA): values in the page's own units.
  live:    Nemotron Nano via agents.llm.client.LLM.chat(role="nano", ...), imported lazily.
  offline: a deterministic rule-based reader over the same page text (same output shape).
verify_raw(...)   -> {spec_field: VerifiedField} plus a list of rejections with reasons.
"""
from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from agents.catalog_builder import units
from agents.catalog_builder.prompts import EXTRACT_FIELDS, build_messages
from agents.catalog_builder.validate import (check_field, check_footprint, check_price, dims_order, money_tokens,
                                             normalize_text)

MAX_QUOTE = 200

# Physical sanity ranges per field (lo, hi, lo_inclusive). Values outside are rejected: they almost
# always mean a unit slip or a number for something else (e.g. an animated "0 kg" counter on a page).
SANE_RANGE: dict[str, tuple[float, float, bool]] = {
    "max_speed_mps": (0.0, 5.0, False), "nominal_speed_mps": (0.0, 5.0, False),
    "payload_kg": (0.0, 3000.0, False), "runtime_h": (0.0, 72.0, False),
    "max_threshold_m": (0.0, 0.2, True), "max_slope_deg": (0.0, 30.0, True),
    "min_aisle_width_vendor_m": (0.0, 6.0, False), "safety_stop_distance_m": (0.0, 5.0, True),
    "min_turn_radius_m": (0.0, 10.0, True), "height_m": (0.0, 3.0, False),
    "length_m": (0.0, 5.0, False), "width_m": (0.0, 5.0, False),
}


def in_sane_range(field_name: str, v: float) -> bool:
    lo, hi, inclusive = SANE_RANGE.get(field_name, (float("-inf"), float("inf"), True))
    return (v >= lo if inclusive else v > lo) and v <= hi


def llm_mode() -> str:
    m = os.environ.get("CLEARANCE_LLM_MODE", "").strip().lower()
    if m in ("live", "offline"):
        return m
    return "live" if os.environ.get("NEBIUS_API_KEY") else "offline"


@dataclass
class VerifiedField:
    field: str
    value: object
    quote: str
    url: str
    retrieved_at: str
    raw: dict = field(default_factory=dict)


# ---------------------------------------------------------------- LLM path
def _response_text(resp) -> str:
    if resp is None:
        return ""
    if isinstance(resp, str):
        return resp
    if isinstance(resp, dict):
        if "choices" in resp:
            return resp["choices"][0]["message"]["content"] or ""
        return json.dumps(resp)
    ch = getattr(resp, "choices", None)
    if ch:
        return ch[0].message.content or ""
    content = getattr(resp, "content", None)
    return content if isinstance(content, str) else str(resp)


def parse_json_object(text: str) -> dict:
    text = (text or "").strip()
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text)
    try:
        obj = json.loads(text)
    except ValueError:
        i, j = text.find("{"), text.rfind("}")
        if i < 0 or j <= i:
            raise ValueError("no JSON object in model output")
        obj = json.loads(text[i:j + 1])
    if not isinstance(obj, dict):
        raise ValueError("model output is not a JSON object")
    return obj


def _default_llm():
    from agents.llm.client import LLM  # lazy: written by the llm module owner

    return LLM()


def extract_with_llm(page_text: str, vendor: str, model: str, url: str, llm=None) -> dict:
    llm = llm or _default_llm()
    resp = llm.chat("nano", build_messages(page_text, vendor, model, url),
                    response_format={"type": "json_object"}, temperature=0,
                    kind="catalog_extract", meta={"vendor": vendor, "model": model, "url": url})
    parsed = getattr(resp, "parsed", None)
    if isinstance(parsed, dict):
        return parsed
    return parse_json_object(_response_text(resp))


# ---------------------------------------------------------------- rule-based path (offline)
# (spec-field, label regex, exclusion regex, pick: "hi" | "lo" | "first")
_RULES = [
    ("max_speed", r"max(?:imum)?\.?\s*(?:travel\s+|moving\s+|driving\s+)?speed|moving speed|drive speed|cruise speed|velocity|\bspeed\b",
     r"turning speed|docking speed|rad/|charging", "hi"),
    ("payload", r"payload|load capacity|maximum loads?|max(?:imum)?\.?\s*(?:capacity|load)|total load",
     r"towing|tray load|per layer|charging pile|staging cart", "hi"),
    ("runtime", r"battery life|run-?time|running time|operation time|operating time|endurance",
     r"charging time|charge time|cycles", "lo"),
    ("max_threshold", r"threshold|surmountable|step tolerance|sill tolerance|floor obstacle|obstacle crossing",
     r"groove", "hi"),
    ("max_slope", r"slope|climbing angle|incline|gradient", r"", "hi"),
    ("min_aisle_width", r"passage width|aisle width|travel width|path width|pass width|corridor width|path clearance",
     r"two robots|u-turn|two way|90", "first"),
    ("safety_stop_distance", r"braking distance|stopping distance|stop distance", r"", "first"),
]
_DIM_LABEL = re.compile(r"dimensions?|machine size|robot size|overall size|\bsize\b", re.I)
_DIM_EXCLUDE = re.compile(r"tray|charging pile|charger|load surface|battery|screen|box|payload|packag|packing|shipping|"
                          r"carton|crate|pallet|lift", re.I)
_IN_PLACE = re.compile(r"turn(?:s|ing)? in place|rotat\w* in place|spin\w* in place|zero[- ]radius turn", re.I)
_ON_REQUEST = re.compile(r"contact sales|get a quote|request a quote|price on request|pricing on request|"
                         r"costs can vary[^.]*|contact us for pric\w*", re.I)


def _lines(page_text: str) -> list[str]:
    out = []
    for ln in page_text.splitlines():
        ln = ln.strip().strip("|").strip()
        if ln:
            out.append(ln)
    return out


def _windows(lines: list[str]):
    """(window_text, label_line): a short label line joined with up to 2 following lines, or a
    sentence from a longer line."""
    for i, ln in enumerate(lines):
        if len(ln) <= 60:
            for k in (1, 2, 3):
                if i + k <= len(lines):
                    w = " ".join(lines[i:i + k])
                    if len(w) <= MAX_QUOTE:
                        yield w, ln
        else:
            for sent in re.split(r"(?<=[.!?])\s+", ln):
                if len(sent) <= MAX_QUOTE:
                    yield sent, sent


def _pick(qs: list[units.Quantity], how: str) -> tuple[float, str]:
    q = qs[0]
    if how == "hi":
        return max(q.values), q.unit
    if how == "lo":
        return min(q.values), q.unit
    return q.values[0], q.unit


def extract_rule_based(page_text: str, vendor: str = "", model: str = "", url: str = "") -> dict:
    lines = _lines(page_text)
    fields: dict[str, dict | None] = {k: None for k in EXTRACT_FIELDS}
    for key, label, excl, how in _RULES:
        dim = units.FIELD_DIM[EXTRACT_FIELDS[key]]
        lab, ex = re.compile(label, re.I), re.compile(excl, re.I) if excl else None
        for w, head in _windows(lines):
            m = lab.search(head)
            if not m or (ex and ex.search(w)):
                continue
            nw = normalize_text(w)
            anchor = 0
            mt = re.search(r"\btotal\b", nw, re.I)
            if mt:
                anchor = mt.start()
            qs = [q for q in units.parse_quantities(nw) if q.start >= anchor and q.kind != "dims"
                  and (q.dim == dim or (dim == "angle" and q.dim == "grade"))]
            if not qs:
                continue
            v, u = _pick(qs, how)
            fields[key] = {"value": v, "unit": u, "quote": w}
            break
    dims = None
    for w, head in _windows(lines):
        if not _DIM_LABEL.search(head) or _DIM_EXCLUDE.search(w):
            continue
        nw = normalize_text(w)
        dq = [q for q in units.parse_quantities(nw) if q.kind == "dims" and q.dim == "length" and len(q.values) >= 2]
        if not dq:
            continue
        q = dq[0]
        order = dims_order(nw[:q.start]) or ["length", "width", "height"]
        if len(order) < len(q.values):
            order = order + [a for a in ("length", "width", "height") if a not in order]
        pos = {a: i for i, a in enumerate(order)}
        height = q.values[pos["height"]] if len(q.values) > 2 and pos.get("height", 9) < len(q.values) else None
        dims = {"length": q.values[pos["length"]], "width": q.values[pos["width"]], "height": height,
                "unit": q.unit, "quote": w, "order_on_page": "x".join(a[0].upper() for a in order[:len(q.values)])}
        break
    in_place = None
    for w, _ in _windows(lines):
        if _IN_PLACE.search(w):
            in_place = {"value": True, "quote": w}
            break
    price = None
    for w, _ in _windows(lines):
        if not re.search(r"pric|cost|msrp|buy|leas|rent|month|subscri", w, re.I):
            continue
        toks = [t for t in money_tokens(normalize_text(w)) if t["kind"] != "other"]
        if toks:  # a purchase price wins over a lease in the same window; code re-classifies in verify_raw
            t = next((t for t in toks if t["kind"] in ("purchase", "from")), toks[0])
            price = {"value": t["value"], "currency": t["currency"], "quote": w, "on_request_quote": None}
            break
    if price is None:
        for w, _ in _windows(lines):
            m = _ON_REQUEST.search(w)
            if m:
                price = {"value": None, "currency": "USD", "quote": None, "on_request_quote": m.group(0)}
                break
    return {"fields": fields, "dimensions": dims, "in_place_rotation": in_place, "price": price,
            "_extractor": "rule_based"}


def extract_raw(page_text: str, vendor: str, model: str, url: str, *, llm=None, mode: str | None = None) -> dict:
    mode = mode or llm_mode()
    if mode == "live" or llm is not None:
        try:
            out = extract_with_llm(page_text, vendor, model, url, llm=llm)
            out.setdefault("_extractor", "llm_nano")
            return out
        except Exception as e:  # keep going with the deterministic reader; the reason is recorded
            out = extract_rule_based(page_text, vendor, model, url)
            out["_extractor"] = f"rule_based (llm error: {type(e).__name__}: {str(e)[:120]})"
            return out
    return extract_rule_based(page_text, vendor, model, url)


# ---------------------------------------------------------------- conversion + validation
def _num(x):
    if isinstance(x, bool) or x is None:
        return None
    try:
        return float(x)
    except (TypeError, ValueError):
        return None


def _round(v: float) -> float:
    return float(f"{v:.6g}")


def verify_raw(raw: dict, page_text: str, url: str, retrieved_at: str):
    """Convert units by code and validate every quote. Returns (verified, rejected, price_info)."""
    verified: dict[str, VerifiedField] = {}
    rejected: list[dict] = []

    def reject(f, why, item):
        rejected.append({"field": f, "url": url, "reason": why, "quote": (item or {}).get("quote")})

    fields = raw.get("fields")
    for key, item in (fields if isinstance(fields, dict) else {}).items():
        spec_field = EXTRACT_FIELDS.get(key, key if key in units.FIELD_DIM else None)
        if spec_field is None or not item:
            continue
        if not isinstance(item, dict):
            reject(spec_field, "model output for this field is not an object", None)
            continue
        v, unit, quote = _num(item.get("value")), item.get("unit") or "", item.get("quote")
        if v is None:
            reject(spec_field, "value is not numeric", item)
            continue
        try:
            si = _round(units.to_si(v, unit, spec_field))
        except units.UnitError as e:
            reject(spec_field, f"unit: {e}", item)
            continue
        if not in_sane_range(spec_field, si):
            reject(spec_field, f"value {si:g} outside sanity range", item)
            continue
        ok, why = check_field(si, quote, page_text, field=spec_field)
        if ok:
            verified[spec_field] = VerifiedField(spec_field, si, quote, url, retrieved_at, dict(item))
        else:
            reject(spec_field, why, item)

    d = raw.get("dimensions")
    if d and not isinstance(d, dict):
        reject("footprint_polygon_m", "model output for dimensions is not an object", None)
        d = None
    if d:
        try:
            L = _round(units.to_si(_num(d.get("length")), d.get("unit") or "", "length_m"))
            W = _round(units.to_si(_num(d.get("width")), d.get("unit") or "", "width_m"))
            H = d.get("height")
            H = _round(units.to_si(_num(H), d.get("unit") or "", "height_m")) if _num(H) is not None else None
        except (units.UnitError, TypeError) as e:
            reject("footprint_polygon_m", f"unit: {e}", d)
        else:
            ok_fp, why_fp = check_footprint(L, W, d.get("quote"), page_text)
            sane = in_sane_range("length_m", L) and in_sane_range("width_m", W) and (
                H is None or in_sane_range("height_m", H))
            if ok_fp and sane:
                poly = rectangle_footprint(L, W)
                verified["footprint_polygon_m"] = VerifiedField(
                    "footprint_polygon_m", poly, d["quote"], url, retrieved_at, dict(d, length_m=L, width_m=W))
                if H is not None and "height_m" not in verified:
                    ok_h, why_h = check_field(H, d.get("quote"), page_text, field="height_m")
                    if ok_h:
                        verified["height_m"] = VerifiedField("height_m", H, d["quote"], url, retrieved_at, dict(d))
                    else:
                        reject("height_m", why_h, d)
            else:
                reject("footprint_polygon_m", why_fp if not ok_fp else "size outside sanity range", d)

    ip = raw.get("in_place_rotation")
    if isinstance(ip, dict) and isinstance(ip.get("value"), bool):
        ok, why = check_field(ip["value"], ip.get("quote"), page_text, field="in_place_rotation")
        if ok:
            verified["in_place_rotation"] = VerifiedField("in_place_rotation", ip["value"], ip["quote"], url, retrieved_at, dict(ip))
        else:
            reject("in_place_rotation", why, ip)

    price_info = None
    p = raw.get("price")
    if isinstance(p, dict):
        pv = _num(p.get("value"))
        if pv is not None:
            ok, why, tok = check_price(pv, p.get("quote"), page_text)
            if ok and tok["kind"] == "lease":  # a lease / subscription rate is never a purchase price
                price_info = {"value": None, "quote": p["quote"], "url": url, "retrieved_at": retrieved_at,
                              "is_public": False, "kind": "lease", "lease_value": pv,
                              "lease_period": tok["period"], "currency": tok["currency"]}
            elif ok:
                price_info = {"value": pv, "currency": tok["currency"], "quote": p["quote"], "url": url,
                              "retrieved_at": retrieved_at, "is_public": True, "kind": tok["kind"]}
            else:
                reject("price", why, p)
        if price_info is None and p.get("on_request_quote"):
            q = p["on_request_quote"]
            if check_field(True, q, page_text)[0]:
                price_info = {"value": None, "quote": q, "url": url, "retrieved_at": retrieved_at, "is_public": False}
            else:
                reject("price", "on-request quote not found in page text", {"quote": q})
    return verified, rejected, price_info


def rectangle_footprint(length_m: float, width_m: float) -> list[tuple[float, float]]:
    """Rectangle centred on the body centre (used as the rotation centre), +x forward. Always approximate."""
    hl, hw = length_m / 2.0, width_m / 2.0
    return [(_round(hl), _round(hw)), (_round(-hl), _round(hw)), (_round(-hl), _round(-hw)), (_round(hl), _round(-hw))]
