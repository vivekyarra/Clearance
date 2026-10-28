"""Quantity parsing and unit normalization, done by code (never by the LLM).

Targets: length -> m, speed -> m/s, mass -> kg, time -> h (RobotSpec.runtime_h), angle -> deg.
A grade in percent is accepted for angle fields and converted with atan(pct/100).
"""
from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

# alias -> (dimension, factor to target unit)
_UNITS: dict[str, tuple[str, float]] = {}


def _reg(dim: str, factor: float, *aliases: str):
    for a in aliases:
        _UNITS[a] = (dim, factor)


_reg("length", 0.001, "mm", "millimeter", "millimeters", "millimetre", "millimetres")
_reg("length", 0.01, "cm", "centimeter", "centimeters", "centimetre", "centimetres")
_reg("length", 1.0, "m", "meter", "meters", "metre", "metres")
_reg("length", 0.0254, "in", "inch", "inches", '"', "''")
_reg("length", 0.3048, "ft", "feet", "foot", "'")
_reg("speed", 1.0, "m/s", "m/sec", "m/second", "mps", "meters per second", "metres per second", "meter per second")
_reg("speed", 0.001, "mm/s", "mm/sec")
_reg("speed", 1 / 60, "m/min", "m/minute", "meters per minute", "metres per minute")
_reg("speed", 0.01, "cm/s", "cm/sec")
_reg("speed", 1 / 3.6, "km/h", "km/hr", "kmh", "kph", "kilometers per hour", "kilometres per hour")
_reg("speed", 0.44704, "mph", "miles per hour")
_reg("speed", 0.3048, "ft/s", "ft/sec", "fps", "feet per second")
_reg("mass", 1.0, "kg", "kgs", "kilogram", "kilograms")
_reg("mass", 0.001, "g", "gram", "grams")
_reg("mass", 0.45359237, "lb", "lbs", "ibs", "pound", "pounds")
_reg("time", 1.0, "h", "hr", "hrs", "hour", "hours")
_reg("time", 1 / 60, "min", "mins", "minute", "minutes")
_reg("angle", 1.0, "°", "deg", "degree", "degrees")
_reg("grade", 1.0, "%")

# Which physical dimension each RobotSpec numeric field holds.
FIELD_DIM: dict[str, str] = {
    "height_m": "length",
    "length_m": "length",
    "width_m": "length",
    "max_threshold_m": "length",
    "min_aisle_width_vendor_m": "length",
    "safety_stop_distance_m": "length",
    "min_turn_radius_m": "length",
    "max_speed_mps": "speed",
    "nominal_speed_mps": "speed",
    "payload_kg": "mass",
    "runtime_h": "time",
    "max_slope_deg": "angle",
}

_SYMBOLIC = {'"', "''", "'", "°", "%"}
# A unit must end the token: "m" is not the start of "min" or "m^2" (area, from "m²"), "kg" is not the
# start of "kgf"; a degree sign followed by C/F is a temperature, never an angle.
_TEMP = r"(?!\s?(?:c|f|celsius|fahrenheit)(?![a-z]))"


def _alias_re(a: str) -> str:
    if a == "°":
        return re.escape(a) + _TEMP
    if a in _SYMBOLIC:
        return re.escape(a)
    tail = r"(?![a-z^])"
    if a in ("deg", "degree", "degrees"):
        tail += _TEMP
    return re.escape(a) + tail


_alias_alt = "|".join(_alias_re(a) for a in sorted(_UNITS, key=len, reverse=True))
UNIT_RE = re.compile(_alias_alt, re.IGNORECASE)

# number: 1,975 | 1 450 | 12.5 | .5 | 1/2 (fractions only as the whole number)
_NUM = r"(?:\d+\s*/\s*\d+(?![\d.,])|\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\d])|\d{1,3}(?: \d{3})+(?![\d.])|\d+(?:[.,]\d+)?|\.\d+)"
_RANGE_SEP = r"\s*(?:-|~|to|–|—)\s*"
_DIM_SEP = r"\s*[x×X*]\s*"
_UNIT_GAP = r"\s?"

