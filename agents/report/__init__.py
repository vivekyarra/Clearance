"""Clearance Report: facts pack, ROI, writer (Ultra / offline template), PDF export."""
from agents.report.facts import build_citations, build_facts, collect_findings, derive_verdicts  # noqa: F401
from agents.report.roi import compute_roi  # noqa: F401
from agents.report.writer import make_regenerate_fn, write_report  # noqa: F401
