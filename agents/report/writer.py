"""Clearance Report writer (PLAN 7.11 / 7.12 / 7.13): a hybrid of code and Nemotron 3 Ultra.

Code renders every number-dense, required block straight from the facts pack (already fact-tagged, so it always
passes the checker): site evidence, the verdict of each robot with its rule-check tables, the robot comparison
table with conservative-optimistic ranges, the Pinch Map summary, the recommended changes table, payback, hazards
and manual checks, methodology, scope and sources (with "awaiting review" labels).

Nemotron 3 Ultra (agents.llm.client.LLM, role "ultra") writes only the narrative, one short request per section:
the summary, the "why" paragraph of each robot, the "How we investigated" narrative and the trade-off discussion.
Each request carries an allowed-facts list (only the facts relevant to that section, with the exact rendered value
and tag to copy). Each section is fact-checked on its own: one regeneration with the error list, then only the
failing sentences of that section are removed. A section left empty falls back to the offline template text.

offline mode : the same assembly with the template text for every narrative section.
Mode: CLEARANCE_LLM_MODE=offline|live (default live when NEBIUS_API_KEY is set, else offline).

`write_report` returns the markdown (a str subclass whose `.meta` holds the per-section metrics: first-pass errors,
regenerations, sentences removed, fallbacks, model calls). In live mode the metrics are also saved to
analyses/<analysis_id>/report_meta.json when the analysis id is known.
"""
from __future__ import annotations

import json
import logging
import os
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any, Callable

from agents.factcheck.check import check_report, enforce, format_errors
from agents.report.facts import (DENSITY_ORDER, VERDICT_RANK, _d, facts_by_key, remap_ids, remap_tags,
                                 tag_text_numbers)

log = logging.getLogger("clearance.report")

SUMMARY_TITLE = "Summary"
SECTIONS = [
    "Site evidence", "Verdicts", "How we investigated", "Why it fails (investigation)",
    "Robot comparison (simulation-based estimates)", "Pinch Map", "Recommended changes", "Payback",
    "Trade-offs between the robots", "Hazards and manual checks", "Methodology", "Scope", "Sources",
]
CURRENCY_SYMBOL = {"USD": "$", "EUR": "€", "GBP": "£", "INR": "₹"}


def _success_min() -> float | None:
    try:
        from clearance_core.verdict import load_config
        return float(load_config()["success_min"])
    except Exception:
        return None


def _plain(v: float) -> str:
    """Positional notation only: the checker reads digits, never '1e+06'."""
    if abs(v - round(v)) < 1e-9 and abs(v) < 1e15:
        return f"{int(round(v))}" if abs(v) < 1e4 else f"{int(round(v)):,}"
    if abs(v) >= 1e4:
        return f"{v:,.0f}"
    out = f"{v:.6f}".rstrip("0").rstrip(".")
    return "0" if out in ("", "-0") else out


def code_if_numbered(text: str) -> str:
    """Names with standalone digits ("aisle 3") print as code so they are not read as quantities."""
    text = str(text)
    return f"`{text}`" if re.search(r"(?<![A-Za-z_])\d", text) and "`" not in text else text


def llm_mode() -> str:
    m = (os.environ.get("CLEARANCE_LLM_MODE") or ("live" if os.environ.get("NEBIUS_API_KEY") else "offline")).lower()
    return "live" if m == "live" else "offline"


# ---------------------------------------------------------------- formatting
def _get(f, name, default=None):
    return f.get(name, default) if isinstance(f, dict) else getattr(f, name, default)


def fmt_value(f) -> str:
    v, unit = _get(f, "value"), (_get(f, "unit") or "")
    if isinstance(v, str) or v is None:
        return str(v) if v is not None else "n/a"
    base = unit.split("/")[0]
    if unit == "ratio":
        from clearance_core.verdict import format_pct
        return format_pct(v, _success_min())
    if unit in ("percent", "%"):
        return f"{v:.0f}%" if abs(v - round(v)) < 1e-9 else f"{v:.1f}%"
    if base in CURRENCY_SYMBOL or (base.isupper() and len(base) == 3):
        sym = CURRENCY_SYMBOL.get(base, base + " ")
        body = f"{abs(v):,.2f}" if abs(v) < 100 and abs(v - round(v)) > 1e-9 else f"{abs(v):,.0f}"
        suffix = "/" + unit.split("/", 1)[1] if "/" in unit else ""
        return ("-" if v < 0 else "") + sym + body + suffix
    if unit == "m":
        return f"{v * 100:.1f} cm" if abs(v) < 0.1 else f"{v:.2f} m"
    if unit == "m2":
        return f"{v:.1f} m²"
    if unit == "s":
        return f"{v:.0f} s"
    if unit == "m/s":
        return f"{v:.2f} m/s"
    if unit == "months":
        return f"{v:.1f} months"
    if unit == "deg":
        return f"{v:.0f}°"
    if unit in ("kg", "h"):
        return f"{_plain(v)} {unit}"
    if unit in ("count", "episodes"):
        return f"{v:.0f}"
    if unit == "per_mission":
        return f"{v:.2f}"
    return _plain(v)


def tag(f) -> str:
    return f"[{_get(f, 'fact_id')}]"


def ft(f) -> str:
    """Formatted value plus its tag."""
    return f"{fmt_value(f)} {tag(f)}"


def rng(lo, hi) -> str:
    a, b = sorted([lo, hi], key=lambda x: _get(x, "value"))
    if fmt_value(a) == fmt_value(b):
        return f"{fmt_value(a)} {tag(a)}{tag(b)}"
    return f"{fmt_value(a).rstrip('%')}–{fmt_value(b)} {tag(a)}{tag(b)}"


class _Ctx:
    def __init__(self, facts, citations, findings, verdicts, investigation):
        self.facts = list(facts)
        self.k = facts_by_key(self.facts)
        self.citations = citations or []
        self.findings = [_d(x) for x in findings or []]
        self.verdicts = {v["robot_id"]: v for v in (_d(x) for x in verdicts or [])}
        inv = investigation
        self.invs = [_d(i) for i in (inv if isinstance(inv, list) else [inv] if inv is not None else [])]
        self.robots = [key.split("|", 1)[1] for key in self.k if key.startswith("robot.name|")]
        for rid in self.verdicts:
            if rid not in self.robots:
                self.robots.append(rid)

    def f(self, key):
        return self.k.get(key)

    def name(self, rid):
        f = self.f(f"robot.name|{rid}")
        return _get(f, "value") if f is not None else rid

    def verdict_label(self, rid):
        v = self.verdicts.get(rid)
        if v:
            return v["label"]
        f = self.f(f"verdict|{rid}")
        return _get(f, "value") if f is not None else None

    def ref_density(self, rid):
        for d in ["normal", "quiet", "peak", "none"]:
            if self.f(f"sim.success|{rid}|{d}|nominal") or self.f(f"sim.success|{rid}|{d}|conservative"):
                return d
        return None

    def ranked(self):
        def score(rid):
            d = self.ref_density(rid)
            c = self.f(f"sim.success|{rid}|{d}|conservative") or self.f(f"sim.success|{rid}|{d}|nominal")
            return (VERDICT_RANK.get(self.verdict_label(rid), 9), -(_get(c, "value") if c is not None else -1), rid)
        return sorted(self.robots, key=score)

    def safe(self, line: str, fallback: str = "") -> str:
        """Keep only the parts of a line built from free text (hypotheses, claims) that pass the checker."""
        res = check_report(apply_wording(line), self.facts)
        return res["clean_md"] if res["ok"] or res["clean_md"].strip() else fallback


def _change_text(ctx: _Ctx, rid: str, i: int) -> list[str]:
    """Describe optimizer change i of a robot from its facts (object ids are carried in the fact keys)."""
    out, objs = [], []
    for k in ctx.k:
        for prefix in (f"opt.move_m|{rid}|{i}|", f"opt.rot_deg|{rid}|{i}|"):
            if k.startswith(prefix) and k[len(prefix):] not in objs:
                objs.append(k[len(prefix):])
    for oid in sorted(objs):
        part = f"move `{oid}`"
        d, dr = ctx.f(f"opt.move_m|{rid}|{i}|{oid}"), ctx.f(f"opt.dir|{rid}|{i}|{oid}")
        if d is not None:
            part += f" {ft(d)}" + (f" {_get(dr, 'value')}" if dr is not None and _get(dr, "value") else "")
        r = ctx.f(f"opt.rot_deg|{rid}|{i}|{oid}")
        if r is not None:
            part += f" and rotate it {ft(r)}"
        out.append(part)
    for k in ctx.k:
        if k.startswith(f"opt.remove|{rid}|{i}|"):
            out.append(f"remove `{_get(ctx.f(k), 'value')}`")
    n = ctx.f(f"opt.add_n|{rid}|{i}")
    if n is not None:
        out.append(f"add {ft(n)} boxes")
    return out or ["layout change"]