DIMS_RE = re.compile(
    rf"(?<![\w.])({_NUM})(?:\s*(?P<u1>{_alias_alt}))?{_DIM_SEP}({_NUM})(?:\s*(?P<u2>{_alias_alt}))?"
    rf"(?:{_DIM_SEP}({_NUM}))?{_UNIT_GAP}\s*(?P<u3>{_alias_alt})",
    re.IGNORECASE,
)
COMPOUND_TIME_RE = re.compile(
    rf"(?<![\w.])(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hours?)\s*(?:and\s*)?(\d+(?:\.\d+)?)\s*(?:min|mins|minutes?)(?![a-z])",
    re.IGNORECASE,
)
QTY_RE = re.compile(
    rf"(?<![\w.])(?P<a>{_NUM})(?:{_RANGE_SEP}(?P<b>{_NUM}))?{_UNIT_GAP}\s*(?P<u>{_alias_alt})",
    re.IGNORECASE,
)
QTY_RANGE_UNIT_RE = re.compile(  # "0.5m/s - 1.2m/s", "0.5 m/s~1.2 m/s"
    rf"(?<![\w.])(?P<a>{_NUM})\s*(?P<u1>{_alias_alt}){_RANGE_SEP}(?P<b>{_NUM})\s*(?P<u2>{_alias_alt})",
    re.IGNORECASE,
)
NUM_RE = re.compile(rf"(?<![\w])({_NUM})")


class UnitError(ValueError):
    pass


@dataclass
class Quantity:
    values: list[float]          # numbers as written (range: [lo, hi]; dims: [a, b, (c)])
    unit: str                    # canonical alias (lowercase)
    dim: str
    text: str = ""
    kind: str = "single"         # single | range | dims | compound
    si: list[float] = field(default_factory=list)
    start: int = -1


def parse_number(s: str) -> float:
    s = s.strip()
    if "/" in s:
        n, d = (p.strip() for p in s.split("/", 1))
        if float(d) == 0:
            raise ValueError(s)
        return float(n) / float(d)
    if re.fullmatch(r"\d{1,3}(?:,\d{3})+(?:\.\d+)?", s) or re.fullmatch(r"\d{1,3}(?: \d{3})+", s):
        return float(s.replace(",", "").replace(" ", ""))
    if "," in s:  # decimal comma ("1,2")
        return float(s.replace(",", "."))
    return float(s)


def number_variants(s: str) -> list[float]:
    """All readings of a number token: '1 450' -> [1450, 1, 450]; '1,2' -> [1.2, 1, 2]."""
    out = []
    try:
        out.append(parse_number(s))
    except ValueError:
        pass
    for part in re.split(r"[ ,/]", s):
        if part and re.fullmatch(r"\d+(?:\.\d+)?", part):
            out.append(float(part))
    return out


def canon_unit(u: str) -> str:
    u = (u if isinstance(u, str) else "").strip().lower()
    if u not in _UNITS:
        raise UnitError(f"unknown unit {u!r}")
    return u


def unit_dim(u: str) -> str:
    return _UNITS[canon_unit(u)][0]


def convert(value: float, unit: str, dim: str | None = None) -> float:
    """Convert value in `unit` to the target unit of its dimension (or of `dim`)."""
    u = canon_unit(unit)
    udim, factor = _UNITS[u]
    if dim is not None and udim != dim:
        if dim == "angle" and udim == "grade":
            return math.degrees(math.atan(float(value) / 100.0))
        raise UnitError(f"unit {unit!r} is {udim}, expected {dim}")
    if udim == "grade":
        raise UnitError("percent grade only converts to an angle field")
    return float(value) * factor


