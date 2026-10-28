"""PDF export of the Clearance Report (P1). fpdf2 (pure Python). Fact chips print as small [f_xxx] marks
and a fact index at the end maps every id to its value and origin, so the printed copy stays traceable."""
from __future__ import annotations

import os
from pathlib import Path

from agents.factcheck.render import render_json

FONT_CANDIDATES = [
    ("C:/Windows/Fonts/arial.ttf", "C:/Windows/Fonts/arialbd.ttf"),
    ("/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf", "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf"),
    ("/Library/Fonts/Arial.ttf", "/Library/Fonts/Arial Bold.ttf"),
]


def _plain(nodes: list[dict], chips: bool = True) -> str:
    out = []
    for n in nodes:
        t = n["type"]
        if t in ("text", "code"):
            out.append(n["text"])
        elif t in ("strong", "em", "link"):
            out.append(_plain(n["children"], chips))
        elif t == "chip" and chips:
            out.append(f"[{n['fact_id']}]")
    return "".join(out)


class _Doc:
    def __init__(self, title: str):
        from fpdf import FPDF

        self.pdf = FPDF(format="A4")
        self.pdf.set_auto_page_break(True, margin=15)
        self.pdf.set_margins(15, 15, 15)
        self.pdf.set_title(title)
        self.unicode = False
        for regular, bold in FONT_CANDIDATES:
            if os.path.exists(regular) and os.path.exists(bold):
                self.pdf.add_font("Body", "", regular)
                self.pdf.add_font("Body", "B", bold)
                self.unicode = True
                break
        self.font = "Body" if self.unicode else "Helvetica"
        self.pdf.add_page()

    def t(self, s: str) -> str:
        if self.unicode:
            return s
        s = s.replace("–", "-").replace("—", "-").replace("→", "->").replace("−", "-").replace("²", "2")
        return s.encode("latin-1", "replace").decode("latin-1")

    def set(self, size: float, bold: bool = False, grey: bool = False):
        self.pdf.set_font(self.font, "B" if bold else "", size)
        self.pdf.set_text_color(110, 118, 128) if grey else self.pdf.set_text_color(25, 30, 36)

    def runs(self, nodes: list[dict], size: float = 10, bold: bool = False, indent: float = 0):
        self.pdf.set_x(self.pdf.l_margin + indent)
        h = size * 0.5
        for n in nodes:
            t = n["type"]
            if t == "chip":
                self.set(size * 0.7, grey=True)
                self.pdf.write(h, self.t(f"[{n['fact_id']}]"))
            elif t in ("strong", "em", "link"):
                self.runs_inline(n["children"], size, bold or t == "strong", h)
            else:
                self.set(size, bold)
                self.pdf.write(h, self.t(n["text"]))
        self.pdf.ln(h + 1.5)

    def runs_inline(self, nodes, size, bold, h):
        for n in nodes:
            if n["type"] == "chip":
                self.set(size * 0.7, grey=True)
                self.pdf.write(h, self.t(f"[{n['fact_id']}]"))
            elif n["type"] in ("strong", "em", "link"):
                self.runs_inline(n["children"], size, bold or n["type"] == "strong", h)
            else:
                self.set(size, bold)
                self.pdf.write(h, self.t(n["text"]))

    def table(self, header: list[list[dict]], rows: list[list[list[dict]]], size: float = 7.5):
        self.set(size)
        width = self.pdf.w - self.pdf.l_margin - self.pdf.r_margin
        with self.pdf.table(width=width, text_align="LEFT", line_height=size * 0.55,
                            first_row_as_headings=False) as tbl:
            for i, r in enumerate([header] + rows):
                row = tbl.row()
                for cell in r:
                    self.set(size, bold=i == 0)
                    row.cell(self.t(_plain(cell)))
        self.pdf.ln(2)


def export_pdf(md: str, facts: list, out_path: str | Path, *, title: str = "Clearance Report",
               fact_index: bool = True) -> str:
    doc = render_json(md, facts)
    d = _Doc(title)
    for b in doc["blocks"]:
        t = b["type"]
        if t == "heading":
            size = {1: 18, 2: 13.5, 3: 11.5}.get(b["level"], 10.5)
            d.pdf.ln(2 if b["level"] > 1 else 0)
            d.runs(b["children"], size=size, bold=True)
        elif t in ("paragraph", "quote"):
            d.runs(b["children"])
        elif t == "list":
            for k, it in enumerate(b["items"]):
                marker = f"{it.get('number', k + 1)}. " if b["ordered"] and it["depth"] == 0 else "- "
                d.runs([{"type": "text", "text": marker}] + it["children"], indent=4 + 5 * it["depth"])
        elif t == "table":
            d.table(b["header"], b["rows"])
        elif t == "code":
            d.set(8.5)
            d.pdf.multi_cell(0, 4.5, d.t(b["text"]))
    if fact_index and doc["facts"]:
        d.pdf.add_page()
        d.set(13.5, bold=True)
        d.pdf.write(7, d.t("Fact index"))
        d.pdf.ln(9)
        txt = lambda s: [{"type": "text", "text": s}]  # noqa: E731
        rows = [[txt(fid), txt(str(c.get("display", ""))), txt(str(c.get("label", ""))),
                 txt(f"{c['origin']['type']}: {c['origin']['ref']}" if c.get("origin") else "")]
                for fid, c in doc["facts"].items() if not c.get("missing")]
        d.table([txt("Fact"), txt("Value"), txt("Label"), txt("Origin")], rows, size=6.5)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    d.pdf.output(str(out_path))
    return str(out_path)