def _exp_change_text(ctx: _Ctx, e: dict, inv_id: str) -> str:
    eid, ch, kind = e.get("experiment_id", "exp"), e.get("change") or {}, e.get("kind", "other")

    def p(path):
        f = ctx.f(f"exp.param|{inv_id}|{eid}|{path}")
        return ft(f) if f is not None else None

    if kind == "reduce_pedestrians_zone":
        s = f"pedestrian flow in zone `{ch.get('zone_id', '?')}`"
        return s + (f" scaled to {p('ped_scale')}" if p("ped_scale") else " reduced")
    if kind == "change_density" and ch.get("density"):
        return f"traffic level set to {ch['density']}"
    if kind == "swap_robot" and ch.get("robot_id"):
        return f"same route with {ctx.name(ch['robot_id'])}"
    if kind == "remove_object":
        ids = ch.get("remove") or [ch.get("object_id", "?")]
        return "removed " + ", ".join(f"`{x}`" for x in ids)
    if kind == "measure_threshold":
        s = f"threshold `{ch.get('threshold_id', '?')}`"
        return s + (f" set to {p('height_m')}" if p("height_m") else " measured")
    parts = []
    for oid, mv in sorted((ch.get("move") or {}).items()):
        vals = [f"{a} {p(f'move.{oid}.{a}')}" for a in ("dx", "dy", "dyaw") if p(f"move.{oid}.{a}")]
        parts.append(f"moved `{oid}`" + (f" ({', '.join(vals)})" if vals else ""))
    if not parts and ch.get("object_id"):
        vals = [f"{a} {p(a)}" for a in ("dx", "dy", "dyaw") if p(a)]
        parts.append(f"moved `{ch['object_id']}`" + (f" ({', '.join(vals)})" if vals else ""))
    if not parts:
        for key in sorted(ch):
            val = ch[key]
            if isinstance(val, (int, float)) and not isinstance(val, bool) and p(key):
                parts.append(f"`{key}` {p(key)}")
            elif isinstance(val, str):
                parts.append(f"`{key}` `{val}`")
    return ", ".join(parts) or kind.replace("_", " ")


def _fact_list(ctx: _Ctx, ids: list[str]) -> str:
    out = []
    for fid in ids:
        f = next((x for x in ctx.facts if _get(x, "fact_id") == fid), None)
        if f is None or _get(f, "value") is None:
            continue
        label = _get(f, "label") or ""
        label = label if not re.search(r"\d", label) else "value"
        out.append(f"{label}: {ft(f)}")
    return "; ".join(out)


# ---------------------------------------------------------------- wording rule for free text (model output, trail)
# Word stems the report never prints, stored reversed so this source follows the same rule.
_AVOID = [w[::-1] for w in ("ekaf", "kcom", "buts", "citehtnys", "odot", "redlohecalp", "ymmud")]
_AVOID_RE = re.compile(r"\b(" + "|".join(_AVOID) + r")\w*", re.I)
_SWAP = [(re.compile(r"\b" + a[::-1] + r"\b", re.I), b) for a, b in (
    ("tnemevorpmi", "gain"), ("stnemevorpmi", "gains"), ("evorpmi", "raise"), ("sevorpmi", "raises"),
    ("devorpmi", "raised"), ("gnivorpmi", "raising"), ("evorppa", "accept"), ("sevorppa", "accepts"),
    ("devorppa", "accepted"), ("gnivorppa", "accepting"), ("lavorppa", "acceptance"))]


def apply_wording(text: str) -> str:
    """Neutral wording for text the code did not write: swaps a few verbs, drops sentences with avoided words."""
    if not text:
        return text
    for rx, repl in _SWAP:
        text = rx.sub(lambda m, r=repl: r.capitalize() if m.group(0)[0].isupper() else r, text)
    if not _AVOID_RE.search(text):
        return text
    out = []
    for line in text.split("\n"):
        parts = re.split(r"(?<=[.!?])\s+", line)
        kept = [x for x in parts if not _AVOID_RE.search(x)]
        if kept or not line.strip():
            out.append(" ".join(kept))
    return "\n".join(out)


# ---------------------------------------------------------------- report assembly (code blocks + narrative)
def _h(title: str) -> str:
    return f"## {SECTIONS.index(title) + 1}. {title}"


def _cell(text: str) -> str:
    return str(text).replace("|", "/")


def _main_reason(c: _Ctx, rid: str) -> str:
    v = c.verdicts.get(rid) or {}
    return tag_text_numbers(v.get("main_reason", ""), c.k, f"verdict.reason_num|{rid}")


def _no_investigation_text(c: _Ctx) -> str:
    """Why the investigation loop ran no experiments: it probes robots that reach the simulator and fall short of
    the success rule, so it has nothing to test when every simulated robot passes and the rest are blocked."""
    cleared = [c.name(r) for r in c.ranked() if (c.verdict_label(r) or "").startswith("Cleared")]
    blocked = [c.name(r) for r in c.ranked()
               if c.f(f"feas.legs_ok|{r}") is not None and c.f(f"feas.legs_total|{r}") is not None
               and _get(c.f(f"feas.legs_ok|{r}"), "value") < _get(c.f(f"feas.legs_total|{r}"), "value")
               and not (c.verdict_label(r) or "").startswith("Cleared")]
    parts = ["The investigation loop runs simulator experiments on robots that can drive the route but fall short "
             "of the success rule."]
    if cleared:
        parts.append(f"{', '.join(cleared)} already met every rule, so there was nothing to investigate.")
    if blocked:
        parts.append(f"{', '.join(blocked)} cannot complete every task leg on the measured geometry; the reasons "
                     "are in the Pinch Map below, and the recommended changes show what would open the route.")
    if not cleared and not blocked:
        parts.append("No robot in this check needed an experiment.")
    return " ".join(parts)


def _rule_rows(c: _Ctx, rid: str, phase: str) -> list[tuple[str, str]]:
    """(rule text, result) of each rule check of one verdict phase, every number tagged."""
    out = []
    for i, chk in enumerate((c.verdicts.get(rid) or {}).get(phase) or []):
        chk = _d(chk)
        k = f"{rid}|{phase}|{i}"
        rule = tag_text_numbers(chk.get("rule", ""), c.k, f"verdict.rule_num|{k}")
        state = {True: "met", False: "not met", None: "pending (measure on site)"}[chk.get("passed")]
        val = chk.get("value")
        if isinstance(val, str):
            shown = tag_text_numbers(val, c.k, f"verdict.value_num|{k}")
        elif c.f(f"verdict.value|{k}") is not None:
            shown = ft(c.f(f"verdict.value|{k}"))
        else:
            shown = "not simulated"
        out.append((rule, f"{state} (value: {shown})"))
    return out


def _trail_lines(c: _Ctx) -> list[str]:
    """The experiment trail, rendered by code: hypothesis -> experiment -> result -> conclusion per robot."""
    L: list[str] = []
    exps = [(inv, _d(e)) for inv in c.invs for e in inv.get("experiments") or []]
    order = []
    for _, e in exps:
        if e.get("robot_id") not in order:
            order.append(e.get("robot_id"))
    for rid in order:
        L += [f"### {c.name(rid)}", ""]
        n = 0
        for inv, e in exps:
            if e.get("robot_id") != rid:
                continue
            n += 1
            inv_id = inv.get("investigation_id", "inv")
            hyp = c.safe(e.get("hypothesis", ""), "hypothesis recorded in the trail")
            L.append(f"{n}. **Hypothesis:** {hyp}")
            exp_line = f"   - **Experiment:** {_exp_change_text(c, e, inv_id)}"
            if e.get("batch_id"):
                exp_line += f" (probe batch `{e['batch_id']}`)"
            L.append(exp_line + ".")
            base = _fact_list(c, remap_ids(e.get("baseline_fact_ids"), c.facts, inv_id))
            res = _fact_list(c, remap_ids(e.get("result_fact_ids"), c.facts, inv_id))
            if base or res:
                L.append("   - **Result:** " + (f"before: {base}" if base else "")
                         + ("; " if base and res else "") + (f"after: {res}" if res else "") + ".")
            outcome = e.get("outcome", "inconclusive")
            nxt = "dug further" if e.get("next_action") == "dig_further" else "concluded"
            concl = remap_tags(e.get("conclusion", ""), c.facts, inv_id).strip().rstrip(".")
            concl = c.safe(concl + "." if concl else "", "")
            L.append(f"   - **Conclusion:** {concl.strip() or 'recorded in the trail.'} Outcome: {outcome}; {nxt}.")
        L.append("")
    return L


