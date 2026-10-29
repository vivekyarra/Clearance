"""Clearance Report fact checker.

Rules
- Every numeric token (ints, decimals, scientific notation, percents, currency, ranges such as 71–93%,
  numbers in table cells, negative numbers, k/M multipliers, attached units, numbers written as words such as
  "ninety percent") must be followed in the same sentence or table cell by a valid [f_xxx] tag whose fact value
  matches it within tolerance after unit conversion (ratio <-> percent, m <-> cm <-> mm, s <-> min <-> h, ...).
  A number binds to the first tag run after it ("71% [f_002], 93% [f_001]"), or to the last run before it when
  no tag follows, so tags cannot be swapped between numbers of one sentence.
- Tolerance: max(half a unit of the last printed digit, tol x |fact value|).
- Exempt: section numbers in headings, list ordinals, fact ids, footnote markers, dates and times, URLs,
  identifiers with digits glued to letters (T4, MiR100, sb_12, 2D), inline code that holds an identifier
  (code holding a quantity such as `99%` is checked), robot model names and site names (taken from the facts
  pack, plus an explicit whitelist), citation lines (items with a link) in the "Sources"/"References" section.
- Numerals that are not plain digits (superscripts, vulgar fractions, circled digits) are rejected.
- Verdicts: a sentence (or table cell) that attaches a verdict label (Cleared / Cleared with changes /
  Not cleared, in any case, with markdown emphasis, or phrased as "is not cleared", "cleared with
  modifications", ...) to a robot whose code verdict differs is rejected.

`check_report` returns {"ok", "errors", "clean_md", "removed", "stats"}; `enforce` adds the single
regeneration round and the removal log, and always returns markdown that passes the check.
"""
from __future__ import annotations

import logging
import math
import re
import unicodedata
from dataclasses import dataclass
from functools import lru_cache
from typing import Callable, Iterable

from agents.factcheck.mdparse import Block, join_cells, parse_blocks, split_sentences

log = logging.getLogger("clearance.factcheck")

TAG_RE = re.compile(r"\[(f_[A-Za-z0-9_]+\??(?:\s*,\s*f_[A-Za-z0-9_]+\??)*)\]")
CODE_RE = re.compile(r"`[^`]*`")
LINK_URL_RE = re.compile(r"\]\((?:[^()\s]|\([^()\s]*\))*\)")
LINK_RE = re.compile(r"\[[^\[\]]*\]\((?:[^()\s]|\([^()\s]*\))*\)")
URL_RE = re.compile(r"<?https?://[^\s)>\]]+>?")
FOOTNOTE_RE = re.compile(r"\[\^[^\]\s]{1,24}\]:?")
MONTHS = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*\.?"
DATE_RES = [
    re.compile(r"\b\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?\b"),
    re.compile(rf"\b\d{{1,2}}(?:st|nd|rd|th)?\s+{MONTHS},?\s+\d{{4}}\b"),
    re.compile(rf"\b{MONTHS}\s+\d{{1,2}}(?:st|nd|rd|th)?,?\s+\d{{4}}\b"),
    re.compile(rf"\b{MONTHS}\s+\d{{4}}\b"),
    re.compile(r"\b\d{1,2}/\d{1,2}/\d{4}\b"),
    re.compile(r"\b\d{1,2}:\d{2}(?::\d{2})?(?:\s?[AaPp][Mm])?\b"),
    re.compile(r"\b(?:GMT|UTC)[+-]\d{1,2}(?::\d{2})?\b"),
]
SECTION_REF_RE = re.compile(r"(?:\b[Ss]ection|§)\s*\d+(?:\.\d+)*")
DEFAULT_EXEMPT_TERMS = [r"\b95\s?%\s?(?:CI|confidence)", r"\bCI\s?95\b", r"\b24/7\b"]
EXEMPT_SECTIONS = ("sources", "references")

NUM_RE = re.compile(r"(?<![\w.])(?:\d+(?:\.\d+)?[eE][+-]?\d+(?![\w.])|\d{1,3}(?:,\d{3})+(?!\d)(?:\.\d+)?|\d+(?:\.\d+)?|\.\d+)")
# digits glued after letters are identifiers (T4, MiR250) unless a percent sign follows ("about93%")
GLUED_PCT_RE = re.compile(r"(?<=[A-Za-z_])\d+(?:\.\d+)?(?=\s?%)")
# length-preserving normalization of look-alike characters before tokenizing
_LOOKALIKE = str.maketrans({"％": "%", "﹪": "%", "٪": "%", "－": "-", "﹣": "-", "‒": "-", "．": ".", "，": ",",
                            "＄": "$", "￥": "¥", "￡": "£", " ": " ", " ": " ", " ": " "})
CURRENCY_SYMBOLS = {"US$": "USD", "A$": "AUD", "AU$": "AUD", "C$": "CAD", "CA$": "CAD", "S$": "SGD", "HK$": "HKD",
                    "NZ$": "NZD", "R$": "BRL", "CN¥": "CNY", "$": "USD", "€": "EUR", "£": "GBP", "¥": "JPY|CNY",
                    "₹": "INR", "Rs.": "INR", "Rs": "INR"}
CURRENCY_CODES = {"USD", "EUR", "GBP", "INR", "JPY", "CNY", "SGD", "AUD", "CAD", "CHF", "HKD", "NZD", "BRL"}
CURRENCY_WORDS = {"dollar": "USD", "dollars": "USD", "euro": "EUR", "euros": "EUR", "rupee": "INR",
                  "rupees": "INR", "yen": "JPY"}
