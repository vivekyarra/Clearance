"""Human review helper for catalog entries (PLAN 7.6 step 5).

  python -m agents.catalog_builder.review <robot_id>                 show fields + sources
  python -m agents.catalog_builder.review <robot_id> --approve [--reviewer NAME]
  python -m agents.catalog_builder.review <robot_id> --set payload_kg=30 --url URL --quote "..."
  python -m agents.catalog_builder.review <robot_id> --clear max_slope_deg
  python -m agents.catalog_builder.review <robot_id> --unapprove
  python -m agents.catalog_builder.review --list
Only reviewed robots (verified_by_human=true) are loaded for simulation by catalog.load_catalog().
"""
from __future__ import annotations

import argparse
import sys

from clearance_core.schemas import FieldSource, RobotSpec

from agents.catalog_builder import catalog

EDITABLE = set(catalog.NUMERIC_FIELDS) | {"min_turn_radius_m"}


def describe(spec: RobotSpec) -> str:
    notes = catalog.read_notes(spec)
    rows = [f"{spec.robot_id}  [{spec.robot_class}]  {spec.vendor} {spec.model}",
            f"verified_by_human={spec.verified_by_human}  agent_quote_validated={notes.get('agent_quote_validated')}"]
    for f in ["footprint_polygon_m"] + catalog.NUMERIC_FIELDS:
        v = getattr(spec, f)
        src = spec.fields_sources.get(f)
        shown = "null" if v is None else v
        rows.append(f"  {f:26s} {shown!s:40.40s} " + (f'"{src.quote[:70]}"  <{src.url}>' if src else ""))
    p = spec.price
    rows.append(f"  {'price':26s} {p.value if p.value is not None else p.note!s:40.40s} <{p.source_url or ''}>")
    if spec.footprint_approx:
        rows.append("  (footprint is an approximate rectangle)")
    for c in notes.get("conflicts", []):
        rows.append(f"  conflict {c['field']}: kept {c['kept']} ({c['kept_url']}), other {c['other']} ({c['other_url']})")
    return "\n".join(rows)


def approve(spec: RobotSpec, reviewer: str, *, allow_unsourced: bool = False) -> RobotSpec:
    missing = catalog.unsourced_fields(spec)
    if missing and not allow_unsourced:
        raise ValueError(f"fields without a source: {missing}")
    spec.verified_by_human = True
    notes = catalog.read_notes(spec)
    notes["review_status"] = "reviewed"
    notes["reviewed_by"] = reviewer
    notes["reviewed_at"] = catalog.now_iso()
    return catalog.write_notes(spec, notes)


def unapprove(spec: RobotSpec) -> RobotSpec:
    spec.verified_by_human = False
    notes = catalog.read_notes(spec)
    notes["review_status"] = "awaiting review"
    notes.pop("reviewed_by", None)
    notes.pop("reviewed_at", None)
    return catalog.write_notes(spec, notes)


def set_field(spec: RobotSpec, field: str, value: float, url: str, quote: str, reviewer: str) -> RobotSpec:
    """Human edit. Still requires a source (url + quote); any edit resets the review flag."""
    if field not in EDITABLE:
        raise ValueError(f"{field} is not editable here (editable: {sorted(EDITABLE)})")
    if not url or not quote:
        raise ValueError("an edited value needs --url and --quote")
    setattr(spec, field, float(value))
    spec.fields_sources[field] = FieldSource(url=url, quote=quote, retrieved_at=catalog.now_iso())
    notes = catalog.read_notes(spec)
    notes.setdefault("human_edits", []).append({"field": field, "value": float(value), "by": reviewer,
                                                "at": catalog.now_iso()})
    notes["agent_quote_validated"] = False  # the edited quote was not machine-checked
    catalog.write_notes(spec, notes)
    return unapprove(spec)


def clear_field(spec: RobotSpec, field: str, reviewer: str) -> RobotSpec:
    if field not in EDITABLE and field != "footprint_polygon_m":
        raise ValueError(f"{field} cannot be cleared here")
    if field == "footprint_polygon_m":
        spec.footprint_polygon_m, spec.footprint_approx = None, False
    elif field == "min_turn_radius_m":
        spec.min_turn_radius_m = 0.0
    else:
        setattr(spec, field, None)
    spec.fields_sources.pop(field, None)
    notes = catalog.read_notes(spec)
    notes.setdefault("human_edits", []).append({"field": field, "value": None, "by": reviewer, "at": catalog.now_iso()})
    catalog.write_notes(spec, notes)
    return unapprove(spec)


def _utf8_stdout():
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="python -m agents.catalog_builder.review")
    ap.add_argument("robot_id", nargs="?")
    ap.add_argument("--list", action="store_true")
    ap.add_argument("--approve", action="store_true")
    ap.add_argument("--unapprove", action="store_true")
    ap.add_argument("--set", dest="set_", default=None, metavar="FIELD=VALUE")
    ap.add_argument("--clear", default=None, metavar="FIELD")
    ap.add_argument("--url", default=None)
    ap.add_argument("--quote", default=None)
    ap.add_argument("--reviewer", default="reviewer")
    ap.add_argument("--allow-unsourced", action="store_true")
    a = ap.parse_args(argv)
    _utf8_stdout()

    if a.list:
        for s in catalog.load_catalog(verified_only=False):
            print(f"{s.robot_id:28s} {s.robot_class:10s} reviewed={s.verified_by_human}")
        return 0
    if not a.robot_id:
        ap.error("robot_id is required")
    spec = catalog.load_spec(a.robot_id)
    try:
        if a.set_:
            f, _, v = a.set_.partition("=")
            spec = set_field(spec, f.strip(), float(v), a.url, a.quote, a.reviewer)
        if a.clear:
            spec = clear_field(spec, a.clear.strip(), a.reviewer)
        if a.unapprove:
            spec = unapprove(spec)
        if a.approve:
            spec = approve(spec, a.reviewer, allow_unsourced=a.allow_unsourced)
    except ValueError as e:
        print(f"error: {e}", file=sys.stderr)
        return 2
    if a.set_ or a.clear or a.unapprove or a.approve:
        catalog.save_spec(spec)
    print(describe(spec))
    return 0


if __name__ == "__main__":
    sys.exit(main())