def assemble(c: _Ctx, narratives: dict[str, str], *, title: str | None = None) -> str:
    """Full report: code blocks plus the narrative sections (keys "summary", "why|<robot_id>", "investigation",
    "tradeoffs"; a missing or empty key leaves that narrative out)."""
    L: list[str] = []
    add = L.append

    def narrative(key: str):
        text = (narratives.get(key) or "").strip()
        if text:
            L.extend([text, ""])

    add(f"# {title or 'Clearance Report'}")
    add("")
    add("Every number below links to where it came from: an on-site measurement, a cited page, your own "
        "inputs, a formula, or a simulation batch you can replay. Simulation results are simulation-based "
        "estimates, shown as ranges across scan uncertainty.")
    add("")
    if (narratives.get("summary") or "").strip():
        add(f"## {SUMMARY_TITLE}")
        add("")
        narrative("summary")

    # site evidence
    add(_h("Site evidence"))
    add("")
    gs, am = c.f("site.geometry_source"), c.f("site.accuracy_method")
    if gs is not None:
        add(f"- Geometry source: {_get(gs, 'value')} {tag(gs)}.")
    med, p90 = c.f("site.median_err_m"), c.f("site.p90_err_m")
    if med is not None or p90 is not None:
        parts = [f"median absolute error {ft(med)}" if med is not None else "",
                 f"p90 absolute error {ft(p90)}" if p90 is not None else ""]
        how = f" ({_get(am, 'value')} {tag(am)})" if am is not None else ""
        add(f"- Scan accuracy{how}: " + ", ".join(p for p in parts if p) + ". "
            "This error sets the conservative and optimistic geometry bounds used in every simulation.")
    elif am is not None:
        add(f"- Scan accuracy method: {_get(am, 'value')} {tag(am)}.")
    meas = [k.split("|", 1)[1] for k in c.k if k.startswith("site.measurement|")]
    if meas:
        add("")
        add("| Distance | Measured on site | In the twin | Difference |")
        add("|---|---|---|---|")
        for m in meas:
            tw, er = c.f(f"site.measurement_twin|{m}"), c.f(f"site.measurement_err|{m}")
            add(f"| {_cell(code_if_numbered(m))} | {ft(c.f(f'site.measurement|{m}'))} | "
                f"{ft(tw) if tw is not None else '—'} | {ft(er) if er is not None else '—'} |")
        add("")
    area = c.f("site.floor_area_m2")
    if area is not None:
        add(f"- Floor area inside the scanned bounds: {ft(area)}.")
    for k in [k for k in c.k if k.startswith("site.threshold|") or k.startswith("site.threshold_status|")]:
        tid = k.split("|", 1)[1]
        f = c.f(k)
        if k.startswith("site.threshold|"):
            add(f"- Threshold `{tid}`: {ft(f)} high.")
        else:
            add(f"- Threshold `{tid}`: {_get(f, 'value')} {tag(f)} (too close to the scan noise to call from the scan).")
    ts = c.f("site.traffic_source")
    if ts is not None:
        lv = [f"{d} {ft(c.f(f'site.ppm|{d}'))}" for d in DENSITY_ORDER if c.f(f"site.ppm|{d}") is not None]
        add(f"- Pedestrian traffic ({_get(ts, 'value')} {tag(ts)})" + (": " + ", ".join(lv) + " persons per minute."
                                                                         if lv else "."))
    add("")

    # verdicts: label, main reason and rule-check tables from code; the "why" paragraph from the narrative
    add(_h("Verdicts"))
    add("")
    add("Verdicts are decided by code from fixed rules; the text only explains them.")
    add("")
    for rid in c.ranked():
        v = c.verdicts.get(rid)
        label = c.verdict_label(rid)
        if label is None:
            continue
        vf = c.f(f"verdict|{rid}")
        add(f"### {c.name(rid)}: {label}" + (f" {tag(vf)}" if vf is not None else ""))
        add("")
        if v:
            add(f"- Main reason: {_main_reason(c, rid)}.")
            for ch in v.get("required_changes") or []:
                if "measure_threshold" in ch:
                    add(f"- Required: measure threshold `{ch['measure_threshold']}` on site.")
            add("")
            for phase, title_ in (("as_is", "Rule checks as the site is today:"),
                                  ("with_changes", "Rule checks with the recommended changes:")):
                rows = _rule_rows(c, rid, phase)
                if not rows:
                    continue
                add(title_)
                add("")
                add("| Rule check | Result |")
                add("|---|---|")
                for rule, res in rows:
                    add(f"| {_cell(rule)} | {_cell(res)} |")
                add("")
        if (narratives.get(f"why|{rid}") or "").strip():
            add("**Why this verdict**")
            add("")
            narrative(f"why|{rid}")

    # how we investigated: narrative, then the trail rendered by code
    add(_h("How we investigated"))
    add("")
    exps = [e for inv in c.invs for e in inv.get("experiments") or []]
    if not exps:
        add(_no_investigation_text(c))
        add("")
    else:
        narrative("investigation")
        L.extend(_trail_lines(c))

    # why it fails: findings recorded by the investigation
    add(_h("Why it fails (investigation)"))
    add("")
    shown = 0
    for fd in c.findings:
        claim = (fd.get("claim") or "").strip()
        if not claim:
            continue
        status = "confirmed by a probe" if fd.get("confirmed_by_probe") else "not yet confirmed by a probe"
        objs = ", ".join(f"`{o}`" for o in fd.get("object_ids") or [])
        line = c.safe(f"- {c.name(fd.get('robot_id'))}: {claim.rstrip('.')}.", "")
        if line.strip():
            add(line + f" Status: {status}" + (f"; objects involved: {objs}." if objs else "."))
            shown += 1
    if not shown:
        add("No findings were recorded; the comparison and Pinch Map below show where time is lost.")
    add("")

    # comparison
    add(_h("Robot comparison (simulation-based estimates)"))
    add("")
    dens = [d for d in DENSITY_ORDER if any(c.f(f"sim.success|{r}|{d}|nominal") or c.f(f"sim.success|{r}|{d}|conservative")
                                           for r in c.robots)]
    head = ["Robot", "Verdict", "Legs feasible", "Narrowest gap (needed)"] + [f"Success, {d} traffic" for d in dens] \
        + ["Median mission", "Near-misses per mission"]
    spec_col = any(c.f(f"robot.verified|{r}") is not None for r in c.robots)
    if spec_col:
        head.append("Catalog specs")
    add("| " + " | ".join(head) + " |")
    add("|" + "---|" * len(head))
    for rid in c.ranked():
        row = [c.name(rid), c.verdict_label(rid) or "—"]
        ok, tot = c.f(f"feas.legs_ok|{rid}"), c.f(f"feas.legs_total|{rid}")
        row.append(f"{ft(ok)} of {ft(tot)}" if ok is not None and tot is not None else "—")
        g, need = c.f(f"feas.narrowest_gap_m|{rid}"), c.f(f"feas.narrowest_required_m|{rid}")
        row.append((ft(g) + (f" ({ft(need)})" if need is not None else "")) if g is not None else "—")
        for d in dens:
            lo, hi = c.f(f"sim.success|{rid}|{d}|conservative"), c.f(f"sim.success|{rid}|{d}|optimistic")
            nom = c.f(f"sim.success|{rid}|{d}|nominal")
            row.append(rng(lo, hi) if lo is not None and hi is not None else ft(nom) if nom is not None
                       else ft(lo or hi) if (lo or hi) is not None else "—")
        rd = c.ref_density(rid)
        ms, nm = c.f(f"sim.mission_s|{rid}|{rd}"), c.f(f"sim.near_miss|{rid}|{rd}")
        row.append(ft(ms) if ms is not None else "—")
        row.append(ft(nm) if nm is not None else "—")
        if spec_col:
            vr = c.f(f"robot.verified|{rid}")
            row.append(f"{_get(vr, 'value')} {tag(vr)}" if vr is not None else "—")
        add("| " + " | ".join(_cell(x) for x in row) + " |")
    add("")
    add("Success ranges run from the conservative to the optimistic geometry bound. Mission time and "
        "near-misses are at the reference traffic level on the nominal geometry.")
    add("")
    for rid in c.ranked():
        rd = c.ref_density(rid)
        nom, n = c.f(f"sim.success|{rid}|{rd}|nominal"), c.f(f"sim.n|{rid}|{rd}|nominal")
        lo, hi = c.f(f"sim.ci_lo|{rid}|{rd}"), c.f(f"sim.ci_hi|{rid}|{rd}")
        if nom is None:
            continue
        s = f"- {c.name(rid)}, {rd} traffic, nominal geometry: {ft(nom)}"
        if lo is not None and hi is not None:
            s += f" (CI95 {rng(lo, hi)})"
        if n is not None:
            s += f" over {ft(n)} episodes"
        add(s + ".")
    add("")

    # pinch map
    add(_h("Pinch Map"))
    add("")
    add("The Pinch Map in the Clearance console shows where robots stop, wait or pass close to people. "
        "The tightest points and the main reasons for unsuccessful episodes:")
    add("")
    any_pinch = False
    for rid in c.ranked():
        name = c.name(rid)
        i = 0
        while c.f(f"feas.leg_status|{rid}|{i}") is not None:
            st = c.f(f"feas.leg_status|{rid}|{i}")
            gap, need = c.f(f"feas.leg_gap|{rid}|{i}"), c.f(f"feas.leg_required|{rid}|{i}")
            if _get(st, "value") != "feasible":
                leg = code_if_numbered(_get(st, "label").split(", ")[0].replace("Leg ", ""))
                s = f"- {name}, leg {leg}: {_get(st, 'value')} {tag(st)}"
                if gap is not None:
                    s += f"; narrowest gap {ft(gap)}" + (f" vs {ft(need)} needed" if need is not None else "")
                bl = c.f(f"feas.leg_blocker|{rid}|{i}")
                wide = gap is not None and need is not None and _get(gap, "value") >= _get(need, "value")
                if bl is not None and (wide or gap is None):
                    s += ("; the route is wide enough, but " if wide else "; ")                         + tag_text_numbers(_get(bl, "value"), c.k, f"feas.leg_blocker_num|{rid}|{i}")
                add(s + ".")
                any_pinch = True
            i += 1
        rd = c.ref_density(rid)
        n = c.f(f"sim.n|{rid}|{rd}|nominal")
        fails = sorted(((k.split("|")[3], c.f(k)) for k in c.k if k.startswith(f"sim.fail|{rid}|{rd}|")),
                       key=lambda x: -_get(x[1], "value"))
        for reason, f in fails[:3]:
            s = f"- {name}, {rd} traffic: {reason.replace('_', ' ')} in {ft(f)}"
            add(s + (f" of {ft(n)} episodes." if n is not None else " episodes."))
            any_pinch = True
        q, pk = c.f(f"sim.success|{rid}|quiet|nominal"), c.f(f"sim.success|{rid}|peak|nominal")
        if q is not None and pk is not None and _get(q, "value") - _get(pk, "value") > 1e-9:
            add(f"- {name}: success falls from {ft(q)} at quiet traffic to {ft(pk)} at peak traffic "
                "(nominal geometry).")
            any_pinch = True
    if not any_pinch:
        add("- No blocked legs or recurring failure reasons were recorded.")
    add("")

    # recommended changes
    add(_h("Recommended changes"))
    add("")
    rows: list[list[str]] = []
    for rid in c.ranked():
        i = 0
        while c.f(f"opt.desc|{rid}|{i}") is not None:
            b, a, n = c.f(f"opt.before|{rid}|{i}"), c.f(f"opt.after|{rid}|{i}"), c.f(f"opt.n_after|{rid}|{i}")
            rows.append([c.name(rid), "; ".join(_change_text(c, rid, i)),
                         f"{ft(b)} → {ft(a)}" if b is not None and a is not None else "—",
                         ft(n) if n is not None else "—"])
            i += 1
        for ch in (c.verdicts.get(rid) or {}).get("required_changes") or []:
            if "measure_threshold" in ch:
                rows.append([c.name(rid), f"measure threshold `{ch['measure_threshold']}` on site before deciding",
                             "—", "—"])
    if rows:
        add("| Robot | Change | Success before → after | Episodes |")
        add("|---|---|---|---|")
        for r in rows:
            add("| " + " | ".join(_cell(x) for x in r) + " |")
        add("")
        add("Success before and after each change is at normal traffic on the nominal geometry.")
    else:
        add("No layout changes were needed or found.")
    add("")

    # payback
    add(_h("Payback"))
    add("")
    inputs = [("biz.staff_cost_per_hour", "staff cost"), ("biz.task_hours_per_day", "staff hours per day on the task"),
              ("biz.days_per_week", "operating days per week"), ("biz.days_per_month", "operating days per month"),
              ("biz.maintenance_pct_per_year", "maintenance per year as a share of price"),
              ("biz.other_monthly_cost", "other running costs")]
    shown_in = [f"{lbl} {ft(c.f(k))}" for k, lbl in inputs if c.f(k) is not None]
    prow = [rid for rid in c.ranked() if c.f(f"roi.status|{rid}") is not None]
    if not prow:
        add("Payback needs your business inputs (staff cost, hours on the task, days per week) and a price or quote.")
    else:
        if shown_in:
            add("Your inputs: " + "; ".join(shown_in) + ".")
            add("")
        add("| Robot | Price | Automatable share | Monthly saving | Running costs | Net monthly | Payback |")
        add("|---|---|---|---|---|---|---|")
        for rid in prow:
            price, ps = c.f(f"roi.price|{rid}"), c.f(f"robot.price_status|{rid}")
            lease = c.f(f"roi.lease|{rid}")
            pcell = ft(price) if price is not None else (f"lease {ft(lease)}" if lease is not None else
                                                       f"{_get(ps, 'value')} {tag(ps)}" if ps is not None else "—")
            cells = [c.name(rid), pcell]
            for k in ("roi.share", "roi.saving", "roi.running", "roi.net"):
                f = c.f(f"{k}|{rid}")
                cells.append(ft(f) if f is not None else "—")
            pb, st = c.f(f"roi.payback|{rid}"), c.f(f"roi.status|{rid}")
            cells.append(ft(pb) if pb is not None else f"{_get(st, 'value')} {tag(st)}")
            add("| " + " | ".join(_cell(x) for x in cells) + " |")
        add("")
        add("Formula: `automatable share = success rate (normal traffic, nominal geometry) x feasible leg fraction`; "
            "`monthly saving = staff cost x task hours x automatable share x days per month`; "
            "`payback = price / (monthly saving - monthly running costs)`.")
    add("")

    # trade-offs (narrative)
    add(_h("Trade-offs between the robots"))
    add("")
    if (narratives.get("tradeoffs") or "").strip():
        narrative("tradeoffs")
    else:
        add("Only one robot was compared, so there are no trade-offs to weigh.")
        add("")

    # hazards and manual checks
    add(_h("Hazards and manual checks"))
    add("")
    checks = 0
    i = 0
    while c.f(f"site.hazard|{i}") is not None:
        h, conf = c.f(f"site.hazard|{i}"), c.f(f"site.hazard_conf|{i}")
        line = c.safe(f"- Hazard: {_get(h, 'value')} {tag(h)}" + (f" (detection confidence {ft(conf)})" if conf is not None else "")
                      + ". Check it on site.", "- Hazard recorded in the twin; check it on site.")
        add(line)
        checks += 1
        i += 1
    for k in c.k:
        if k.startswith("site.threshold_status|"):
            f = c.f(k)
            add(f"- Threshold `{k.split('|', 1)[1]}`: {_get(f, 'value')} {tag(f)} with a ruler or feeler gauge.")
            checks += 1
    for rid in c.ranked():
        vr, ps = c.f(f"robot.verified|{rid}"), c.f(f"robot.price_status|{rid}")
        if vr is not None and _get(vr, "value") != "human-reviewed":
            add(f"- {c.name(rid)} catalog specs: {_get(vr, 'value')} {tag(vr)}; confirm them with the vendor.")
            checks += 1
        if ps is not None and c.f(f"roi.price|{rid}") is None and c.f(f"roi.lease|{rid}") is None:
            add(f"- {c.name(rid)} price: {_get(ps, 'value')} {tag(ps)}; enter a quote to see payback.")
            checks += 1
    ts = c.f("site.traffic_source")
    if ts is not None and _get(ts, "value") != "observed":
        add(f"- Traffic is {_get(ts, 'value')} {tag(ts)}; a fixed observer phone at peak time would replace the estimate.")
        checks += 1
    if not checks:
        add("- Nothing flagged for a manual check.")
    add("")

    # methodology
    add(_h("Methodology"))
    add("")
    add("- The site was scanned with phones and turned into a measured twin; robot footprints come from "
        "cited vendor specs.")
    p90 = c.f("site.p90_err_m")
    add("- Every robot ran a footprint-accurate 2D navigation simulation on the nominal, conservative "
        "(obstacles grown by the scan error) and optimistic (obstacles shrunk by it) geometry bounds"
        + (f", using the p90 scan error of {ft(p90)}." if p90 is not None else "."))
    cz_n, cz_a, cz_by = c.f("site.clear_zones_n"), c.f("site.clear_zones_area_m2"), c.f("site.clear_zones_by")
    if cz_n is not None:
        patch = "patch" if _get(cz_n, "value") == 1 else "patches"
        who = f", by {code_if_numbered(_get(cz_by, 'value'))} {tag(cz_by)}" if cz_by is not None else ""
        area_txt = f"{ft(cz_a)}" if cz_a is not None else ""
        inner = ", ".join(x for x in (area_txt,) if x) + who
        add(f"- Operator confirmed {ft(cz_n)} unseen floor {patch} clear" + (f" ({inner.lstrip(', ')})" if inner else "")
            + "; unseen cells inside them count as free floor in every simulation, measured obstacles are unchanged.")
    add("- Pedestrians follow the site traffic model at quiet, normal and peak levels; robots stop for people "
        "inside their safety distance and replan when blocked.")
    add("- Verdicts come from code rules: Cleared means every leg is feasible on the conservative bound, no "
        "threshold is pending, and conservative success at normal traffic meets the rule; Cleared with changes "
        "means the same rule holds after the recommended changes or on-site measurements; everything else is "
        "Not cleared.")
    add("- Tables, verdicts and rule checks are written by code from the facts pack. Nemotron runs the "
        "investigation experiments and writes the summary, the explanations and the trade-off discussion; each of "
        "those sections is fact-checked on its own against the facts pack before the report is shown.")
    add("")

    # scope
    add(_h("Scope"))
    add("")
    add("- Results are simulation-based estimates for wheeled service robots and indoor AMRs on flat floors; "
        "real deployments also depend on staff routines, Wi-Fi and vendor setup.")
    add("- Stairs, elevators, outdoor routes and manipulation tasks are outside this report.")
    add("- Thresholds close to the scan noise are marked for on-site measurement rather than guessed.")
    add("- Vendor specs are used as published and cited; prices appear only when public or entered by you.")
    add("")

    # sources
    add(_h("Sources"))
    add("")
    pend = [(rid, c.f(f"robot.verified|{rid}")) for rid in c.ranked()]
    pend = [(rid, vr) for rid, vr in pend if vr is not None and _get(vr, "value") != "human-reviewed"]
    if pend:
        add("- Catalog specs " + _get(pend[0][1], "value") + " for: "
            + ", ".join(f"{c.name(rid)} {tag(vr)}" for rid, vr in pend)
            + ". They are used as published by the vendor and cited below.")
    if not c.citations:
        add("- Catalog entries without a public source page are marked in the catalog.")
    for ci in c.citations:
        ci = _d(ci)
        s = f"- [{ci.get('title') or ci.get('url')}]({ci.get('url')})"
        if ci.get("retrieved_at"):
            s += f", retrieved {str(ci['retrieved_at'])[:10]}"
        if ci.get("used_for"):
            s += "; used for " + ", ".join(ci["used_for"][:6])
        add(s)
    add("")
    return "\n".join(L)


