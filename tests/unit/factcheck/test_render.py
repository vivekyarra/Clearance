from __future__ import annotations

from agents.factcheck.render import render_html, render_json

FACTS = [
    {"fact_id": "f_001", "value": 0.93, "unit": "ratio", "label": "Success rate", "origin": {"type": "sim_batch", "ref": "sb_1"}},
    {"fact_id": "f_002", "value": 0.18, "unit": "m", "label": "Gap", "origin": {"type": "measurement", "ref": "twin:leg0"}},
    {"fact_id": "f_003", "value": 1.2, "unit": "m/s", "label": "Max speed",
     "origin": {"type": "source_url", "ref": "https://example.com/spec"}},
    {"fact_id": "f_004", "value": 6, "unit": "h/day", "label": "Task hours", "origin": {"type": "user_input", "ref": "business.x"}},
]

MD = """# Clearance Report

## 1. Verdicts

Robot A reaches **93% [f_001]** with a `T4` move; see [spec](https://example.com/spec).

- gap 18 cm [f_002]
  - nested 1.2 m/s [f_003]
1. ordered 6 [f_004]

| Robot | Success |
|---|---|
| A | 93% [f_001] |

Unknown [f_999] tag.
"""


def _chips(nodes):
    out = []
    for n in nodes:
        if n["type"] == "chip":
            out.append(n)
        out += _chips(n.get("children", []))
    return out


def test_render_json_blocks_and_chips():
    doc = render_json(MD, FACTS)
    kinds = [b["type"] for b in doc["blocks"]]
    assert kinds == ["heading", "heading", "paragraph", "list", "list", "table", "paragraph"]
    para = doc["blocks"][2]["children"]
    chips = _chips(para)
    assert chips[0]["fact_id"] == "f_001" and chips[0]["action"] == "replay" and chips[0]["display"] == "93%"
    assert any(n["type"] == "code" and n["text"] == "T4" for n in para)
    assert any(n["type"] == "link" for n in para)
    lst = doc["blocks"][3]
    assert len(lst["items"]) == 2 and lst["items"][1]["depth"] == 1
    assert _chips(lst["items"][1]["children"])[0]["href"] == "https://example.com/spec"
    assert _chips(lst["items"][0]["children"])[0]["action"] == "twin"
    assert doc["blocks"][4]["ordered"] and _chips(doc["blocks"][4]["items"][0]["children"])[0]["action"] == "inputs"
    tbl = doc["blocks"][5]
    assert len(tbl["header"]) == 2 and len(tbl["rows"]) == 1
    assert _chips(tbl["rows"][0][1])[0]["fact_id"] == "f_001"
    assert _chips(doc["blocks"][6]["children"])[0] == {"type": "chip", "fact_id": "f_999", "missing": True}
    assert set(doc["facts"]) == {"f_001", "f_002", "f_003", "f_004"}


def test_render_html_chips_and_escaping():
    html = render_html(MD + "\n<script>alert(1)</script> [f_001]\n", FACTS, standalone=True)
    assert html.startswith("<!doctype html>")
    assert 'data-fact-id="f_001"' in html and 'data-action="replay"' in html
    assert 'href="https://example.com/spec"' in html
    assert "<script>" not in html and "&lt;script&gt;" in html
    assert "cl-chip--missing" in html
    assert "<table>" in html and "<ol>" in html and "<code>T4</code>" in html


def test_render_html_link_scheme_filtered():
    html = render_html("[x](javascript:alert(1))", FACTS)
    assert "javascript:" not in html