def to_si(value: float, unit: str, field_name: str | None = None) -> float:
    dim = FIELD_DIM.get(field_name) if field_name else None
    if field_name and dim is None:
        raise UnitError(f"field {field_name!r} has no numeric dimension")
    return convert(value, unit, dim)


def _si(values, unit, dim_target=None):
    try:
        return [convert(v, unit, dim_target) for v in values]
    except UnitError:
        return []


# a unit written in brackets right after the numbers, e.g. "435*450*1120 (mm)"
_PAREN_UNIT_RE = re.compile(r"(?<=\d)(\s*)\((mm|cm|m|in|kg|lbs?|h|km/h|m/s)\)", re.I)


def parse_quantities(text: str) -> list[Quantity]:
    """Every number+unit quantity in `text` (dims, ranges, compound h+min, singles). Overlapping
    readings are all returned: validation accepts a value if ANY reading supports it."""
    text = _PAREN_UNIT_RE.sub(lambda m: f"{m.group(1)} {m.group(2)} ", text)  # "435*450 (mm)": same length
    out: list[Quantity] = []
    for m in COMPOUND_TIME_RE.finditer(text):
        v = float(m.group(1)) + float(m.group(2)) / 60.0
        out.append(Quantity([v], "h", "time", m.group(0), "compound", [v], m.start()))
    for m in DIMS_RE.finditer(text):
        u = canon_unit(m.group("u3"))
        pairs = [(m.group(1), m.group("u1")), (m.group(3), m.group("u2")), (m.group(5), None)]
        try:
            vals = [parse_number(n) for n, _ in pairs if n]
            per_unit = [canon_unit(pu) if pu else u for n, pu in pairs if n]
        except (ValueError, UnitError):
            continue
        if len({_UNITS[x][0] for x in per_unit}) != 1:
            continue
        si = [s for v, pu in zip(vals, per_unit) for s in _si([v], pu)]
        out.append(Quantity(vals, u, _UNITS[u][0], m.group(0), "dims", si, m.start()))
    for m in QTY_RANGE_UNIT_RE.finditer(text):
        u1, u2 = canon_unit(m.group("u1")), canon_unit(m.group("u2"))
        if _UNITS[u1][0] != _UNITS[u2][0]:
            continue
        try:
            a, b = parse_number(m.group("a")), parse_number(m.group("b"))
        except ValueError:
            continue
        si = _si([a], u1) + _si([b], u2)
        out.append(Quantity([a, b], u2, _UNITS[u2][0], m.group(0), "range", si, m.start()))
    for m in QTY_RE.finditer(text):
        u = canon_unit(m.group("u"))
        toks = [m.group("a")] + ([m.group("b")] if m.group("b") else [])
        for tok_set in _token_readings(toks):
            out.append(Quantity(tok_set, u, _UNITS[u][0], m.group(0),
                                "range" if len(tok_set) > 1 else "single", _si(tok_set, u), m.start()))
    _rank = {"compound": 0, "dims": 1, "range": 2, "single": 3}
    out.sort(key=lambda q: (q.start, _rank[q.kind]))
    return out


def _token_readings(toks: list[str]) -> list[list[float]]:
    """The reading of the tokens as written. '1 200 mm' is 1200 mm only: reading it also as '200 mm'
    would let a value of 0.2 m pass for a 1.2 m quote."""
    try:
        return [[parse_number(t) for t in toks]]
    except ValueError:
        return []


def numbers_in(text: str) -> list[float]:
    vals: list[float] = []
    for m in NUM_RE.finditer(text):
        vals.extend(number_variants(m.group(1)))
    return vals


def si_candidates(quantities: list[Quantity], dim: str) -> list[float]:
    """SI values in `quantities` that are of dimension `dim` (grade counts for angle)."""
    vals: list[float] = []
    for q in quantities:
        if q.dim == dim:
            vals.extend(q.si)
        elif dim == "angle" and q.dim == "grade":
            vals.extend(convert(v, "%", "angle") for v in q.values)
    return vals