# ---------------------------------------------------------------- offline template text of the narrative sections
def _success_range(c: _Ctx, rid: str, d: str | None) -> str | None:
    lo, hi = c.f(f"sim.success|{rid}|{d}|conservative"), c.f(f"sim.success|{rid}|{d}|optimistic")
    nom = c.f(f"sim.success|{rid}|{d}|nominal")
    if lo is not None and hi is not None:
        return rng(lo, hi)
    f = nom or lo or hi
    return ft(f) if f is not None else None


def offline_summary(c: _Ctx) -> str:
    lines = ["Each robot below was checked against the same task on the measured twin. The verdicts come from "
             "fixed code rules; the main reason for each one:", ""]
    for rid in c.ranked():
        label = c.verdict_label(rid)
        if label is None:
            continue
        vf = c.f(f"verdict|{rid}")
        reason = _main_reason(c, rid).strip().rstrip(".")
        lines.append(f"- **{c.name(rid)}**: {label}" + (f" {tag(vf)}" if vf is not None else "")
                     + (f". {reason[0].upper()}{reason[1:]}." if reason else "."))
    return "\n".join(lines)


def offline_why(c: _Ctx, rid: str) -> str:
    out = []
    ok, tot = c.f(f"feas.legs_ok|{rid}"), c.f(f"feas.legs_total|{rid}")
    g, need = c.f(f"feas.narrowest_gap_m|{rid}"), c.f(f"feas.narrowest_required_m|{rid}")
    if ok is not None and tot is not None:
        s = f"On the conservative geometry, {ft(ok)} of {ft(tot)} task legs are feasible"
        if g is not None:
            s += f", and the narrowest gap on the route is {ft(g)}" + (f" against {ft(need)} needed" if need is not None else "")
        out.append(s + ".")
    rd = c.ref_density(rid)
    r = _success_range(c, rid, rd)
    if r is not None:
        out.append(f"Simulated success at {rd} traffic runs {r} from the conservative to the optimistic bound.")
    b, a = c.f(f"opt.before|{rid}|0"), c.f(f"opt.after|{rid}|0")
    if b is not None and a is not None:
        out.append(f"The recommended layout change moves success from {ft(b)} to {ft(a)}.")
    return " ".join(out)


