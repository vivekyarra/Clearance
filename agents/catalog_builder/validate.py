"""Verbatim-quote validator.

A field value is accepted only when
  1. its quote appears in the page text after whitespace/unicode normalization, starting and ending on
     token boundaries ("2 m/s" is not a quote of "1.2 m/s"), and it carries no instructions to the reader;
  2. the quote contains a number that, read with the unit written next to it and converted by code,
     equals the value (for a known field: only quantities of that field's dimension count), and
  3. that number belongs to the field: the label nearest to it is not another item's label ("Machine
     Weight 65 kg" is not a payload, "Camera FOV 15°" is not a slope); a range only supports its upper end
     for max_* fields (lower end for runtime); robot dimensions only support length/width/height at the
     position the page's order label (W x D x H ...) gives; and a value standing side by side with a
     different value of the same kind (a comparison table listing several robots) is ambiguous.
Prices (check_price): the amount must sit next to a currency sign; monthly/lease amounts are labelled
"lease", "from $X" is labelled "from", discounts, deposits and fees are not prices.
"""
from __future__ import annotations

import functools
import math
import re
import unicodedata

from agents.catalog_builder import units

MIN_QUOTE_CHARS = 3
MAX_QUOTE_CHARS = 400
REL_TOL = 1e-3
CONTEXT_CHARS = 60

_PRE_NFKC = {
    "º": "°",  # masculine ordinal used as a degree sign ("75º/sec")
    "˚": "°",  # ring above
    "²": "^2", "³": "^3",  # m², m³ are areas/volumes, never lengths
}
_POST_NFKC = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'", "′": "'",
    "“": '"', "”": '"', "„": '"', "″": '"', "''": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-", "—": "-", "―": "-", "−": "-",
    "⁄": "/", "×": " x ", "✕": " x ", "✖": " x ",
    "、": ",", "。": ".",
}
_ZERO_WIDTH = re.compile("[­​‌‍⁠﻿]")
_MD_LINK = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_LINEBREAK_HYPHEN = re.compile(r"([A-Za-z])-[ \t]*\r?\n[ \t|]*(?=[a-z])")  # OCR/PDF "Maxi-\nmum"

# Text that addresses the reader (an LLM) instead of describing the robot: never a spec quote.
INJECTION_RE = re.compile(
    r"ignore (?:all |any |the )?(?:previous|prior|above|earlier|preceding)|disregard (?:all|any|the|previous|prior|above)"
    r"|system prompt|\byou are (?:now )?(?:an? |the )?(?:ai|assistant|language model|llm|chatbot)"
    r"|\b(?:assistant|system|user)\s*:|new instructions|(?:follow|these|the following) instructions"
    r"|prompt injection|respond with|output (?:only|the following)|<\|im_|\[/?inst\]|page text (?:start|end)"
    r"|\b(?:set|report|return|change) (?:the |its )?(?:value|field|spec\w*|max\w*|payload|speed)\b.{0,20}\bto\b",
    re.I,
)


