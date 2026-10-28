"""Render Clearance Report markdown for the UI: [f_xxx] tags become chip objects (JSON) or chip
elements (HTML). Chip actions follow PLAN 7.11: sim batch -> replay, URL -> source, measurement -> twin view,
user input -> inputs panel, formula -> formula view, investigation -> trace."""
from __future__ import annotations

import html
import re

from agents.factcheck.check import _fget, _origin
from agents.factcheck.mdparse import parse_blocks

ACTIONS = {"sim_batch": "replay", "source_url": "source", "measurement": "twin", "user_input": "inputs",
           "formula": "formula", "investigation": "trace"}
CHIP_TEXT = {"sim_batch": "sim", "source_url": "src", "measurement": "meas", "user_input": "input",
             "formula": "calc", "investigation": "trace"}
INLINE_RE = re.compile(
    r"(?P<tag>\[(?P<ids>f_[A-Za-z0-9_]+\??(?:\s*,\s*f_[A-Za-z0-9_]+\??)*)\])"
    r"|(?P<code>`[^`]+`)"
    r"|(?P<link>\[(?P<ltext>[^\[\]]+)\]\((?P<href>[^)\s]+)\))"
    r"|(?P<strong>\*\*(?P<stext>.+?)\*\*)"
    r"|(?P<em>(?<![\w*])\*(?!\s)(?P<etext>[^*]+?)(?<!\s)\*(?![\w*]))"
)


def _display(f) -> str:
    from agents.report.writer import fmt_value  # shared value formatting
    return fmt_value(f)


def chip(fid: str, facts_by_id: dict) -> dict:
    f = facts_by_id.get(fid.rstrip("?"))
    if f is None or fid.endswith("?"):
        return {"type": "chip", "fact_id": fid.rstrip("?"), "missing": True}
    o = _origin(f)
    out = {"type": "chip", "fact_id": fid, "value": _fget(f, "value"), "unit": _fget(f, "unit") or "",
           "label": _fget(f, "label"), "display": _display(f),
           "origin": {"type": o.get("type"), "ref": o.get("ref")}, "action": ACTIONS.get(o.get("type"), "info")}
    if o.get("type") == "source_url" and str(o.get("ref", "")).startswith(("http://", "https://")):
        out["href"] = o["ref"]
    return out


def inlines(text: str, facts_by_id: dict) -> list[dict]:
    out, pos = [], 0
    for m in INLINE_RE.finditer(text):
        if m.start() > pos:
            out.append({"type": "text", "text": text[pos:m.start()]})
        if m.group("tag"):
            out += [chip(i.strip(), facts_by_id) for i in m.group("ids").split(",")]
        elif m.group("code"):
            out.append({"type": "code", "text": m.group("code")[1:-1]})
        elif m.group("link"):
            out.append({"type": "link", "href": m.group("href"), "children": inlines(m.group("ltext"), facts_by_id)})
        elif m.group("strong"):
            out.append({"type": "strong", "children": inlines(m.group("stext"), facts_by_id)})
        else:
            out.append({"type": "em", "children": inlines(m.group("etext"), facts_by_id)})
        pos = m.end()
    if pos < len(text):
        out.append({"type": "text", "text": text[pos:]})
    return out


