/** Small, safe Markdown renderer (headings, paragraphs, lists, GFM tables, bold/italic/code/links) with fact chips. */
import { Fragment, type ReactNode } from "react";
import { parseFactTags } from "../lib/facts";
import { FactChips } from "./Facts";

export interface MdBlock {
  kind: "h" | "p" | "ul" | "ol" | "table";
  level?: number;
  text?: string;
  items?: string[];
  rows?: string[][];
  head?: string[];
}

export function parseMarkdown(md: string): MdBlock[] {
  const lines = md.replace(/\r\n?/g, "\n").split("\n");
  const out: MdBlock[] = [];
  let i = 0;
  const isTableSep = (l: string) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(l);
  const cells = (l: string) =>
    l
      .trim()
      .replace(/^\|/, "")
      .replace(/\|$/, "")
      .split("|")
      .map((c) => c.trim());
  while (i < lines.length) {
    const l = lines[i];
    if (!l.trim()) {
      i++;
      continue;
    }
    const h = /^(#{1,4})\s+(.*)$/.exec(l);
    if (h) {
      out.push({ kind: "h", level: h[1].length, text: h[2].trim() });
      i++;
      continue;
    }
    if (l.trim().startsWith("|") && i + 1 < lines.length && isTableSep(lines[i + 1])) {
      const head = cells(l);
      const rows: string[][] = [];
      i += 2;
      while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(cells(lines[i++]));
      out.push({ kind: "table", head, rows });
      continue;
    }
    if (/^\s*[-*]\s+/.test(l)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*[-*]\s+/.test(lines[i])) {
        let item = lines[i++].replace(/^\s*[-*]\s+/, "");
        while (i < lines.length && /^\s{2,}\S/.test(lines[i]) && !/^\s*[-*]\s+/.test(lines[i])) item += " " + lines[i++].trim();
        items.push(item);
      }
      out.push({ kind: "ul", items });
      continue;
    }
    if (/^\s*\d+[.)]\s+/.test(l)) {
      const items: string[] = [];
      while (i < lines.length && /^\s*\d+[.)]\s+/.test(lines[i])) items.push(lines[i++].replace(/^\s*\d+[.)]\s+/, ""));
      out.push({ kind: "ol", items });
      continue;
    }
    const para: string[] = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,4})\s/.test(lines[i]) && !/^\s*[-*]\s+/.test(lines[i]) && !/^\s*\d+[.)]\s+/.test(lines[i]) && !(lines[i].trim().startsWith("|") && isTableSep(lines[i + 1] ?? ""))) para.push(lines[i++].trim());
    out.push({ kind: "p", text: para.join(" ") });
  }
  return out;
}

/** Split markdown into "## " sections: [{title, body}] (text before the first heading has title ""). */
export function splitSections(md: string): { title: string; body: string }[] {
  const out: { title: string; body: string }[] = [];
  let cur = { title: "", body: [] as string[] };
  for (const line of md.replace(/\r\n?/g, "\n").split("\n")) {
    const m = /^##\s+(.*)$/.exec(line);
    if (m) {
      if (cur.title || cur.body.some((x) => x.trim())) out.push({ title: cur.title, body: cur.body.join("\n") });
      cur = { title: m[1].trim(), body: [] };
    } else cur.body.push(line);
  }
  if (cur.title || cur.body.some((x) => x.trim())) out.push({ title: cur.title, body: cur.body.join("\n") });
  return out;
}

const INLINE = /(`[^`]+`)|\[([^\]]+)\]\((https?:\/\/[^\s)]+|\/[^\s)]*)\)|\*\*([^*]+)\*\*|(?<![\w*])\*([^*\s][^*]*)\*(?!\w)|(?<!\w)_([^_\s][^_]*)_(?!\w)/;

export function Inline({ text }: { text: string }): ReactNode {
  const parts: ReactNode[] = [];
  let rest = text;
  let k = 0;
  while (rest) {
    const m = INLINE.exec(rest);
    if (!m) {
      parts.push(<TextWithFacts key={k++} text={rest} />);
      break;
    }
    if (m.index > 0) parts.push(<TextWithFacts key={k++} text={rest.slice(0, m.index)} />);
    if (m[1]) parts.push(<code key={k++}>{m[1].slice(1, -1)}</code>);
    else if (m[2])
      parts.push(
        <a key={k++} href={m[3]} target={m[3].startsWith("http") ? "_blank" : undefined} rel="noreferrer">
          {m[2]}
        </a>,
      );
    else if (m[4])
      parts.push(
        <strong key={k++}>
          <Inline text={m[4]} />
        </strong>,
      );
    else if (m[5] || m[6])
      parts.push(
        <em key={k++}>
          <Inline text={(m[5] || m[6])!} />
        </em>,
      );
    rest = rest.slice(m.index + m[0].length);
  }
  return <>{parts}</>;
}

function TextWithFacts({ text }: { text: string }) {
  return (
    <>
      {parseFactTags(text).map((s, i) => (s.kind === "text" ? <Fragment key={i}>{s.text}</Fragment> : <FactChips key={i} ids={s.ids} />))}
    </>
  );
}

export function Markdown({ md, skipHeadings }: { md: string; skipHeadings?: boolean }) {
  const blocks = parseMarkdown(md);
  return (
    <div className="md">
      {blocks.map((b, i) => {
        switch (b.kind) {
          case "h":
            if (skipHeadings) return null;
            return b.level === 1 ? <h2 key={i}>{<Inline text={b.text!} />}</h2> : b.level === 2 ? <h2 key={i}><Inline text={b.text!} /></h2> : <h3 key={i}><Inline text={b.text!} /></h3>;
          case "p":
            return (
              <p key={i}>
                <Inline text={b.text!} />
              </p>
            );
          case "ul":
          case "ol": {
            const L = b.kind === "ul" ? "ul" : "ol";
            return (
              <L key={i}>
                {b.items!.map((it, j) => (
                  <li key={j}>
                    <Inline text={it} />
                  </li>
                ))}
              </L>
            );
          }
          case "table":
            return (
              <div className="table-wrap card" key={i}>
                <table className="table">
                  <thead>
                    <tr>
                      {b.head!.map((h, j) => (
                        <th key={j}>
                          <Inline text={h} />
                        </th>
                      ))}
                    </tr>
                  </thead>
                  <tbody>
                    {b.rows!.map((r, j) => (
                      <tr key={j}>
                        {r.map((c, kk) => (
                          <td key={kk}>
                            <Inline text={c} />
                          </td>
                        ))}
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            );
        }
      })}
    </div>
  );
}