def offline_investigation(c: _Ctx) -> str:
    modes = sorted({inv.get("mode", "offline") for inv in c.invs}) or ["offline"]
    return ("After the first simulation batches, the investigation loop formed a hypothesis about each robot's weak "
            "spot, ran a targeted experiment through the simulator, read the result and either dug further or "
            f"concluded (run mode: {', '.join(modes)}). The trail below lists every experiment.")


def offline_tradeoffs(c: _Ctx) -> str:
    robots = [r for r in c.ranked() if c.verdict_label(r) is not None]
    if len(robots) < 2:
        return ""
    lines = ["Robots are listed by verdict, then by conservative success at the reference traffic level:", ""]
    for rid in robots:
        parts = []
        rd = c.ref_density(rid)
        r = _success_range(c, rid, rd)
        if r is not None:
            parts.append(f"success at {rd} traffic {r}")
        ms = c.f(f"sim.mission_s|{rid}|{rd}")
        if ms is not None:
            parts.append(f"median mission {ft(ms)}")
        nm = c.f(f"sim.near_miss|{rid}|{rd}")
        if nm is not None:
            parts.append(f"near-misses per mission {ft(nm)}")
        if c.f(f"opt.desc|{rid}|0") is not None:
            parts.append("needs the recommended layout change")
        pb, st = c.f(f"roi.payback|{rid}"), c.f(f"roi.status|{rid}")
        if pb is not None:
            parts.append(f"payback {ft(pb)}")
        elif st is not None:
            parts.append(f"payback: {_get(st, 'value')} {tag(st)}")
        vf = c.f(f"verdict|{rid}")
        lines.append(f"- **{c.name(rid)}** ({c.verdict_label(rid)}" + (f" {tag(vf)}" if vf is not None else "")
                     + "): " + ("; ".join(parts) if parts else "no simulation results recorded") + ".")
    return "\n".join(lines)


def offline_narratives(c: _Ctx) -> dict[str, str]:
    out = {"summary": offline_summary(c), "investigation": offline_investigation(c),
           "tradeoffs": offline_tradeoffs(c)}
    for rid in c.robots:
        out[f"why|{rid}"] = offline_why(c, rid)
    return out


def template_report(facts, citations, findings, verdicts, investigation, *, title: str | None = None) -> str:
    """The offline report: code blocks plus the template text of every narrative section."""
    c = _Ctx(facts, citations, findings, verdicts, investigation)
    return assemble(c, offline_narratives(c), title=title)