MULTIPLIERS = {"k": 1e3, "K": 1e3, "thousand": 1e3, "M": 1e6, "mn": 1e6, "million": 1e6, "bn": 1e9, "billion": 1e9}
ORDINALS = {"st", "nd", "rd", "th"}
PCT_WORDS = {"percent", "pct", "pp"}

# unit word -> (dimension, factor to the dimension's base unit)
UNITS: dict[str, tuple[str, float]] = {}
for _names, _dim, _f in [
    (["m", "meter", "meters", "metre", "metres"], "length", 1.0),
    (["cm", "centimeter", "centimeters", "centimetre", "centimetres"], "length", 0.01),
    (["mm", "millimeter", "millimeters", "millimetre", "millimetres"], "length", 0.001),
    (["km"], "length", 1000.0),
    (["m2", "m²", "sqm", "sq m", "square meters", "square metres"], "area", 1.0),
    (["m/s", "mps"], "speed", 1.0),
    (["km/h", "kph"], "speed", 1 / 3.6),
    (["s", "sec", "secs", "second", "seconds"], "time", 1.0),
    (["min", "mins", "minute", "minutes"], "time", 60.0),
    (["h", "hr", "hrs", "hour", "hours"], "time", 3600.0),
    (["day", "days"], "time", 86400.0),
    (["week", "weeks", "wk"], "time", 604800.0),
    (["month", "months", "mo"], "month", 1.0),
    (["year", "years", "yr", "yrs"], "month", 12.0),
    (["deg", "degree", "degrees", "°"], "angle", 1.0),
    (["rad"], "angle", 180 / math.pi),
    (["kg", "kilogram", "kilograms"], "mass", 1.0),
    (["g", "gram", "grams"], "mass", 0.001),
    (["x", "×", "times"], "factor", 1.0),
    (["persons", "person", "people", "pedestrians", "pedestrian"], "persons", 1.0),
    (["episodes", "episode", "runs", "run", "missions", "mission", "deliveries", "delivery", "legs", "leg",
      "stops", "stop", "near-misses", "near-miss", "boxes", "box", "hazards", "hazard", "robots", "robot",
      "experiments", "experiment", "changes", "change", "trials", "probes", "probe", "count"], "count", 1.0),
]:
    for _n in _names:
        UNITS[_n] = (_dim, _f)
FACT_UNITS = {"ratio": ("ratio", 1.0), "fraction": ("ratio", 1.0), "percent": ("percent", 1.0), "%": ("percent", 1.0),
              "pct": ("percent", 1.0), "per_mission": ("count", 1.0), "count/day": ("count", 1.0),
              "persons/min": ("persons", 1.0), "episodes": ("count", 1.0), "count": ("count", 1.0),
              "": (None, 1.0)}
_UNIT_ALT = "|".join(sorted((re.escape(u) for u in list(UNITS) + ["%", "percent", "per cent"]),
                            key=len, reverse=True))
UNIT_AFTER_RE = re.compile(rf"\s?({_UNIT_ALT})(?![A-Za-z0-9²])")
MULT_AFTER_RE = re.compile(r"\s?(k|K|M|mn|bn|thousand|million|billion)(?![A-Za-z0-9])")
PCT_AFTER_RE = re.compile(r"\s?(%|percent\b|per\s?cent\b|pct\b|pp\b|percentage\s+points?\b)", re.I)
CUR_CODE_AFTER_RE = re.compile(r"\s?(" + "|".join(sorted(CURRENCY_CODES)) + r")\b")
CUR_SYM_AFTER_RE = re.compile(r"\s?(€|£|¥|₹|\$)(?![\w$])")
CUR_WORD_AFTER_RE = re.compile(r"\s?(" + "|".join(sorted(CURRENCY_WORDS, key=len, reverse=True)) + r")\b", re.I)
_SYM_ALT = "|".join(re.escape(s) for s in sorted(CURRENCY_SYMBOLS, key=len, reverse=True))
CUR_BEFORE_RE = re.compile(rf"(?:(?<![A-Za-z])({_SYM_ALT})|\b({'|'.join(sorted(CURRENCY_CODES))}))\s?([-−–]?)$")
SIGN_BEFORE_RE = re.compile(r"([-−–])$")

# numbers written as words
_W_UNITS = {w: i for i, w in enumerate("zero one two three four five six seven eight nine ten eleven twelve thirteen "
                                       "fourteen fifteen sixteen seventeen eighteen nineteen".split())}
_W_TENS = {w: 10 * (i + 2) for i, w in enumerate("twenty thirty forty fifty sixty seventy eighty ninety".split())}
_W_SCALES = {"hundred": 100, "dozen": 12, "thousand": 1e3, "million": 1e6, "billion": 1e9}
_W_ALT = "|".join(sorted(list(_W_UNITS) + list(_W_TENS) + list(_W_SCALES), key=len, reverse=True))
WORD_NUM_RE = re.compile(rf"(?<![\w-])(?:{_W_ALT})(?:[\s-]+(?:{_W_ALT}))*(?![\w-])", re.I)

# verdict phrases
_SEP = r"[\s\-]+"
_NEG = (r"(?:not|never|cannot|can't|isn't|wasn't|aren't|weren't|won't|wouldn't|couldn't|doesn't|didn't|"
        rf"hasn't|haven't|no{_SEP}longer)")
_NEG_FILL = r"(?:yet|be|been|being|get|got|considered|deemed|rated|currently|fully|quite|so|as|it|is|was|even|still)"
_CWC_WORDS = (r"(?:changes?|modifications?|adjustments?|conditions?|caveats?|fix(?:es)?|measur\w*|layout|"
              r"re-?arrange\w*|moves?|moving|removal|removing)")