def normalize_text(s: str) -> str:
    """Canonical form for containment checks: NFKC, unified quotes/dashes/x-signs, markdown emphasis
    and links reduced to their text, table pipes and all whitespace runs collapsed to one space."""
    if not s:
        return ""
    for a, b in _PRE_NFKC.items():
        s = s.replace(a, b)
    s = unicodedata.normalize("NFKC", s)
    s = _ZERO_WIDTH.sub("", s)
    for a, b in _POST_NFKC.items():
        s = s.replace(a, b)
    s = _MD_LINK.sub(r"\1", s)
    s = s.replace("**", "").replace("__", "").replace("`", "")
    s = re.sub(r"(?m)^\s{0,3}#{1,6}\s+", "", s)
    s = s.replace("|", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()


@functools.lru_cache(maxsize=64)
def _page_variants(page_text: str) -> tuple[str, ...]:
    """Normalized page, plus a variant with line-break hyphenation joined (OCR'd / PDF text)."""
    a = normalize_text(page_text)
    joined = _LINEBREAK_HYPHEN.sub(r"\1", page_text)
    b = normalize_text(joined) if joined != page_text else a
    return (a,) if b == a else (a, b)


def _boundary_ok(hay: str, s: int, e: int, nq: str) -> bool:
    """The match must not start or end inside a word or a number."""
    if s > 0:
        p, q0 = hay[s - 1], nq[0]
        if (p.isdigit() and q0.isdigit()) or (p.isalpha() and q0.isalpha()):
            return False
        if q0.isdigit() and p in ".," and s >= 2 and hay[s - 2].isdigit():
            return False
    if e < len(hay):
        n, ql = hay[e], nq[-1]
        if (n.isdigit() and ql.isdigit()) or (n.isalpha() and ql.isalpha()):
            return False
        if ql.isdigit() and n in ".," and e + 1 < len(hay) and hay[e + 1].isdigit():
            return False
    return True


def occurrences(nq: str, page_text: str) -> list[tuple[str, int, int]]:
    """(normalized page, start, end) of every token-aligned occurrence of normalized quote `nq`."""
    out = []
    for hay in _page_variants(page_text or ""):
        i = hay.find(nq)
        while i >= 0:
            if _boundary_ok(hay, i, i + len(nq), nq):
                out.append((hay, i, i + len(nq)))
            i = hay.find(nq, i + 1)
        if out:
            break
    return out


def _close(a: float, b: float, rel: float = REL_TOL) -> bool:
    return math.isclose(a, b, rel_tol=rel, abs_tol=1e-6)


# ---------------------------------------------------------------- labels (which item a number belongs to)
_W = r"(?:\s*width)?"
_PACKAGING = (r"(?:tray|packag\w*|packing|shipping|carton|crate|box|charg\w*(?:\s*(?:pile|station|dock))?|screen|display"
              r"|load surface|platform|shelf|shelves|drawer|cabin|compartment|lift\w*|wheels?|groove\w*(?:\s*crossing)?"
              r"|lidar|camera|door)(?:\s*(?:dimensions?|size|width|height|length|diameter))?")
_LEN = {
    "length": r"length|depth|long",
    "width": r"width|wide",
    "height": r"height|tall",
    "threshold": r"threshold|sill|steps?|obstacles?|surmountable(?:\s*height)?|overcoming(?:\s*height)?|gap|bump|curb|kerb",
    "aisle": rf"(?:aisle|corridor|passage|path|pass|passing|travel|lane|doorway|door){_W}|clearance",
    "stop": r"brak\w*(?:\s*distance)?|stop\w*(?:\s*distance)?",
    "radius": r"turn\w*\s*radius|turning circle|radius",
}


def _len_labels(own: str) -> tuple[str, str]:
    others = "|".join(v for k, v in _LEN.items() if k != own)
    return _LEN[own], f"{_PACKAGING}|dimensions?|size|{others}"


FIELD_LABELS: dict[str, tuple[str, str]] = {  # field -> (own label regex, foreign label regex)
    "max_speed_mps": (r"speed|velocity|forwards?|travel\w*|driv\w*|moving|cruise",
                      r"(?:docking|turning|rotation\w*|angular|reverse|backwards?|lift\w*|charging|elevator|conveyor)"
                      r"(?:\s*speed)?|backwards?|wind"),
    "payload_kg": (r"payload|load(?:ing)?(?:\s*(?:capacity|weight))?|capacity|weight capacity|carry\w*|total|max(?:imum)?\.?\s*loads?",
                   r"(?:tray|layer|shelf)\s*loads?|loads?\s*(?:per|/)\s*(?:tray|layer|shelf)|(?:per|/|each)\s*(?:tray|layer|shelf)"
                   r"|(?:machine|robot|net|self|body|own|total)?\s*weight|weighs|tow\w*|battery"),
    "runtime_h": (r"battery life|run-?\s?time|operat\w*(?:\s*time)?|running time|working time|endurance|autonomy",
                  r"charg\w*(?:\s*time)?|standby|warranty|lead time|delivery time|response"),
    "max_slope_deg": (r"slope|incline|decline|climb\w*(?:\s*angle)?|gradient|grade|ramp",
                      r"fov|field of view|view\w*|lidar|camera|scan\w*|rotat\w*|turn\w*|tilt\w*|humidity|battery|charg\w*"
                      r"|temperature|lifting"),
}
FIELD_LABELS["nominal_speed_mps"] = FIELD_LABELS["max_speed_mps"]
for _f, _k in (("length_m", "length"), ("width_m", "width"), ("height_m", "height"),
               ("max_threshold_m", "threshold"), ("min_aisle_width_vendor_m", "aisle"),
               ("safety_stop_distance_m", "stop"), ("min_turn_radius_m", "radius")):
    FIELD_LABELS[_f] = _len_labels(_k)
# a dims quantity ("565 x 537 x 1290 mm") only needs to not be another object's size
_DIMS_LABELS = (r"(?:robot|machine|body|overall|product|external)?\s*(?:dimensions?|size|footprint)", _PACKAGING)

# For a range ("0.5-1.2 m/s") only one end is the field's value.
RANGE_END = {"max_speed_mps": "hi", "payload_kg": "hi", "max_threshold_m": "hi", "max_slope_deg": "hi",
             "runtime_h": "lo"}
FOOTPRINT_AXES = {"length_m": "length", "width_m": "width", "height_m": "height"}

_AXIS = {"l": "length", "d": "length", "length": "length", "depth": "length", "w": "width", "width": "width",
         "h": "height", "height": "height"}
_AX = r"(length|width|depth|height|[lwdh])"
ORDER_RE = re.compile(rf"(?<![a-z]){_AX}\s*[x*/]\s*{_AX}(?:\s*[x*/]\s*{_AX})?(?![a-z])", re.I)


def dims_order(text: str) -> list[str] | None:
    """Axis order of a dimensions label: 'Dimensions (W x D x H)' -> ['width', 'length', 'height']."""
    found = None
    for m in ORDER_RE.finditer(text or ""):
        axes = [_AXIS[g.lower()] for g in m.groups() if g]
        if len(set(axes)) == len(axes) and {"length", "width"} <= set(axes):
            found = axes  # the last one wins: it is nearest to the numbers that follow
    return found


def _labels(text: str, own: str, foreign: str) -> list[tuple[int, int, str]]:
    found = []
    for kind, rx in (("own", own), ("foreign", foreign)):
        for m in re.finditer(rf"(?:(?<![a-z])|(?=[^a-z]))(?:{rx})(?![a-z])", text, re.I):
            if m.end() > m.start():
                found.append((m.start(), m.end(), kind))
    # drop labels contained in a longer one ("speed" inside "docking speed")
    keep = [a for a in found if not any(b != a and b[0] <= a[0] and a[1] <= b[1] and (b[1] - b[0]) > (a[1] - a[0])
                                        for b in found)]
    return keep


def _q_end(q: units.Quantity) -> int:
    return q.start + len(q.text)


def _nearest_label(text: str, q: units.Quantity, own: str, foreign: str) -> str | None:
    best = None
    for s, e, kind in _labels(text, own, foreign):
        if e <= q.start:
            d, side = q.start - e, 0
        elif s >= _q_end(q):
            d, side = s - _q_end(q), 1
        else:
            continue
        key = (d, side)
        if best is None or key < best[0]:
            best = (key, kind)
    return best[1] if best else None


def _q_si(q: units.Quantity, dim: str) -> list[float]:
    if q.dim == dim:
        return list(q.si)
    if dim == "angle" and q.dim == "grade":
        return [units.convert(v, "%", "angle") for v in q.values]
    return []


def _same_value(a: units.Quantity, b: units.Quantity, dim: str) -> bool:
    sa, sb = sorted(_q_si(a, dim)), sorted(_q_si(b, dim))
    if not sa or not sb:
        return False
    tol = 5e-3 if a.unit == b.unit else 0.1  # unit conversions on pages are rounded
    if len(sa) == len(sb):
        return all(_close(x, y, tol) for x, y in zip(sa, sb))
    # a range next to a conversion of one of its ends ("0-20 mm | 0.79 in")
    return a.unit != b.unit and any(_close(x, y, tol) for x in sa for y in sb)


_SIDE_BY_SIDE = re.compile(r"[\s,;/]*")


def _ambiguous(ctx: str, qs: int, qe: int, cand: units.Quantity, dim: str, labels=None) -> bool:
    """Another quantity of the same kind but a different value stands right next to the candidate
    (only spaces, pipes, commas or slashes between): e.g. a comparison table row. A neighbour labelled
    as another item ("10 kg/tray") does not count."""
    for o in units.parse_quantities(ctx):
        if not _q_si(o, dim) or (cand.kind == "dims") != (o.kind == "dims"):
            continue
        if labels and _nearest_label(ctx, o, *labels) == "foreign":
            continue
        os_, oe = o.start, _q_end(o)
        if os_ < qe and oe > qs:  # overlapping reading of the same text
            continue
        gap = ctx[oe:qs] if oe <= qs else ctx[qe:os_]
        if _SIDE_BY_SIDE.fullmatch(gap) and not _same_value(cand, o, dim):
            return True
    return False


def _dims_axis_ok(q: units.Quantity, value: float, axis: str, order: list[str] | None) -> bool:
    si = q.si
    if order and len(order) >= len(si):
        idx = [i for i, a in enumerate(order[:len(si)]) if a == axis]
    elif axis == "height":
        idx = [2] if len(si) == 3 else []
    else:
        idx = [0, 1]
    return any(i < len(si) and _close(value, si[i]) for i in idx)


def _order_for(nq: str, occ: tuple[str, int, int]) -> list[str] | None:
    hay, s, _ = occ
    return dims_order(nq) or dims_order(hay[max(0, s - CONTEXT_CHARS):s])


def _context(occ: tuple[str, int, int]) -> tuple[str, int]:
    hay, s, e = occ
    a = max(0, s - CONTEXT_CHARS)
    while a > 0 and not hay[a - 1].isspace():
        a -= 1
    b = min(len(hay), e + CONTEXT_CHARS)
    while b < len(hay) and not hay[b].isspace():
        b += 1
    return hay[a:b], s - a


def _numeric_check(value: float, nq: str, occs, field: str, *, allow_dims: bool = True) -> tuple[bool, str]:
    dim = units.FIELD_DIM[field]
    qty = units.parse_quantities(nq)
    policy = RANGE_END.get(field)
    axis = FOOTPRINT_AXES.get(field)
    ranges = [q for q in qty if q.kind == "range" and _q_si(q, dim)]
    own, foreign = FIELD_LABELS.get(field, ("(?!)", "(?!)"))
    matched, why_not = [], f"no {dim} quantity in quote equals {value:g}"
    dims_spans = [(d.start, _q_end(d)) for d in qty if d.kind == "dims"]
    for q in qty:
        si = _q_si(q, dim)
        if not si:
            continue
        if q.kind != "dims" and any(a <= q.start and _q_end(q) <= b for a, b in dims_spans):
            continue  # "1350 mm" at the end of "835 x 500 x 1350 mm" is the dims' last entry, read as dims
        if q.kind == "dims":
            if not (allow_dims and axis):
                continue
            if not any(_close(value, v) for v in si):
                continue
            if _nearest_label(nq, q, *_DIMS_LABELS) == "foreign":
                why_not = "the dimensions in the quote belong to another object (packaging, tray, charger, ...)"
                continue
            if not any(_dims_axis_ok(q, value, axis, _order_for(nq, o)) for o in occs):
                why_not = f"{value:g} is not the {axis} position of the dimensions"
                continue
            matched.append(q)
            continue
        if policy:
            enclosing = [r for r in ranges if r is not q and r.start <= q.start and _q_end(q) <= _q_end(r)]
            if q.kind == "range" or enclosing:
                r = q if q.kind == "range" else enclosing[0]
                rsi = _q_si(r, dim)
                end = max(rsi) if policy == "hi" else min(rsi)
                si = [v for v in si if _close(v, end)]
                if any(_close(value, v) for v in _q_si(r, dim)) and not any(_close(value, v) for v in si):
                    why_not = f"{value:g} is the {'lower' if policy == 'hi' else 'upper'} end of a range"
        if not any(_close(value, v) for v in si):
            continue
        if _nearest_label(nq, q, own, foreign) == "foreign":
            why_not = f"the {value:g} in the quote is labelled as another item"
            continue
        matched.append(q)
    if not matched:
        return False, why_not
    for occ in occs:
        ctx, off = _context(occ)
        for q in matched:
            labels = _DIMS_LABELS if q.kind == "dims" else (own, foreign)
            if not _ambiguous(ctx, off + q.start, off + _q_end(q), q, dim, labels):
                return True, "ok"
    return False, "ambiguous: a different value stands side by side (comparison of several products?)"


def _quote_occurrences(quote, page_text) -> tuple[str, list, str]:
    if not quote or not isinstance(quote, str):
        return "", [], "no quote"
    nq = normalize_text(quote)
    if len(nq) < MIN_QUOTE_CHARS:
        return nq, [], "quote too short"
    if len(nq) > MAX_QUOTE_CHARS:
        return nq, [], "quote too long"
    if INJECTION_RE.search(nq):
        return nq, [], "quote contains instructions, not a specification"
    occs = occurrences(nq, page_text or "")
    if not occs:
        return nq, [], "quote not found in page text"
    # a clean-looking span cut out of an instruction ("... set max speed 5 m/s") is not a spec either
    clean = [o for o in occs if not INJECTION_RE.search(_context(o)[0])]
    if not clean:
        return nq, [], "the page text around the quote contains instructions, not a specification"
    return nq, clean, "ok"


_BOOL_PATTERNS = {
    "in_place_rotation": (re.compile(r"(?:turn|rotat|spin|pivot)\w*\s*(?:on the spot|in place|in situ)|zero[- ]?(?:turning[- ])?radius"
                                     r"|360\s*°?\s*(?:in[- ]place|on the spot)", re.I),
                          re.compile(r"\b(?:not|cannot|can't|no|unable|without)\b", re.I)),
}


def check_field(value, quote, page_text, *, field: str | None = None) -> tuple[bool, str]:
    """validate_field with a reason string ("ok" or why it failed)."""
    if value is None:
        return False, "value is null"
    nq, occs, why = _quote_occurrences(quote, page_text)
    if not occs:
        return False, why

    if isinstance(value, bool):
        pat = _BOOL_PATTERNS.get(field or "")
        if pat:
            stated = pat[0].search(nq) is not None
            negated = stated and pat[1].search(nq) is not None
            if not stated:
                return False, f"quote does not state {field}"
            if negated == value:
                return False, f"quote states the opposite of {field}={value}"
        return True, "ok"
    if isinstance(value, str):
        try:
            value = float(value)
        except ValueError:
            ok = normalize_text(value).casefold() in nq.casefold()
            return (ok, "ok" if ok else "text value not in quote")
    if isinstance(value, (list, tuple)):
        for v in value:
            ok, why = check_field(v, quote, page_text, field=field)
            if not ok:
                return False, why
        return True, "ok"
    try:
        value = float(value)
    except (TypeError, ValueError):
        return False, "value is not numeric"
    if not math.isfinite(value):
        return False, "value is not finite"

    if field and units.FIELD_DIM.get(field):
        return _numeric_check(value, nq, occs, field)
    # unknown or dimensionless field: a raw number in the quote, or any converted quantity
    if any(_close(value, v, 1e-9) for v in units.numbers_in(nq)):
        return True, "ok"
    if any(_close(value, v) for q in units.parse_quantities(nq) for v in q.si):
        return True, "ok"
    return False, f"number {value:g} not in quote"


def check_footprint(length_m, width_m, quote, page_text, height_m=None) -> tuple[bool, str]:
    """Length and width (and height, if given) must be distinct positions of one robot-dimensions
    quantity, mapped through the page's order label, or each a separately labelled number."""
    for v in (length_m, width_m) + ((height_m,) if height_m is not None else ()):
        if v is None or isinstance(v, bool) or not isinstance(v, (int, float)) or not math.isfinite(v) or v <= 0:
            return False, "footprint size is not a positive number"
    nq, occs, why = _quote_occurrences(quote, page_text)
    if not occs:
        return False, why
    reason = "length and width are not distinct positions of the robot dimensions"
    for q in units.parse_quantities(nq):
        if q.kind != "dims" or q.dim != "length" or len(q.si) < 2:
            continue
        if _nearest_label(nq, q, *_DIMS_LABELS) == "foreign":
            reason = "the dimensions in the quote belong to another object (packaging, tray, charger, ...)"
            continue
        for occ in occs:
            order = _order_for(nq, occ)
            si = q.si
            if order and len(order) >= len(si):
                pos = {a: i for i, a in enumerate(order[:len(si)])}
                want = {"length": length_m, "width": width_m}
                if height_m is not None:
                    want["height"] = height_m
                ok = all(a in pos and _close(v, si[pos[a]]) for a, v in want.items())
            else:
                ok = ((_close(length_m, si[0]) and _close(width_m, si[1])) or
                      (_close(length_m, si[1]) and _close(width_m, si[0])))
                if height_m is not None:
                    ok = ok and len(si) == 3 and _close(height_m, si[2])
            if not ok:
                continue
            ctx, off = _context(occ)
            if _ambiguous(ctx, off + q.start, off + _q_end(q), q, "length", _DIMS_LABELS):
                reason = "ambiguous: a different value stands side by side (comparison of several products?)"
                continue
            return True, "ok"
    # separately labelled numbers ("Length 890 mm ... Width 580 mm"), never the same number twice
    parts = [(length_m, "length_m"), (width_m, "width_m")] + ([(height_m, "height_m")] if height_m is not None else [])
    results = [_numeric_check(float(v), nq, occs, f, allow_dims=False) for v, f in parts]
    if all(ok for ok, _ in results):
        return True, "ok"
    if any(q.kind == "dims" for q in units.parse_quantities(nq)):
        return False, reason
    return False, next(w for ok, w in results if not ok)


# ---------------------------------------------------------------- prices
_CUR = r"US\$|USD|\$|€|EUR|£|GBP"
_AMT = r"\d{1,3}(?:[, ]\d{3})+(?:\.\d+)?|\d+(?:\.\d+)?"
_MULT = r"(?:\s?(?P<mult>k|thousand|m|mm|million)(?![a-z]))?"
MONEY_RE = re.compile(rf"(?P<cur>{_CUR})\s?(?P<amt>{_AMT}){_MULT}|(?P<amt2>{_AMT}){_MULT.replace('mult', 'mult2')}\s?(?P<cur2>{_CUR})(?![a-z])",
                      re.I)
CURRENCY = {"US$": "USD", "$": "USD", "USD": "USD", "€": "EUR", "EUR": "EUR", "£": "GBP", "GBP": "GBP"}
_PERIOD_AFTER = re.compile(r"^\s*(?:/|per|a|an|each|every)\s*(?P<p>mo|mth|month|yr|year|annum|week|wk|day|hour|hr)\b"
                           r"|^\s*(?P<p2>monthly|annually|yearly|weekly|daily|hourly)\b", re.I)
_LEASE_WORDS = re.compile(r"\b(?:leas\w*|rent\w*|subscription|subscribe|raas|robot[- ]as[- ]a[- ]service|financ\w*"
                          r"|instal+ments?|per month|monthly|/\s*mo)\b", re.I)
_FROM_BEFORE = re.compile(r"(?:\bfrom|starting (?:at|from)|starts (?:at|from)|as low as|as little as)\s*:?\s*$", re.I)
_NOT_PRICE_BEFORE = re.compile(r"(?:save|saving|savings|off|discount|deposit|shipping|delivery|fee|rebate|credit|refund"
                               r"|reservation|down payment|tax|vat|grant|subsidy|revenue|roi)\w*\s*(?:of|:)?\s*(?:up to\s*)?$", re.I)
_NOT_PRICE_AFTER = re.compile(r"^\s*(?:off|discount|savings?|deposit|rebate|credit|fee|shipping|in savings|per year in savings)\b", re.I)
_MULTS = {"k": 1e3, "thousand": 1e3, "m": 1e6, "mm": 1e6, "million": 1e6}


def money_tokens(text: str) -> list[dict]:
    """Every currency amount in `text`: {value, currency, kind: purchase|from|lease|other, period, start}."""
    out = []
    for m in MONEY_RE.finditer(text or ""):
        cur = m.group("cur") or m.group("cur2")
        amt = (m.group("amt") or m.group("amt2")).replace(",", "").replace(" ", "")
        mult = (m.group("mult") or m.group("mult2") or "").lower()
        try:
            val = float(amt) * _MULTS.get(mult, 1.0)
        except ValueError:
            continue
        before, after = text[max(0, m.start() - 40):m.start()], text[m.end():m.end() + 30]
        clause = re.split(r"[.;!?]\s|\bor\b|\band\b|,", before)[-1]
        per = _PERIOD_AFTER.match(after)
        if per or _LEASE_WORDS.search(clause) or _LEASE_WORDS.match(after.strip()):
            kind = "lease"
        elif _NOT_PRICE_BEFORE.search(clause) or _NOT_PRICE_AFTER.match(after):
            kind = "other"
        elif _FROM_BEFORE.search(clause):
            kind = "from"
        else:
            kind = "purchase"
        period = None
        if per:
            p = (per.group("p") or per.group("p2") or "").lower()
            period = {"mo": "month", "mth": "month", "monthly": "month", "yr": "year", "annum": "year",
                      "annually": "year", "yearly": "year", "wk": "week", "weekly": "week", "daily": "day",
                      "hr": "hour", "hourly": "hour"}.get(p, p)
        elif kind == "lease":
            period = "month" if re.search(r"month|/\s*mo", clause + after, re.I) else None
        out.append({"value": val, "currency": CURRENCY.get(cur.upper() if cur.isalpha() else cur, "USD"),
                    "kind": kind, "period": period, "start": m.start()})
    return out


def check_price(value, quote, page_text) -> tuple[bool, str, dict | None]:
    """(ok, reason, token). ok only when `value` is a currency amount in the quote; token["kind"] tells
    a purchase price from a 'from' price and a lease; discounts, deposits and fees are rejected."""
    try:
        v = float(value)
    except (TypeError, ValueError):
        return False, "price is not numeric", None
    if not math.isfinite(v) or v <= 0:
        return False, "price is not a positive number", None
    nq, occs, why = _quote_occurrences(quote, page_text)
    if not occs:
        return False, why, None
    toks = [t for t in money_tokens(nq) if _close(t["value"], v, 1e-6)]
    if not toks:
        return False, f"no currency amount equal to {v:g} in quote", None
    good = [t for t in toks if t["kind"] != "other"]
    if not good:
        return False, "the amount is a discount, deposit or fee, not a price", None
    return True, "ok", good[0]


def validate_field(value, quote, page_text, *, field: str | None = None) -> bool:
    """Contract entry point. `value` is the normalized value (SI for known fields, raw otherwise)."""
    return check_field(value, quote, page_text, field=field)[0]