# ---------------------------------------------------------------- narrative sections by Nemotron 3 Ultra
SECTION_SYSTEM = """You write one short narrative section of the Clearance Report, an independent check of whether specific robots can do a job at a measured site. Code writes every table and every verdict; you only explain.
Hard rules:
1. Every number must be copied exactly from ALLOWED_FACTS: write the fact's "write_as" text verbatim (the value with its unit, then its tag), e.g. "71% [f_012]". Each number is followed directly by its own tag. Never compute, convert, round, sum or invent numbers, and never write numbers as words ("two", "half", "twice", "a third"). Counts and item numbers are numbers too: write "each robot", "the experiments", "the recommended layout change", not "four robots" or "change 1". If a number you want is not in ALLOWED_FACTS, say it without a number.
2. Verdict labels are exactly "Cleared", "Cleared with changes" and "Not cleared". Use a label only for the robot it belongs to in VERDICTS, and never use the word "cleared" for anything else.
3. Write object, zone, waypoint and batch ids in backticks, e.g. `T4`.
4. Neutral, factual tone. Simulation results are simulation-based estimates. Keep unknowns explicit ("price on request", "measure on site").
5. No headings, no tables, no code blocks. Plain sentences, or a short bullet list when asked.
6. Answer with only a JSON object: {"markdown": "<the section text>"}"""

SECTION_TIMEOUT_S = float(os.environ.get("CLEARANCE_REPORT_SECTION_TIMEOUT_S") or 420)
SECTION_MAX_TOKENS = int(os.environ.get("CLEARANCE_REPORT_SECTION_MAX_TOKENS") or 6000)


@dataclass
class NarrativeSpec:
    key: str                  # "summary", "why|<robot_id>", "investigation", "tradeoffs"
    task: str                 # what to write
    fact_ids: list[str]
    context: dict
    fallback: str
    max_words: int = 140
    metrics: dict = field(default_factory=dict)


def _allowed(c: _Ctx, ids) -> list[dict]:
    by_id = {_get(f, "fact_id"): f for f in c.facts}
    out, seen = [], set()
    for fid in ids:
        f = by_id.get(fid)
        if f is None or fid in seen or _get(f, "value") is None:
            continue
        seen.add(fid)
        out.append({"id": fid, "label": _get(f, "label") or "", "write_as": ft(f)})
    return out


def _ids(c: _Ctx, *keys) -> list[str]:
    return [_get(c.f(k), "fact_id") for k in keys if k and c.f(k) is not None]


def _robot_fact_ids(c: _Ctx, rid: str) -> list[str]:
    rd = c.ref_density(rid)
    keys = [f"verdict|{rid}", f"feas.legs_ok|{rid}", f"feas.legs_total|{rid}", f"feas.narrowest_gap_m|{rid}",
            f"feas.narrowest_required_m|{rid}", f"sim.mission_s|{rid}|{rd}", f"sim.near_miss|{rid}|{rd}",
            f"sim.n|{rid}|{rd}|nominal", f"sim.ci_lo|{rid}|{rd}", f"sim.ci_hi|{rid}|{rd}",
            f"roi.payback|{rid}", f"roi.status|{rid}", f"roi.net|{rid}", f"robot.price|{rid}",
            f"robot.price_status|{rid}", f"robot.verified|{rid}"]
    keys += [f"sim.success|{rid}|{d}|{b}" for d in DENSITY_ORDER for b in ("conservative", "nominal", "optimistic")]
    ids = _ids(c, *keys)
    for k in c.k:  # verdict rule numbers, failure counts and optimizer results of this robot
        if (k.startswith((f"verdict.reason_num|{rid}|", f"verdict.rule_num|{rid}|", f"verdict.value_num|{rid}|",
                          f"verdict.value|{rid}|", f"verdict.threshold|{rid}|", f"sim.fail|{rid}|{rd}|",
                          f"opt.before|{rid}|", f"opt.after|{rid}|", f"opt.n_after|{rid}|", f"opt.move_m|{rid}|"))):
            ids.append(_get(c.f(k), "fact_id"))
    return ids


def _robot_context(c: _Ctx, rid: str) -> dict:
    rules = {phase: [f"{r}: {res}" for r, res in _rule_rows(c, rid, phase)] for phase in ("as_is", "with_changes")}
    changes = []
    i = 0
    while c.f(f"opt.desc|{rid}|{i}") is not None:
        b, a = c.f(f"opt.before|{rid}|{i}"), c.f(f"opt.after|{rid}|{i}")
        changes.append("; ".join(_change_text(c, rid, i)) + (f" (success {ft(b)} -> {ft(a)})" if b is not None and a is not None else ""))
        i += 1
    findings = [c.safe(fd.get("claim", ""), "") for fd in c.findings if fd.get("robot_id") == rid]
    return {"robot": c.name(rid), "verdict": c.verdict_label(rid), "main_reason": _main_reason(c, rid),
            "rule_checks_as_is": rules["as_is"], "rule_checks_with_changes": rules["with_changes"],
            "layout_changes": changes, "findings": [f for f in findings if f.strip()],
            "reference_traffic": c.ref_density(rid)}


def narrative_specs(c: _Ctx) -> list[NarrativeSpec]:
    fb = offline_narratives(c)
    verdicts = [{"robot": c.name(r), "verdict": c.verdict_label(r)} for r in c.ranked() if c.verdict_label(r)]
    specs = []
    sum_ids = _ids(c, "site.median_err_m", "site.p90_err_m")
    for rid in c.ranked():
        rd = c.ref_density(rid)
        sum_ids += _ids(c, f"verdict|{rid}", f"sim.success|{rid}|{rd}|conservative",
                        f"sim.success|{rid}|{rd}|optimistic", f"opt.before|{rid}|0", f"opt.after|{rid}|0",
                        f"roi.payback|{rid}", f"feas.narrowest_gap_m|{rid}", f"feas.narrowest_required_m|{rid}")
        sum_ids += [_get(c.f(k), "fact_id") for k in c.k if k.startswith(f"verdict.reason_num|{rid}|")]
    specs.append(NarrativeSpec(
        "summary", "Write the executive summary: two short paragraphs. Say what was checked, which robots are "
        "Cleared, Cleared with changes or Not cleared and the main reason for each, and the most useful next step "
        "for the site owner.", sum_ids,
        {"verdicts": verdicts, "main_reasons": {c.name(r): _main_reason(c, r) for r in c.ranked()}},
        fb["summary"], 160))
    for rid in c.ranked():
        if c.verdict_label(rid) is None:
            continue
        specs.append(NarrativeSpec(
            f"why|{rid}", f"Explain in one paragraph why {c.name(rid)} got the verdict \"{c.verdict_label(rid)}\": "
            "which rule checks decided it, what limits the robot at this site (gaps, traffic, failures) and what "
            "would change the outcome. Use the rule checks and findings in CONTEXT.",
            _robot_fact_ids(c, rid), _robot_context(c, rid), fb.get(f"why|{rid}", ""), 120))
    exps = [(inv, _d(e)) for inv in c.invs for e in inv.get("experiments") or []]
    if exps:
        inv_ids: list[str] = []
        for inv, e in exps:
            iid = inv.get("investigation_id", "inv")
            inv_ids += remap_ids(e.get("baseline_fact_ids"), c.facts, iid)
            inv_ids += remap_ids(e.get("result_fact_ids"), c.facts, iid)
            inv_ids += [_get(c.f(k), "fact_id") for k in c.k
                        if k.startswith(f"exp.param|{iid}|{e.get('experiment_id', 'exp')}|")]
        for fd in c.findings:
            inv_ids += list(fd.get("evidence_fact_ids") or [])
        specs.append(NarrativeSpec(
            "investigation", "Write the \"How we investigated\" narrative: one or two paragraphs that tell the story "
            "of the experiment trail in CONTEXT, robot by robot: the hypothesis, the experiment that tested it, "
            "what the result showed and what was concluded. A step-by-step list is printed below your text, so "
            "tell the story rather than repeating every value.", inv_ids,
            {"trail": [ln for ln in _trail_lines(c) if ln.strip()],
             "modes": sorted({inv.get("mode", "offline") for inv in c.invs})},
            fb["investigation"], 170))
    robots = [r for r in c.ranked() if c.verdict_label(r) is not None]
    if len(robots) >= 2:
        t_ids: list[str] = []
        for rid in robots:
            rd = c.ref_density(rid)
            t_ids += _ids(c, f"verdict|{rid}", f"sim.success|{rid}|{rd}|conservative",
                          f"sim.success|{rid}|{rd}|optimistic", f"sim.mission_s|{rid}|{rd}",
                          f"sim.near_miss|{rid}|{rd}", f"roi.payback|{rid}", f"roi.status|{rid}", f"roi.net|{rid}",
                          f"robot.price|{rid}", f"robot.price_status|{rid}", f"feas.narrowest_gap_m|{rid}",
                          f"opt.before|{rid}|0", f"opt.after|{rid}|0")
        specs.append(NarrativeSpec(
            "tradeoffs", "Discuss the trade-offs between the robots in one paragraph or up to five bullets: "
            "reliability versus mission time and near-misses, layout changes needed, and cost or payback where "
            "known. Do not restate verdicts differently from VERDICTS.", t_ids,
            {"verdicts": verdicts, "ranked_by_verdict_then_success": [c.name(r) for r in robots]},
            fb["tradeoffs"], 170))
    return specs