VERDICT_PATTERNS = [
    ("Not cleared", re.compile(rf"\b{_NEG}(?:{_SEP}{_NEG_FILL}){{0,3}}{_SEP}cleared\b|\buncleared\b", re.I)),
    ("Cleared with changes", re.compile(
        rf"\bconditionally{_SEP}cleared\b|\bcleared{_SEP}(?:only{_SEP})?"
        rf"(?:with|after|once|if|provided|subject{_SEP}to|pending|upon|following)"
        rf"(?:{_SEP}[\w']+){{0,3}}?{_SEP}{_CWC_WORDS}\b", re.I)),
    ("Cleared", re.compile(r"\bcleared\b", re.I)),
]
VERDICT_RE = re.compile(r"\b(cleared with changes|not cleared|cleared)\b", re.I)
VERDICT_CANON = {"cleared with changes": "Cleared with changes", "not cleared": "Not cleared", "cleared": "Cleared"}
_GENERIC_ALIASES = {"robot", "robots", "amr", "bot", "the robot", "service robot", "delivery robot", "pro", "plus",
                    "max", "mini", "lite"}


# ---------------------------------------------------------------- facts access
def _fget(f, name, default=None):
    if isinstance(f, dict):
        return f.get(name, default)
    return getattr(f, name, default)


def _origin(f) -> dict:
    o = _fget(f, "origin") or {}
    return o if isinstance(o, dict) else {"type": getattr(o, "type", ""), "ref": getattr(o, "ref", "")}


def fact_number(f) -> float | None:
    v = _fget(f, "value")
    if v is None or isinstance(v, (bool, str)):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


def fact_dim(unit: str | None) -> tuple[str | None, float, str | None]:
    """(dimension, factor, currency) of a fact unit such as 'm', 'ratio', 'USD/month', 'h/day'."""
    u = (unit or "").strip()
    if u in FACT_UNITS:
        d, f = FACT_UNITS[u]
        return d, f, None
    if u in UNITS:
        d, f = UNITS[u]
        return d, f, None
    base = u.split("/")[0].strip()
    if base.upper() in CURRENCY_CODES:
        return "currency", 1.0, base.upper()
    if base in UNITS:
        d, f = UNITS[base]
        return d, f, None
    if base in FACT_UNITS:
        d, f = FACT_UNITS[base]
        return d, f, None
    return None, 1.0, None


def _aliases(name: str) -> list[str]:
    """Shorter forms of a vendor + model name used in prose ("Northwind Tray T2" -> "Tray T2")."""
    words = name.split()
    out = []
    for i in range(1, len(words)):
        suffix = " ".join(words[i:])
        if not re.search(r"[A-Za-z]", suffix) or suffix.lower() in _GENERIC_ALIASES:
            continue
        if len(words) - i >= 2 or len(suffix) >= 4:
            out.append(suffix)
    return out


def robot_names_from_facts(facts) -> dict[str, list[str]]:
    """robot_id -> names used in prose (model name, vendor+model, shorter model forms, robot id)."""
    out: dict[str, list[str]] = {}
    for f in facts:
        key = _fget(f, "key") or ""
        label = _fget(f, "label") or ""
        rid = None
        if key.startswith("robot.name|"):
            rid = key.split("|", 1)[1]
        else:
            m = re.match(r"^Robot model \((.+)\)$", label)
            rid = m.group(1) if m else None
        if rid and isinstance(_fget(f, "value"), str):
            names = out.setdefault(rid, [rid])
            name = _fget(f, "value")
            if name not in names:
                names.append(name)
    # shorter forms only when no other robot claims them
    claims: dict[str, set[str]] = {}
    for rid, ns in out.items():
        for n in ns[1:]:
            for a in _aliases(n):
                claims.setdefault(a.lower(), set()).add(rid)
    full = {n.lower() for ns in out.values() for n in ns}
    for rid, ns in out.items():
        for n in list(ns[1:]):
            for a in _aliases(n):
                if claims[a.lower()] == {rid} and a.lower() not in full and a not in ns:
                    ns.append(a)
    return out


def site_names_from_facts(facts) -> list[str]:
    out = []
    for f in facts:
        key = _fget(f, "key") or ""
        if (key == "site.name" or (_fget(f, "label") or "") == "Site name") and isinstance(_fget(f, "value"), str):
            out.append(_fget(f, "value"))
    return out


def verdicts_from_facts(facts) -> dict[str, str]:
    out = {}
    for f in facts:
        key = _fget(f, "key") or ""
        ref = _origin(f).get("ref", "")
        val = _fget(f, "value")
        if not isinstance(val, str) or val not in VERDICT_CANON.values():
            continue
        if key.startswith("verdict|"):
            out[key.split("|", 1)[1]] = val
        elif ref.startswith("verdict:") and (_fget(f, "label") or "").startswith("Verdict,"):
            out[ref.split(":", 1)[1]] = val
    return out


# ---------------------------------------------------------------- tokenizer
@dataclass
class NumToken:
    text: str
    start: int
    end: int
    value: float          # magnitude x multiplier, signed
    decimals: int
    mult: float = 1.0
    pct: bool = False
    unit: str | None = None
    currency: str | None = None   # ISO code, or several codes joined by "|" ("¥" -> "JPY|CNY")
    negative: bool = False
    word: bool = False            # written as words ("ninety")


def _mask(s: str, spans: Iterable[tuple[int, int]]) -> str:
    chars = list(s)
    for a, b in spans:
        for i in range(max(a, 0), min(b, len(chars))):
            chars[i] = " "
    return "".join(chars)


@lru_cache(maxsize=4096)
def _term_re(term: str) -> re.Pattern:
    left = r"(?<!\w)" if re.match(r"\w", term) else ""
    right = r"(?!\w)" if re.search(r"\w$", term) else ""
    return re.compile(left + re.escape(term) + right, re.I)


