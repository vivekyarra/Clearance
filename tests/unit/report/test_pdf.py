from __future__ import annotations

from agents.report import pdf
from agents.report.facts import build_citations, build_facts, collect_findings, derive_verdicts, investigations
from agents.report.writer import template_report


def _md(analysis):
    facts = build_facts(analysis)
    md = template_report(facts, build_citations(analysis), collect_findings(analysis, facts),
                         derive_verdicts(analysis), investigations(analysis))
    return md, facts


def test_pdf_export_with_system_font(analysis, tmp_path):
    md, facts = _md(analysis)
    out = pdf.export_pdf(md, facts, tmp_path / "r.pdf")
    data = open(out, "rb").read()
    assert data[:5] == b"%PDF-" and len(data) > 5000


def test_pdf_export_core_font_fallback(analysis, tmp_path, monkeypatch):
    monkeypatch.setattr(pdf, "FONT_CANDIDATES", [])
    md, facts = _md(analysis)
    out = pdf.export_pdf(md + "\nUnicode – → − ² ₹ checks.\n", facts, tmp_path / "sub" / "r.pdf", fact_index=False)
    assert open(out, "rb").read()[:5] == b"%PDF-"