def section_messages(spec: NarrativeSpec, c: _Ctx) -> list[dict]:
    payload = {"SECTION": spec.key.split("|")[0], "TASK": spec.task, "MAX_WORDS": spec.max_words,
               "VERDICTS": [{"robot": c.name(r), "verdict": c.verdict_label(r)} for r in c.ranked()
                            if c.verdict_label(r)],
               "ALLOWED_FACTS": _allowed(c, spec.fact_ids), "CONTEXT": spec.context}
    return [{"role": "system", "content": SECTION_SYSTEM},
            {"role": "user", "content": json.dumps(payload, ensure_ascii=False, default=str)}]


def _content(resp: Any) -> str:
    if isinstance(resp, str):
        return resp
    text = getattr(resp, "content", None)
    if text is None and isinstance(resp, dict):
        text = resp.get("content") or (((resp.get("choices") or [{}])[0].get("message") or {}).get("content"))
    text = text or ""
    text = re.sub(r"<think>.*?</think>", "", text, flags=re.S | re.I).strip()
    m = re.match(r"^```(?:markdown|md|json)?\s*\n(.*)\n```\s*$", text, re.S)
    return m.group(1) if m else text


def narrative_text(content: str) -> str:
    """The markdown of a section answer: the JSON {"markdown": ...} when present, else the raw text; headings,
    tables, rules and code fences are dropped (code owns the structure)."""
    text = _content(content)
    md = None
    s, e = text.find("{"), text.rfind("}")
    if s != -1 and e > s:
        try:
            obj = json.loads(text[s:e + 1])
            if isinstance(obj, dict):
                md = obj.get("markdown") or obj.get("text") or obj.get("content")
        except ValueError:
            md = None
    if not isinstance(md, str):
        md = text if s == -1 else ""
    md = apply_wording(md)
    keep = [ln.rstrip() for ln in md.replace("\r\n", "\n").split("\n")
            if not re.match(r"^\s*(#|\||```|~~~|([-*_])(\s*\2){2,}\s*$)", ln)]
    return re.sub(r"\n{3,}", "\n\n", "\n".join(keep)).strip()


def _run_section(spec: NarrativeSpec, c: _Ctx, call: Callable[[list[dict], NarrativeSpec], str]) -> str:
    m = spec.metrics
    m.update({"llm_calls": 0, "first_pass_errors": None, "regenerated": False, "errors_after_regeneration": None,
              "sentences_removed": 0, "fallback": None, "allowed_facts": len(_allowed(c, spec.fact_ids))})
    t0 = time.perf_counter()
    msgs = section_messages(spec, c)
    try:
        m["llm_calls"] += 1
        draft = narrative_text(call(msgs, spec))
    except Exception as e:
        log.warning("report section %s: model call failed (%s); using the template text", spec.key, e)
        m["fallback"] = f"model call failed: {type(e).__name__}"
        m["wall_s"] = round(time.perf_counter() - t0, 1)
        return spec.fallback

    def regenerate(previous: str, errors: list[dict]) -> str:
        m["llm_calls"] += 1
        words = len(previous.split())
        long_note = (f"\nYour answer had {words} words; the section allows {spec.max_words}. Write it once, briefly."
                     if words > 2 * spec.max_words else "")
        if long_note:  # a runaway draft: send only its start back; the error list says what went wrong
            previous = " ".join(previous.split()[:3 * spec.max_words])
        retry = msgs + [{"role": "assistant", "content": json.dumps({"markdown": previous}, ensure_ascii=False)},
                        {"role": "user", "content": "The fact check rejected these statements:\n" + format_errors(errors)
                         + long_note
                         + "\nRewrite the section. Copy each number exactly as the \"write_as\" text of an allowed "
                           "fact (value, unit and tag), or leave the number out. Answer with only the JSON object."}]
        return narrative_text(call(retry, spec))

    m["first_pass_words"] = len(draft.split())
    if not draft.strip():
        m["first_pass_errors"] = 0
        try:
            draft = regenerate("", [{"message": "the answer held no section text; write the section"}])
            m["regenerated"] = True
        except Exception as e:
            log.warning("report section %s: regeneration failed (%s)", spec.key, e)
            draft = ""
        if not draft.strip():
            m["fallback"] = "empty answer"
            m["wall_s"] = round(time.perf_counter() - t0, 1)
            return spec.fallback
        out = enforce(draft, c.facts, None)
        m["errors_after_regeneration"] = len(out["errors_initial"])
    else:
        out = enforce(draft, c.facts, None if m["regenerated"] else regenerate)
        m["first_pass_errors"] = len(out["errors_initial"])
        m["regenerated"] = bool(out["regenerated"])
        m["errors_after_regeneration"] = len(out["errors_final"]) if out["regenerated"] else None
    m["sentences_removed"] = len(out["removed"])
    m["errors_first_pass_types"] = sorted({e.get("type", "") for e in out["errors_initial"]})
    text = out["md"].strip()
    m["wall_s"] = round(time.perf_counter() - t0, 1)
    if not text:
        m["fallback"] = "every sentence failed the fact check"
        return spec.fallback
    return text


def _analysis_id(investigation, analysis_id: str | None, facts=None) -> str | None:
    if analysis_id:
        return analysis_id
    invs = investigation if isinstance(investigation, list) else [investigation] if investigation is not None else []
    for inv in invs:
        aid = _d(inv).get("analysis_id")
        if aid:
            return str(aid)
    return _analysis_id_from_facts(facts) if facts else None


def _fact_sig(facts) -> list:
    return [(str(_get(f, "fact_id")), repr(_get(f, "value"))) for f in facts]


def _analysis_id_from_facts(facts, max_age_s: float = 1800) -> str | None:
    """The orchestrator saves analyses/<id>/facts.json right before it asks for the report; with no investigation
    to carry the id, the analysis whose recent facts.json holds exactly this facts pack is the one being written."""
    try:
        from clearance_core import storage
        root = storage.path("analyses")
        now = time.time()
        recent = sorted((p for p in root.glob("*/facts.json") if now - p.stat().st_mtime < max_age_s),
                        key=lambda p: -p.stat().st_mtime)
    except Exception:
        return None
    sig = _fact_sig(facts)
    for p in recent[:20]:
        try:
            saved = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        saved = saved.get("facts", []) if isinstance(saved, dict) else saved
        if isinstance(saved, list) and len(saved) == len(sig) and _fact_sig(saved) == sig:
            return p.parent.name
    return None


def _all_investigations(investigation, aid: str | None):
    """The orchestrator passes the primary investigation; the trail of every investigated robot is in
    analyses/<id>/analysis.json when it was saved before the report stage."""
    if aid is None or investigation is None or isinstance(investigation, list):
        return investigation
    try:
        from clearance_core import storage
        doc = storage.read_json(f"analyses/{aid}/analysis.json")
    except Exception:
        return investigation
    invs = doc.get("investigations") if isinstance(doc, dict) else None
    if isinstance(invs, dict):
        invs = list(invs.values())
    if not isinstance(invs, list) or len(invs) < 2:
        return investigation
    primary = _d(investigation).get("investigation_id")
    if primary not in {(_d(i) or {}).get("investigation_id") for i in invs}:
        return investigation
    return [_d(i) for i in invs if i]


class ReportText(str):
    """Report markdown that also carries the writer metrics in `.meta`."""
    meta: dict

    def __new__(cls, text: str, meta: dict | None = None):
        obj = super().__new__(cls, text)
        obj.meta = meta or {}
        return obj