def render_json(md: str, facts: list) -> dict:
    """{"blocks": [...], "facts": {id: chip}}; block types: heading, paragraph, list, table, code, hr, quote."""
    fb = {_fget(f, "fact_id"): f for f in facts}
    blocks: list[dict] = []
    table: dict | None = None
    for b in parse_blocks(md):
        if b.kind != "table_row" and b.kind != "table_sep":
            table = None
        if b.kind == "heading":
            text = (b.prefix + " " if b.prefix else "") + b.text
            blocks.append({"type": "heading", "level": b.level, "children": inlines(text, fb)})
        elif b.kind == "paragraph":
            blocks.append({"type": "paragraph", "children": inlines(b.text, fb)})
        elif b.kind == "quote":
            blocks.append({"type": "quote", "children": inlines(b.text, fb)})
        elif b.kind == "list_item":
            indent = len(b.prefix) - len(b.prefix.lstrip())
            ordered = b.prefix.strip()[0].isdigit()
            last = blocks[-1] if blocks else None
            item = {"depth": indent // 2, "children": inlines(b.text, fb)}
            if ordered:
                item["number"] = int(re.match(r"\d+", b.prefix.strip()).group(0))
            if last is not None and last["type"] == "list" and (last["ordered"] == ordered or indent):
                last["items"].append(item)
            else:
                blocks.append({"type": "list", "ordered": ordered, "items": [item]})
        elif b.kind == "table_row":
            cells = [inlines(c, fb) for c in b.cells]
            if table is None:
                table = {"type": "table", "header": cells, "rows": []}
                blocks.append(table)
            else:
                table["rows"].append(cells)
        elif b.kind == "code":
            blocks.append({"type": "code", "text": "\n".join(b.raw[1:-1] if len(b.raw) > 1 else b.raw)})
        elif b.kind == "hr":
            blocks.append({"type": "hr"})
    chips = {}
    for f in facts:
        fid = _fget(f, "fact_id")
        chips[fid] = chip(fid, fb)
    return {"blocks": blocks, "facts": chips}


# ---------------------------------------------------------------- HTML
def _h_inlines(nodes: list[dict]) -> str:
    out = []
    for n in nodes:
        t = n["type"]
        if t == "text":
            out.append(html.escape(n["text"]))
        elif t == "code":
            out.append(f"<code>{html.escape(n['text'])}</code>")
        elif t == "strong":
            out.append(f"<strong>{_h_inlines(n['children'])}</strong>")
        elif t == "em":
            out.append(f"<em>{_h_inlines(n['children'])}</em>")
        elif t == "link":
            href = n["href"] if n["href"].startswith(("http://", "https://", "#", "/")) else "#"
            out.append(f'<a href="{html.escape(href)}" rel="noopener noreferrer" target="_blank">'
                       f"{_h_inlines(n['children'])}</a>")
        elif t == "chip":
            out.append(_h_chip(n))
    return "".join(out)


def _h_chip(c: dict) -> str:
    if c.get("missing"):
        return f'<span class="cl-chip cl-chip--missing" data-fact-id="{html.escape(c["fact_id"])}">?</span>'
    otype = c["origin"]["type"] or "info"
    title = f'{c["label"]}: {c["display"]} ({otype.replace("_", " ")}: {c["origin"]["ref"]})'
    attrs = (f'class="cl-chip cl-chip--{html.escape(otype)}" data-fact-id="{html.escape(c["fact_id"])}" '
             f'data-action="{html.escape(c["action"])}" data-origin-type="{html.escape(otype)}" '
             f'data-origin-ref="{html.escape(str(c["origin"]["ref"]))}" title="{html.escape(title)}"')
    text = CHIP_TEXT.get(otype, "info")
    if c.get("href"):
        return f'<a {attrs} href="{html.escape(c["href"])}" target="_blank" rel="noopener noreferrer">{text}</a>'
    return f'<button type="button" {attrs}>{text}</button>'


CSS = """
:root{--fg:#1d232b;--muted:#5b6672;--bg:#ffffff;--line:#d9dee4;--chip:#eef2f6;--accent:#1f6feb}
@media (prefers-color-scheme: dark){:root{--fg:#e6e9ee;--muted:#9aa5b1;--bg:#12161b;--line:#2c333b;--chip:#1e252d;--accent:#58a6ff}}
body{font:15px/1.55 system-ui,-apple-system,"Segoe UI",sans-serif;color:var(--fg);background:var(--bg);max-width:960px;margin:0 auto;padding:24px 16px}
h1{font-size:1.7em;margin:.2em 0 .6em}h2{font-size:1.25em;margin:1.6em 0 .5em;border-bottom:1px solid var(--line);padding-bottom:.2em}
h3{font-size:1.05em;margin:1.2em 0 .4em}table{border-collapse:collapse;width:100%;font-size:.92em;display:block;overflow-x:auto}
th,td{border:1px solid var(--line);padding:6px 8px;text-align:left;vertical-align:top}th{background:var(--chip)}
code{background:var(--chip);padding:1px 4px;border-radius:4px;font-size:.92em}
.cl-chip{display:inline-block;font:600 10px/1.6 system-ui,sans-serif;padding:0 5px;margin:0 1px;border-radius:8px;border:1px solid var(--line);
background:var(--chip);color:var(--accent);cursor:pointer;vertical-align:super;text-decoration:none}
.cl-chip--missing{color:#b42318}
"""


def render_html(md: str, facts: list, *, standalone: bool = False, title: str = "Clearance Report") -> str:
    doc = render_json(md, facts)
    out = []
    for b in doc["blocks"]:
        t = b["type"]
        if t == "heading":
            out.append(f"<h{b['level']}>{_h_inlines(b['children'])}</h{b['level']}>")
        elif t == "paragraph":
            out.append(f"<p>{_h_inlines(b['children'])}</p>")
        elif t == "quote":
            out.append(f"<blockquote>{_h_inlines(b['children'])}</blockquote>")
        elif t == "list":
            tagname = "ol" if b["ordered"] else "ul"
            items = "".join(f'<li style="margin-left:{it["depth"] * 1.2}em">{_h_inlines(it["children"])}</li>'
                            for it in b["items"])
            out.append(f"<{tagname}>{items}</{tagname}>")
        elif t == "table":
            head = "".join(f"<th>{_h_inlines(c)}</th>" for c in b["header"])
            rows = "".join("<tr>" + "".join(f"<td>{_h_inlines(c)}</td>" for c in r) + "</tr>" for r in b["rows"])
            out.append(f"<table><thead><tr>{head}</tr></thead><tbody>{rows}</tbody></table>")
        elif t == "code":
            out.append(f"<pre><code>{html.escape(b['text'])}</code></pre>")
        elif t == "hr":
            out.append("<hr>")
    body = "\n".join(out)
    if not standalone:
        return body
    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width, initial-scale=1">'
            f"<title>{html.escape(title)}</title><style>{CSS}</style></head><body>{body}</body></html>")
