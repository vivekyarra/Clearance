"""Tiny line-oriented markdown block parser shared by the fact checker, the chip renderer and PDF export.
Handles exactly what report markdown uses: headings, paragraphs (soft-wrapped lines are joined),
bullet/ordered lists, pipe tables, fenced code, horizontal rules, blockquotes."""
from __future__ import annotations

import re
from dataclasses import dataclass, field

HEADING_RE = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
TABLE_SEP_RE = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$")
TABLE_ROW_RE = re.compile(r"^\s*\|.*$")
LIST_RE = re.compile(r"^(\s*)([-*+]|\d+[.)])\s+(.*)$")
HR_RE = re.compile(r"^\s*([-*_])(\s*\1){2,}\s*$")
FENCE_RE = re.compile(r"^\s*(```|~~~)")
QUOTE_RE = re.compile(r"^\s*>\s?(.*)$")
SECTION_NUM_RE = re.compile(r"^((?:\d+\.)*\d+\.?)\s+")


@dataclass
class Block:
    kind: str                 # blank | heading | paragraph | list_item | table_row | table_sep | code | hr | quote
    line: int                 # 1-based first line
    raw: list[str]
    text: str = ""            # heading/paragraph/list/quote content (wrapped lines joined with a space)
    level: int = 0            # heading level
    prefix: str = ""          # list marker with indentation ("  - ", "3. "), heading section number ("2.")
    cells: list[str] = field(default_factory=list)
    section: str = ""         # text of the nearest heading above (section number stripped)


def split_cells(row: str) -> list[str]:
    s = row.strip()
    if s.startswith("|"):
        s = s[1:]
    if s.endswith("|") and not s.endswith("\\|"):
        s = s[:-1]
    cells, cur, i = [], "", 0
    while i < len(s):
        if s[i] == "\\" and i + 1 < len(s) and s[i + 1] == "|":
            cur += "\\|"
            i += 2
            continue
        if s[i] == "|":
            cells.append(cur.strip())
            cur = ""
        else:
            cur += s[i]
        i += 1
    cells.append(cur.strip())
    return cells


def join_cells(cells: list[str]) -> str:
    return "| " + " | ".join(cells) + " |"


def _has_pipe(line: str) -> bool:
    return bool(re.search(r"(?<!\\)\|", line))


def _is_sep(line: str) -> bool:
    return bool(TABLE_SEP_RE.match(line)) and "-" in line and "|" in line


def parse_blocks(md: str) -> list[Block]:
    lines = md.replace("\r\n", "\n").replace("\r", "\n").split("\n")
    blocks: list[Block] = []
    section = ""
    in_code: Block | None = None
    in_table = False
    for n, line in enumerate(lines, 1):
        if in_code is not None:
            in_code.raw.append(line)
            if FENCE_RE.match(line):
                in_code = None
            continue
        if FENCE_RE.match(line):
            in_code = Block("code", n, [line], section=section)
            blocks.append(in_code)
            continue
        if not line.strip():
            in_table = False
            blocks.append(Block("blank", n, [line], section=section))
            continue
        m = HEADING_RE.match(line)
        if m:
            text = m.group(2)
            sm = SECTION_NUM_RE.match(text)
            prefix = sm.group(1) if sm else ""
            text = text[sm.end():] if sm else text
            section = text.strip()
            in_table = False
            blocks.append(Block("heading", n, [line], text=text, level=len(m.group(1)), prefix=prefix,
                                section=section))
            continue
        if TABLE_SEP_RE.match(line) and "-" in line and ("|" in line):
            in_table = True
            blocks.append(Block("table_sep", n, [line], section=section))
            continue
        # pipe tables, with or without the leading pipe ("a | b" followed by "---|---")
        next_is_sep = n < len(lines) and _is_sep(lines[n])
        if TABLE_ROW_RE.match(line) or (_has_pipe(line) and (in_table or next_is_sep)):
            blocks.append(Block("table_row", n, [line], cells=split_cells(line), section=section))
            continue
        in_table = False
        if HR_RE.match(line):
            blocks.append(Block("hr", n, [line], section=section))
            continue
        m = LIST_RE.match(line)
        if m:
            blocks.append(Block("list_item", n, [line], text=m.group(3), prefix=m.group(1) + m.group(2) + " ",
                                section=section))
            continue
        m = QUOTE_RE.match(line)
        if m:
            prev = blocks[-1] if blocks else None
            if prev is not None and prev.kind == "quote":
                prev.raw.append(line)
                prev.text = (prev.text + " " + m.group(1).strip()).strip()
            else:
                blocks.append(Block("quote", n, [line], text=m.group(1).strip(), prefix="> ", section=section))
            continue
        prev = blocks[-1] if blocks else None
        if prev is not None and prev.kind in ("paragraph", "list_item"):
            prev.raw.append(line)
            prev.text = prev.text + " " + line.strip()
        else:
            blocks.append(Block("paragraph", n, [line], text=line.strip(), section=section))
    return blocks


ABBREV = {"e.g", "i.e", "vs", "approx", "incl", "cf", "ca", "fig", "no", "est", "avg", "etc", "resp", "max", "min"}
_END_RE = re.compile(r"[.!?](?:\s*\[f_[A-Za-z0-9_]+\??(?:\s*,\s*f_[A-Za-z0-9_]+\??)*\])*(?=\s|$)")


def split_sentences(text: str) -> list[str]:
    """Split prose into sentences; pieces concatenate back to `text` exactly. A fact tag written right
    after the full stop ("... 93%. [f_004]") stays with its sentence. Decimal points never split."""
    out, start = [], 0
    for m in _END_RE.finditer(text):
        if text[m.start()] == ".":
            word = re.search(r"([A-Za-z.]+)$", text[start:m.start()])
            if word and word.group(1).lower().strip(".") in ABBREV:
                continue
        end = m.end()
        ws = re.match(r"\s*", text[end:])
        end += ws.end() if ws else 0
        out.append(text[start:end])
        start = end
    if start < len(text):
        out.append(text[start:])
    return [s for s in out if s]