def is_quantity(text: str) -> bool:
    """True when `text` states a quantity: a bare number, or a number with a unit, percent, currency or
    multiplier. Identifiers (`sb_2026_1000`, `T4`, `pb-slim-2`) and plain counters (`x = 42`) are not."""
    if re.fullmatch(r"\s*[-−+]?\s*\d[\d.,]*\s*", text.translate(_LOOKALIKE)):
        return True
    toks = tokenize_numbers(text)
    return any(t.pct or t.unit or t.currency or t.mult != 1.0 or t.word for t in toks)


def mask_exempt(text: str, whitelist: Iterable[str] = (), exempt_terms: Iterable[str] = ()) -> tuple[str, list[str]]:
    """Blank out everything that never needs a tag; returns (masked text, tag ids found)."""
    tags: list[str] = []
    spans: list[tuple[int, int]] = []
    for m in TAG_RE.finditer(text):
        tags += [t.strip() for t in m.group(1).split(",")]
        spans.append(m.span())
    for m in CODE_RE.finditer(text):
        inner = m.group(0)[1:-1]
        if is_quantity(inner):  # `99%` is a quantity in code formatting: check it
            spans += [(m.start(), m.start() + 1), (m.end() - 1, m.end())]
        else:
            spans.append(m.span())
    for rx in [LINK_URL_RE, URL_RE, FOOTNOTE_RE, SECTION_REF_RE, *DATE_RES]:
        spans += [m.span() for m in rx.finditer(text)]
    for term in sorted({w for w in whitelist if w}, key=len, reverse=True):
        spans += [m.span() for m in _term_re(term).finditer(text)]
    for pat in list(DEFAULT_EXEMPT_TERMS) + list(exempt_terms):
        spans += [m.span() for m in re.finditer(pat, text)]
    return _mask(text, spans), tags


def _word_value(words: str) -> float:
    total, cur = 0.0, 0.0
    for w in re.split(r"[\s-]+", words.lower()):
        if w in _W_UNITS:
            cur += _W_UNITS[w]
        elif w in _W_TENS:
            cur += _W_TENS[w]
        elif w in ("hundred", "dozen"):
            cur = (cur or 1) * _W_SCALES[w]
        elif w in _W_SCALES:
            total += (cur or 1) * _W_SCALES[w]
            cur = 0.0
    return total + cur


def _candidates(masked: str) -> list[tuple[int, int, str, float, int, bool]]:
    """(start, end, raw, value, decimals, word) for every numeric literal, in text order."""
    out = []
    for m in NUM_RE.finditer(masked):
        raw = m.group(0)
        after = masked[m.end():]
        # digits glued to letters: ordinal or identifier (1st, 2D, 4f2a) unless it is a unit/multiplier (5m, 12k)
        glued = re.match(r"[A-Za-z]+(?:/[A-Za-z]+)?", after)
        if glued:
            word = re.match(r"[A-Za-z]+", after).group(0)
            if not (glued.group(0) in UNITS or word in UNITS or word in MULTIPLIERS or word.lower() in PCT_WORDS
                    or word in CURRENCY_CODES or word.lower() in CURRENCY_WORDS):
                continue
        try:
            val = float(raw.replace(",", ""))
        except ValueError:
            continue
        if math.isinf(val) or math.isnan(val):
            val = float("inf")
        mant = re.split(r"[eE]", raw)[0]
        decimals = len(mant.split(".")[1]) if "." in mant else 0
        if re.search(r"[eE]", raw):
            exp = int(re.split(r"[eE]", raw)[1])
            decimals -= exp
        out.append((m.start(), m.end(), raw, val, decimals, False))
    for m in GLUED_PCT_RE.finditer(masked):
        out.append((m.start(), m.end(), m.group(0), float(m.group(0)), len(m.group(0).split(".")[1])
                    if "." in m.group(0) else 0, False))
    for m in WORD_NUM_RE.finditer(masked):
        raw = m.group(0)
        if raw.lower() == "one":  # the pronoun ("one of the robots") unless a percent or a measure follows
            nxt = masked[m.end():]
            um = UNIT_AFTER_RE.match(nxt)
            if not (PCT_AFTER_RE.match(nxt) or (um and UNITS.get(um.group(1), ("count",))[0] not in ("count",))):
                continue
        out.append((m.start(), m.end(), raw, _word_value(raw), 0, True))
    out.sort(key=lambda x: x[0])
    return out