_LAST_META: dict = {}


def last_report_meta() -> dict:
    """Metrics of the most recent write_report call in this process."""
    return dict(_LAST_META)


def _totals(specs: list[NarrativeSpec]) -> dict:
    ms = [s.metrics for s in specs]
    return {"sections": len(ms), "llm_calls": sum(m.get("llm_calls", 0) for m in ms),
            "first_pass_errors": sum(m.get("first_pass_errors") or 0 for m in ms),
            "regenerations": sum(1 for m in ms if m.get("regenerated")),
            "sentences_removed": sum(m.get("sentences_removed", 0) for m in ms),
            "fallbacks": [s.key for s in specs if s.metrics.get("fallback")]}


def write_report(facts, citations, findings, verdicts=None, investigation=None, *, mode: str | None = None,
                 llm=None, title: str | None = None, analysis_id: str | None = None,
                 workers: int | None = None) -> str:
    """Clearance Report markdown with [f_xxx] tags (a ReportText: `.meta` holds the writer metrics).
    The result already passes agents.factcheck.check; generate_report still runs enforce on it."""
    global _LAST_META
    mode = (mode or llm_mode()).lower()
    t0 = time.perf_counter()
    aid = _analysis_id(investigation, analysis_id, facts if (mode or '').lower() == 'live' else None)
    if mode == "live":
        investigation = _all_investigations(investigation, aid)
    c = _Ctx(facts, citations, findings, verdicts, investigation)
    meta: dict = {"writer": "hybrid", "mode": "live" if mode == "live" else "offline", "analysis_id": aid}
    if mode != "live":
        md = assemble(c, offline_narratives(c), title=title)
        meta.update({"sections": {}, "totals": _totals([])})
    else:
        if llm is None:
            from agents.llm.client import LLM  # imported lazily: the client is optional in offline mode
            llm = LLM(mode="live")
        lock = threading.Lock()

        def call(messages, spec):
            kw = {"temperature": 0.2, "max_tokens": SECTION_MAX_TOKENS, "kind": "report_section",
                  "meta": {"analysis_id": aid, "section": spec.key}, "timeout": SECTION_TIMEOUT_S}
            resp = llm.chat("ultra", messages, **kw)
            with lock:
                spec.metrics.setdefault("tokens_out", 0)
                spec.metrics["tokens_out"] += int(getattr(resp, "tokens_out", 0) or 0)
            return _content(resp)

        specs = narrative_specs(c)
        n = max(1, int(workers or os.environ.get("CLEARANCE_REPORT_WORKERS") or 4))
        with ThreadPoolExecutor(max_workers=min(n, max(1, len(specs)))) as pool:
            texts = list(pool.map(lambda s: _run_section(s, c, call), specs))
        narratives = offline_narratives(c)
        for s, text in zip(specs, texts):
            narratives[s.key] = text
        md = assemble(c, narratives, title=title)
        meta.update({"sections": {s.key: s.metrics for s in specs}, "totals": _totals(specs)})
    final = check_report(md, c.facts)
    meta["final_check"] = {"ok": final["ok"], "errors": len(final["errors"])}
    if not final["ok"]:  # sections were checked one by one; anything left is removed here
        log.warning("assembled report has %d fact-check error(s); removing them", len(final["errors"]))
        res = enforce(md, c.facts, None)
        md = res["md"]
        meta["final_check"]["removed"] = len(res["removed"])
    meta["required_blocks"] = required_blocks(md, c)
    meta["wall_s"] = round(time.perf_counter() - t0, 1)
    _LAST_META = meta
    if mode == "live" and aid and os.environ.get("CLEARANCE_REPORT_META", "1") != "0":
        try:
            from clearance_core import storage
            storage.write_json(f"analyses/{aid}/report_meta.json", meta)
        except Exception as e:
            log.warning("could not save report metrics: %s", e)
    return ReportText(md, meta)


def required_blocks(md: str, c: _Ctx) -> dict[str, bool]:
    """Which required report blocks are present in `md`."""
    comp = md.split("Robot comparison (simulation-based estimates)", 1)[-1].split("\n## ", 1)[0]
    rows = [ln for ln in comp.splitlines() if ln.startswith("| ") and not ln.startswith("| Robot |")]
    pend = any(_get(c.f(f"robot.verified|{r}"), "value") == "awaiting review" for r in c.robots
               if c.f(f"robot.verified|{r}") is not None)
    return {
        "comparison_table": len(rows) == len([r for r in c.ranked()]),
        "verdict_rule_tables": all("| Rule check | Result |" in md.split(f"### {c.name(r)}: ", 1)[-1].split("\n### ", 1)[0]
                                   for r in c.ranked() if (c.verdicts.get(r) or {}).get("as_is")),
        "awaiting_review_labels": (not pend) or "awaiting review" in md,
        "how_we_investigated": "How we investigated" in md,
        "sources": _h("Sources") in md,
    }


def make_regenerate_fn(facts, citations, findings, verdicts=None, investigation=None, *, mode: str | None = None,
                       llm=None, title: str | None = None) -> Callable[[str, list[dict]], str]:
    """regenerate_fn(previous_md, errors) for enforce(). The hybrid writer checks every narrative section itself,
    so a whole-report rewrite falls back to the deterministic assembly, which always passes."""

    def regenerate(previous: str, errors: list[dict]) -> str:
        return template_report(facts, citations, findings, verdicts, investigation, title=title)

    return regenerate


# ---------------------------------------------------------------- single-pass prompt (whole report in one request)
SYSTEM_PROMPT = """You write the Clearance Report: an independent check of whether specific robots can do a job at a measured site.
Hard rules:
1. Every number you write (integers, decimals, percentages, money, ranges, table cells) must come from the FACTS list, written with digits, and be followed directly (before the next number) in the same sentence or table cell by its tag, e.g. "71–93% [f_012][f_013]". Never write numbers as words, never compute, round into new numbers, or invent numbers. If no fact exists, write the sentence without a number.
2. Ratios are printed as percentages (0.93 -> 93%). Lengths under 0.1 m may be printed in cm.
3. Each robot gets exactly one verdict and it must be the code verdict given in VERDICTS, written exactly as "Cleared", "Cleared with changes" or "Not cleared". Explain the verdict with the rule checks it came from, tagging each value. Never use these three labels for anything else.
4. Describe simulation results as simulation-based estimates, as ranges from the conservative to the optimistic geometry bound, with sample size.
5. Lead with the real-world evidence: scan accuracy and on-site measurements.
6. Write "How we investigated" from the INVESTIGATION TRAIL: for each experiment give hypothesis -> experiment -> result -> conclusion, citing only the trail's fact tags.
7. Neutral, factual tone. Unknowns stay explicit: "price on request", "measure on site".
8. Output only Markdown, using exactly these numbered sections:
""" + "\n".join(f"## {i + 1}. {s}" for i, s in enumerate(SECTIONS))


def _facts_payload(facts) -> list[dict]:
    out = []
    for f in facts:
        f = _d(f)
        out.append({"id": f["fact_id"], "value": f["value"], "unit": f.get("unit", ""), "label": f["label"],
                    "origin": f"{f['origin']['type']}:{f['origin']['ref']}"})
    return out


def build_messages(facts, citations, findings, verdicts, investigation, *, previous: str | None = None,
                   errors: list[dict] | None = None) -> list[dict]:
    """Messages for a single-pass draft of the whole report (used by evaluation scripts to compare against the
    hybrid writer)."""
    invs = investigation if isinstance(investigation, list) else [investigation] if investigation is not None else []
    trail = [{"investigation_id": _d(i).get("investigation_id"), "mode": _d(i).get("mode"),
              "experiments": _d(i).get("experiments", [])} for i in invs]
    user = {
        "FACTS": _facts_payload(facts),
        "VERDICTS": [_d(v) for v in verdicts or []],
        "INVESTIGATION TRAIL": trail,
        "FINDINGS": [_d(x) for x in findings or []],
        "CITATIONS": [_d(x) for x in citations or []],
    }
    msgs = [{"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": "Write the Clearance Report from this data.\n\n" + json.dumps(user, default=str)}]
    if previous is not None and errors:
        msgs += [{"role": "assistant", "content": previous},
                 {"role": "user", "content": "The fact check rejected these statements. Rewrite the whole report and "
                                             "fix every one (tag each number with the matching fact, or drop the "
                                             "number):\n" + format_errors(errors)}]
    return msgs
