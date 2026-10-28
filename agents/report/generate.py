"""One call from a hydrated analysis to a fact-checked Clearance Report, saved under analyses/<id>/
(facts.json, citations.json, report.md, report.json with the writer metrics under "writer", report_meta.json,
report_check.json; report.pdf when asked)."""
from __future__ import annotations

from clearance_core import storage

from agents.factcheck.check import enforce
from agents.factcheck.render import render_json
from agents.report.facts import _d, build_citations, build_facts, collect_findings, derive_verdicts, investigations
from agents.report.writer import make_regenerate_fn, write_report


def generate_report(analysis: dict, *, save: bool = True, mode: str | None = None, llm=None,
                    title: str | None = None, pdf: bool = False) -> dict:
    analysis = _d(analysis) or {}
    facts = build_facts(analysis)
    citations = build_citations(analysis)
    findings = collect_findings(analysis, facts)
    verdicts = analysis.get("verdicts")
    verdicts = [_d(v) for v in verdicts] if verdicts is not None else [_d(v) for v in derive_verdicts(analysis)]
    invs = investigations(analysis)
    site = (analysis.get("site") or {}).get("name") if isinstance(analysis.get("site"), dict) else None
    title = title or (f"Clearance Report: {site}" if site else "Clearance Report")

    md = write_report(facts, citations, findings, verdicts, invs, mode=mode, llm=llm, title=title)
    regen = make_regenerate_fn(facts, citations, findings, verdicts, invs, mode=mode, llm=llm, title=title)
    checked = enforce(md, facts, regen)
    writer_meta = getattr(md, "meta", {}) or {}
    out = {"md": checked["md"], "facts": facts, "citations": citations, "verdicts": verdicts,
           "check": {k: v for k, v in checked.items() if k != "md"}, "writer": writer_meta, "keys": {}}
    if save and analysis.get("analysis_id"):
        base = f"analyses/{analysis['analysis_id']}/"
        out["keys"]["facts"] = storage.write_json(base + "facts.json", [f.model_dump(mode="json") for f in facts])
        out["keys"]["citations"] = storage.write_json(base + "citations.json", citations)
        storage.path(base + "report.md").write_text(checked["md"], encoding="utf-8")
        out["keys"]["report"] = base + "report.md"
        out["keys"]["report_json"] = storage.write_json(base + "report.json",
                                                        {**render_json(checked["md"], facts), "writer": writer_meta})
        out["keys"]["report_meta"] = storage.write_json(base + "report_meta.json", writer_meta)
        out["keys"]["check"] = storage.write_json(base + "report_check.json", out["check"])
        if pdf:
            from agents.report.pdf import export_pdf
            export_pdf(checked["md"], facts, storage.path(base + "report.pdf"), title=title)
            out["keys"]["pdf"] = base + "report.pdf"
    return out