def tokenize_numbers(masked: str) -> list[NumToken]:
    masked = masked.translate(_LOOKALIKE)
    toks: list[NumToken] = []
    for s, e, raw, val, decimals, word in _candidates(masked):
        if toks and s < toks[-1].end:  # inside the previous token's suffix ("5 km" then "km" is not a number)
            continue
        tok = NumToken(raw, s, e, val, decimals, word=word)
        pos = e
        mm = MULT_AFTER_RE.match(masked, pos)
        if mm and not (mm.group(1) == "M" and re.match(r"\s?M[A-Za-z]", masked[pos:])):
            tok.mult = MULTIPLIERS[mm.group(1)]
            pos = mm.end()
        um = PCT_AFTER_RE.match(masked, pos)
        if um:
            tok.pct = True
            pos = um.end()
        else:
            cm = CUR_CODE_AFTER_RE.match(masked, pos) or CUR_SYM_AFTER_RE.match(masked, pos) \
                or CUR_WORD_AFTER_RE.match(masked, pos)
            if cm:
                c = cm.group(1)
                tok.currency = CURRENCY_SYMBOLS.get(c) or CURRENCY_WORDS.get(c.lower()) or c
                pos = cm.end()
            else:
                um = UNIT_AFTER_RE.match(masked, pos)
                if um and um.group(1) in UNITS:
                    tok.unit = um.group(1)
                    pos = um.end()
        tok.end = pos
        # currency before: "$1,200", "USD 1,200", "€ 3", sign before or after the symbol
        # only a short window before the number matters (keeps tokenizing linear on long lines)
        w0 = max(0, s - 24)
        before = masked[w0:s]
        bm = CUR_BEFORE_RE.search(before)
        if bm and bm.start() == 0 and w0 > 0 and masked[w0 - 1].isalnum():
            bm = None
        cut = s
        if bm:
            sym = bm.group(1) or bm.group(2)
            tok.currency = CURRENCY_SYMBOLS.get(sym, sym)
            if bm.group(3):
                tok.negative = True
            cut = w0 + bm.start()
        # a sign written right against the number ("-300", "−$300", "–93%"); "A - 93%" is a dash
        sign_at = cut - 1 if cut > 0 and masked[cut - 1] in "-−–" else None
        if sign_at is not None and not tok.negative:
            prev = toks[-1] if toks else None
            is_range = prev is not None and masked[prev.end:sign_at].strip() == "" and prev.end > sign_at - 3
            pre_char = masked[sign_at - 1] if sign_at > 0 else ""
            if not is_range and (not pre_char or pre_char in " (\t[:=/~≈\n"):
                tok.negative = True
        tok.value = (-1 if tok.negative else 1) * val * tok.mult
        toks.append(tok)
    # ranges: "71–93%", "12-18 cm", "$1,200–1,500", "0.7 to 0.9", "between 71 and 93%"
    for a, b in zip(toks, toks[1:]):
        between = masked[a.end:b.start]
        joined = re.fullmatch(r"\s*(?:[–—-]|to)\s*", between) or (
            re.fullmatch(r"\s*and\s*", between) and re.search(r"\bbetween\s*$", masked[max(0, a.start - 16):a.start], re.I))
        if joined:
            if not (a.pct or a.unit or a.currency) and (b.pct or b.unit):
                a.pct, a.unit = b.pct, b.unit
            if b.currency is None and a.currency:
                b.currency = a.currency
            if a.mult == 1.0 and b.mult != 1.0:
                a.mult = b.mult
                a.value *= b.mult
    return toks


def unsupported_numerals(masked: str) -> list[tuple[int, str]]:
    """Positions of numerals that are not plain digits (⁹⁰, ½, ③); exponents after a letter (m², CO₂) are units."""
    out = []
    for i, ch in enumerate(masked):
        if ord(ch) < 128 or ch.isdecimal():
            continue
        if unicodedata.numeric(ch, None) is None:
            continue
        j = i - 1
        while j >= 0 and unicodedata.numeric(masked[j], None) is not None and not masked[j].isdecimal() \
                and ord(masked[j]) >= 128:
            j -= 1
        if j >= 0 and masked[j].isalpha() and not ("0" <= masked[j] <= "9"):
            continue
        out.append((i, ch))
    return out


# ---------------------------------------------------------------- matching
def match_token(tok: NumToken, fact, tol: float) -> tuple[bool, str]:
    fv = fact_number(fact)
    if fv is None:
        return False, "non_numeric_fact"
    if not math.isfinite(tok.value):
        return False, "value_mismatch"
    fdim, ffac, fcur = fact_dim(_fget(fact, "unit"))
    if tok.currency:
        if fdim == "currency" and fcur and fcur not in tok.currency.split("|"):
            return False, "currency_mismatch"
        if fdim not in (None, "currency"):
            return False, "unit_mismatch"
    base = abs(tok.value)
    half = 0.5 * 10 ** (-tok.decimals) * tok.mult
    cands: list[tuple[float, float]]
    if tok.pct:
        if fdim == "ratio":
            cands = [(base / 100, half / 100)]
        elif fdim == "percent":
            cands = [(base, half)]
        elif fdim is None:
            cands = [(base, half), (base / 100, half / 100)]
        else:  # a count, a length or money printed with a percent sign
            return False, "unit_mismatch"
    elif tok.unit:
        if tok.unit not in UNITS:
            return False, "unit_mismatch"
        tdim, tfac = UNITS[tok.unit]
        if fdim is None or tdim == "count" and fdim in ("count", "ratio", "persons"):
            cands = [(base, half)]
        elif tdim == fdim:
            cands = [(base * tfac / ffac, half * tfac / ffac)]
        elif fdim == "count" and tdim in ("persons", "factor"):
            cands = [(base, half)]
        else:
            return False, "unit_mismatch"
    elif fdim == "percent":
        cands = [(base, half), (base * 100, half * 100)]
    elif fdim == "ratio" and tok.decimals <= 0 and tok.mult == 1.0:
        # a bare integer ("change 1") is not a rounded ratio (0.78): only an exact 0 or 1 matches
        cands = [(base, 1e-9)]
    else:
        cands = [(base, half)]
    target = abs(fv)
    if tok.negative and fv > 0:
        return False, "sign_mismatch"
    for c, h in cands:
        allowed = max(h * (1 + 1e-9), tol * target) + 1e-12
        if abs(c - target) <= allowed:
            return True, ""
    return False, "value_mismatch"


def _fmt_fact(f) -> str:
    v, u = _fget(f, "value"), _fget(f, "unit") or ""
    if isinstance(v, (int, float)) and not isinstance(v, bool) and u == "ratio":
        return f"{v} ratio (= {v * 100:.4g}%)"
    return f"{v!r} {u}".strip()


# ---------------------------------------------------------------- segments
@dataclass
class Segment:
    text: str
    block: Block
    cell: int | None = None


def _tag_runs(text: str) -> list[tuple[int, int, list[str]]]:
    """Consecutive tags ("[f_1][f_2]", "[f_1], [f_2]", "[f_1, f_2]") form one run: (start, end, ids)."""
    runs: list[tuple[int, int, list[str]]] = []
    for m in TAG_RE.finditer(text):
        ids = [t.strip() for t in m.group(1).split(",")]
        if runs and re.fullmatch(r"[\s,]*", text[runs[-1][1]:m.start()]):
            runs[-1] = (runs[-1][0], m.end(), runs[-1][2] + ids)
        else:
            runs.append((m.start(), m.end(), ids))
    return runs


def _check_segment(seg: Segment, facts_by_id: dict, tol: float, whitelist, exempt_terms,
                   extra_spans: Iterable[tuple[int, int]] = ()) -> tuple[list[dict], int]:
    errors: list[dict] = []
    masked, tags = mask_exempt(seg.text, whitelist, exempt_terms)
    masked = _mask(masked, extra_spans)
    line = seg.block.line
    snippet = seg.text.strip()
    bad_ids = set()
    for t in tags:
        if t.endswith("?") or t not in facts_by_id:
            bad_ids.add(t)
            errors.append({"type": "unknown_fact", "line": line, "token": f"[{t}]", "fact_ids": [t.rstrip("?")],
                           "segment": snippet,
                           "message": f"line {line}: tag [{t}] does not name a fact in the facts pack; "
                                      f"use only ids from the facts pack."})
    runs = [(a, b, [i for i in ids if i not in bad_ids]) for a, b, ids in _tag_runs(seg.text)]
    for i, ch in unsupported_numerals(masked):
        errors.append({"type": "unsupported_number", "line": line, "token": ch, "fact_ids": [], "segment": snippet,
                       "message": f"line {line}: '{ch}' is a numeral the checker cannot verify; write the number "
                                  f"with plain digits followed by its [f_xxx] tag, or remove it."})
    toks = tokenize_numbers(masked)
    for tok in toks:
        shown = seg.text[tok.start:tok.end].strip()
        after = [r for r in runs if r[0] >= tok.end]
        run = after[0] if after else (runs[-1] if runs else None)
        valid = run[2] if run is not None else []
        if not valid:
            errors.append({"type": "untagged_number", "line": line, "token": shown, "fact_ids": [],
                           "segment": snippet,
                           "message": f"line {line}: number '{shown}' has no fact tag after it in the same "
                                      f"sentence/cell; add the matching [f_xxx] right after the number or remove "
                                      f"the number."})
            continue
        reasons = []
        for fid in valid:
            ok, why = match_token(tok, facts_by_id[fid], tol)
            if ok:
                break
            reasons.append(why)
        else:
            kind = next((r for r in ("value_mismatch", "sign_mismatch", "currency_mismatch", "unit_mismatch")
                         if r in reasons), "non_numeric_fact")
            kind = "value_mismatch" if kind == "sign_mismatch" else kind
            desc = "; ".join(f"{fid} = {_fmt_fact(facts_by_id[fid])}" for fid in valid)
            errors.append({"type": kind, "line": line, "token": shown, "fact_ids": valid, "segment": snippet,
                           "message": f"line {line}: '{shown}' does not match the tag that follows it ({desc}). "
                                      f"Print the fact value (rounding allowed) or cite the right fact."})
    return errors, len(toks)


# ---------------------------------------------------------------- verdict statements
def _vnorm(text: str) -> str:
    """Same-length copy of `text` without markdown emphasis, code ticks, HTML tags or fact tags."""
    t = TAG_RE.sub(lambda m: " " * len(m.group(0)), text)
    t = re.sub(r"<[^<>]{0,200}>", lambda m: " " * len(m.group(0)), t)
    t = re.sub(r"[*~`]", " ", t)
    t = re.sub(r"(?<!\w)_+|_+(?!\w)", lambda m: " " * len(m.group(0)), t)
    return t.replace("’", "'").replace("‘", "'")


@lru_cache(maxsize=4096)
def _name_re(name: str, flags: int) -> re.Pattern:
    return re.compile(r"(?<![\w])" + re.escape(name) + r"(?![\w])", flags)


def _mentions(norm: str, names: dict[str, list[str]]) -> list[tuple[int, int, str]]:
    out = []
    for rid, ns in names.items():
        for i, n in enumerate(ns):
            flags = re.I if i > 0 and len(n) >= 4 else 0  # robot ids match exactly, names in any case
            for m in _name_re(n, flags).finditer(norm):
                out.append((m.start(), m.end(), rid))
    return sorted(out)


def verdict_labels(text: str) -> list[tuple[int, int, str]]:
    """(start, end, canonical label) of every verdict statement in `text`; the earliest, longest phrase wins."""
    norm = _vnorm(text)
    found = []
    for label, rx in VERDICT_PATTERNS:
        found += [(m.start(), -(m.end() - m.start()), m.end(), label) for m in rx.finditer(norm)]
    out: list[tuple[int, int, str]] = []
    for s, _, e, label in sorted(found):
        if out and s < out[-1][1]:
            continue
        out.append((s, e, label))
    return out


def _attach(pos_s: int, pos_e: int, mentions: list[tuple[int, int, str]]) -> str | None:
    before = [x for x in mentions if x[1] <= pos_s]
    after = [x for x in mentions if x[0] >= pos_e]
    return before[-1][2] if before else (after[0][2] if after else None)


def _verdict_error(label: str, rid: str, token: str, text: str, line: int, verdicts: dict[str, str]) -> dict:
    return {"type": "verdict_mismatch", "line": line, "token": token, "fact_ids": [], "segment": text.strip(),
            "robot_id": rid,
            "message": f"line {line}: states '{label}' for {rid}, but the code verdict is "
                       f"'{verdicts[rid]}'. Use the code verdict label exactly."}


def _verdict_errors(text: str, line: int, names: dict[str, list[str]], verdicts: dict[str, str],
                    context: list[tuple[int, int, str]] | None = None) -> list[dict]:
    """Label occurrences attach to the nearest preceding robot mention (else the first one after);
    `context` (mentions from a neighbouring table cell or header) is used when the text names no robot."""
    if not verdicts:
        return []
    mentions = _mentions(_vnorm(text), names)
    if not mentions and not context:
        return []
    errs = []
    for s, e, label in verdict_labels(text):
        rid = _attach(s, e, mentions) if mentions else context[-1][2]
        if rid is None or rid not in verdicts:
            continue
        if verdicts[rid] != label:
            errs.append(_verdict_error(label, rid, text[s:e], text, line, verdicts))
    return errs


# ---------------------------------------------------------------- report
def _safe_prose(prefix: str, text: str) -> str:
    """Re-joined prose must not turn into a heading, table row, list item, quote or code fence."""
    if not prefix and re.match(r"\s*(#|\||>|[-*+]\s|\d+[.)]\s|```|~~~|[-*_]{3,}\s*$)", text):
        text = "\\" + text.lstrip()
    return prefix + text


def check_report(md: str, facts: list, tol: float = 0.01, *, whitelist: Iterable[str] = (),
                 exempt_terms: Iterable[str] = (), verdicts: dict[str, str] | None = None,
                 exempt_sections: Iterable[str] = EXEMPT_SECTIONS) -> dict:
    """Validate every number and verdict statement in `md` against `facts`.
    Returns {"ok", "errors", "clean_md" (failing sentences/cells removed), "removed", "stats"}."""
    md = md if isinstance(md, str) else ""
    facts = list(facts or [])
    facts_by_id = {_fget(f, "fact_id"): f for f in facts}
    names = robot_names_from_facts(facts)
    wl = set(whitelist)
    for ns in names.values():
        wl.update(n for n in ns if re.search(r"[A-Za-z]", n))
    wl.update(n for n in site_names_from_facts(facts) if re.search(r"[A-Za-z]", n))
    vmap = dict(verdicts_from_facts(facts), **(verdicts or {}))
    exempt_sections = {s.lower() for s in exempt_sections}

    errors: list[dict] = []
    removed: list[dict] = []
    out_lines: list[str] = []
    n_numbers = 0
    header: list[str] | None = None   # header cells of the current table
    prev_kind = "blank"

    for blk in parse_blocks(md):
        if blk.kind not in ("table_row", "table_sep"):
            header = None
        kind_before, prev_kind = prev_kind, blk.kind
        in_exempt = blk.section.strip().lower() in exempt_sections and blk.kind != "heading"
        if blk.kind in ("blank", "hr", "table_sep"):
            out_lines += blk.raw
            continue
        if blk.kind == "code":
            out_lines.append(blk.raw[0])
            closed = len(blk.raw) > 1 and bool(re.match(r"^\s*(```|~~~)", blk.raw[-1]))
            body = blk.raw[1:-1] if closed else blk.raw[1:]
            for k, code_line in enumerate(body):
                if not is_quantity_line(code_line):
                    out_lines.append(code_line)
                    continue
                sub = Block("code", blk.line + 1 + k, [code_line], section=blk.section)
                errs, n = _check_segment(Segment(code_line, sub), facts_by_id, tol, wl, exempt_terms)
                n_numbers += n
                if errs:
                    errors += errs
                    removed.append({"line": sub.line, "segment": code_line.strip(), "errors": [e["type"] for e in errs]})
                else:
                    out_lines.append(code_line)
            if closed:
                out_lines.append(blk.raw[-1])
            continue
        text_all = " | ".join(blk.cells) if blk.kind == "table_row" else blk.text
        if in_exempt and (LINK_RE.search(text_all) or URL_RE.search(text_all)):
            # citation lines: source titles may carry numbers; verdict statements are still checked
            ves = _verdict_errors(text_all, blk.line, names, vmap)
            if ves:
                errors += ves
                removed.append({"line": blk.line, "segment": text_all, "errors": [e["type"] for e in ves]})
            else:
                out_lines += blk.raw
            continue
        if blk.kind == "table_row":
            is_header = header is None
            row_errs_by_cell: dict[int, list[dict]] = {}
            for ci, cell in enumerate(blk.cells):
                errs, n = _check_segment(Segment(cell, blk, ci), facts_by_id, tol, wl, exempt_terms)
                n_numbers += n
                # robot context for a label in this cell: earlier cells of the row, then the column header
                ctx = []
                for cj in range(ci - 1, -1, -1):
                    ctx = _mentions(_vnorm(blk.cells[cj]), names)
                    if ctx:
                        break
                if not ctx and header is not None and ci < len(header):
                    ctx = _mentions(_vnorm(header[ci]), names)
                if not ctx:
                    for cj in range(ci + 1, len(blk.cells)):
                        ctx = _mentions(_vnorm(blk.cells[cj]), names)
                        if ctx:
                            ctx = ctx[:1]
                            break
                errs += _verdict_errors(cell, blk.line, names, vmap, context=ctx)
                if errs:
                    row_errs_by_cell.setdefault(ci, []).extend(errs)
            cells = list(blk.cells)
            for ci, errs in sorted(row_errs_by_cell.items()):
                errors += errs
                removed.append({"line": blk.line, "segment": cells[ci], "errors": [e["type"] for e in errs]})
                cells[ci] = "—"
            if is_header:
                header = list(blk.cells)
            out_lines.append(join_cells(cells) if row_errs_by_cell else blk.raw[0])
            continue
        if blk.kind == "heading":
            errs, n = _check_segment(Segment(blk.text, blk), facts_by_id, tol, wl, exempt_terms)
            errs += _verdict_errors(blk.text, blk.line, names, vmap)
            n_numbers += n
            if errs:
                errors += errs
                removed.append({"line": blk.line, "segment": blk.text, "errors": [e["type"] for e in errs]})
            else:
                out_lines += blk.raw
            continue
        # prose: paragraph / list item / quote
        kept, any_removed = [], False
        for sent in split_sentences(blk.text):
            errs, n = _check_segment(Segment(sent, blk), facts_by_id, tol, wl, exempt_terms)
            errs += _verdict_errors(sent, blk.line, names, vmap)
            n_numbers += n
            if errs:
                errors += errs
                removed.append({"line": blk.line, "segment": sent.strip(), "errors": [e["type"] for e in errs]})
                any_removed = True
            else:
                kept.append(sent)
        if not any_removed:
            out_lines += blk.raw
        elif "".join(kept).strip():
            out_lines.append(_safe_prose(blk.prefix, "".join(kept).rstrip()))
    return {"ok": not errors, "errors": errors, "clean_md": "\n".join(out_lines), "removed": removed,
            "stats": {"numbers_checked": n_numbers, "errors": len(errors)}}


def is_quantity_line(line: str) -> bool:
    masked, _ = mask_exempt(line)
    return is_quantity(masked) or bool(unsupported_numerals(masked))


def format_errors(errors: list[dict], limit: int = 60) -> str:
    lines = [f"- {e['message']}" for e in errors[:limit]]
    if len(errors) > limit:
        lines.append(f"- ... and {len(errors) - limit} more")
    return "\n".join(lines)


def _drop_failing_blocks(md: str, errors: list[dict]) -> str:
    """Last-resort removal: drop every line of each block that still has an error (a whole table when its
    header row fails), so the result is valid markdown and always passes."""
    bad = {e.get("line") for e in errors}
    blocks = parse_blocks(md)
    drop: set[int] = set()
    for i, b in enumerate(blocks):
        span = set(range(b.line, b.line + len(b.raw)))
        if not (span & bad):
            continue
        if b.kind == "code":
            drop |= span & bad
            continue
        drop |= span
        if b.kind in ("table_row", "table_sep"):  # drop the whole table: a table without its header is not one
            j = i
            while j > 0 and blocks[j - 1].kind in ("table_row", "table_sep"):
                j -= 1
            k = i
            while k + 1 < len(blocks) and blocks[k + 1].kind in ("table_row", "table_sep"):
                k += 1
            for bb in blocks[j:k + 1]:
                drop |= set(range(bb.line, bb.line + len(bb.raw)))
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    return "\n".join(l for n, l in enumerate(lines, 1) if n not in drop)


def enforce(md: str, facts: list, regenerate_fn: Callable[[str, list[dict]], str] | None = None, *,
            tol: float = 0.01, max_passes: int = 25, **kw) -> dict:
    """One regeneration with the error list, then removal of any sentence/cell that still fails.
    regenerate_fn(previous_md, errors) -> new markdown. Returns
    {"md", "ok_first_pass", "ok_after_regeneration", "regenerated", "errors_initial", "errors_final", "removed",
     "ok_final"}. The returned markdown always passes check_report."""
    md = md if isinstance(md, str) else ""
    first = check_report(md, facts, tol, **kw)
    result = {"md": md, "ok_first_pass": first["ok"], "ok_after_regeneration": first["ok"], "regenerated": False,
              "errors_initial": first["errors"], "errors_final": [], "removed": [], "ok_final": first["ok"]}
    if first["ok"]:
        return result
    candidate = md
    if regenerate_fn is not None:
        try:
            out = regenerate_fn(md, first["errors"])
            if isinstance(out, str) and out.strip():
                candidate = out
                result["regenerated"] = True
            else:
                log.warning("report regeneration returned no markdown (%s); keeping the first draft",
                            type(out).__name__)
        except Exception as e:  # keep the first draft; removal below still guarantees a clean report
            log.warning("report regeneration failed: %s", e)
    second = check_report(candidate, facts, tol, **kw) if result["regenerated"] else first
    result["ok_after_regeneration"] = second["ok"]
    result["errors_final"] = second["errors"]
    clean, removed = (candidate if second["ok"] else second["clean_md"]), list(second["removed"])
    ok = second["ok"]
    for _ in range(max(1, max_passes)):  # removal can merge text; re-check until stable
        if ok:
            break
        again = check_report(clean, facts, tol, **kw)
        if again["ok"]:
            ok = True
            break
        removed += again["removed"]
        if again["clean_md"] == clean:  # nothing left to remove sentence by sentence
            clean = _drop_failing_blocks(clean, again["errors"])
        else:
            clean = again["clean_md"]
    while not ok:  # every round drops at least one line, so this ends (at worst with an empty report)
        again = check_report(clean, facts, tol, **kw)
        if again["ok"]:
            ok = True
            break
        removed += again["removed"]
        shorter = _drop_failing_blocks(clean, again["errors"])
        clean = shorter if shorter != clean else ""
    for r in removed:
        log.warning("fact-check removed line %s: %r (%s)", r["line"], r["segment"][:160], ",".join(r["errors"]))
    result["md"], result["removed"], result["ok_final"] = clean, removed, ok
    return result
