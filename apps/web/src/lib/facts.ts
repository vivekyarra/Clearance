import type { Fact, Twin, XY } from "../types";

/** One fact id: f_ followed by [A-Za-z0-9_-]. */
const ID = "f_[A-Za-z0-9][A-Za-z0-9_\\-]*";
/** A bracket group of one or more ids: [f_1] or [f_1, f_2] or [f_1; f_2]. */
const TAG_RE = new RegExp(`\\[(${ID}(?:\\s*[,;]\\s*${ID})*)\\]`, "g");

export type Segment = { kind: "text"; text: string } | { kind: "facts"; ids: string[]; raw: string };

/**
 * Split text into plain text and fact-tag segments. Adjacent tag groups ("[f_1][f_2]")
 * are merged into one segment so they render as a single chip cluster.
 */
export function parseFactTags(text: string): Segment[] {
  const out: Segment[] = [];
  let last = 0;
  TAG_RE.lastIndex = 0;
  let m: RegExpExecArray | null;
  while ((m = TAG_RE.exec(text))) {
    if (m.index > last) out.push({ kind: "text", text: text.slice(last, m.index) });
    const ids = m[1].split(/\s*[,;]\s*/).filter(Boolean);
    const prev = out[out.length - 1];
    if (prev && prev.kind === "facts" && m.index === last) {
      prev.ids.push(...ids);
      prev.raw += m[0];
    } else {
      out.push({ kind: "facts", ids, raw: m[0] });
    }
    last = m.index + m[0].length;
  }
  if (last < text.length) out.push({ kind: "text", text: text.slice(last) });
  return out;
}

export function extractFactIds(text: string): string[] {
  const ids: string[] = [];
  for (const s of parseFactTags(text)) if (s.kind === "facts") ids.push(...s.ids);
  return ids;
}

export function indexFacts(facts: Fact[]): Map<string, Fact> {
  return new Map(facts.map((f) => [f.fact_id, f]));
}

const PCT_UNITS = new Set(["ratio", "fraction", "share"]);

export function formatFactValue(f: Pick<Fact, "value" | "unit">): string {
  const v = f.value;
  const unit = f.unit ?? "";
  if (v === null || v === undefined) return "unknown";
  if (typeof v === "string") return unit ? `${v} ${unit}` : v;
  if (PCT_UNITS.has(unit)) return `${(v * 100).toFixed(1).replace(/\.0$/, "")}%`;
  if (unit === "%") return `${fmtNum(v)}%`;
  if (unit === "USD" || unit === "usd" || unit === "$") return `$${fmtNum(v, 0)}`;
  if (unit === "m" && Math.abs(v) < 1) return `${(v * 100).toFixed(1).replace(/\.0$/, "")} cm`;
  return unit ? `${fmtNum(v)} ${unit}` : fmtNum(v);
}

export function fmtNum(v: number, maxFrac = 2): string {
  if (!Number.isFinite(v)) return String(v);
  const abs = Math.abs(v);
  const frac = abs >= 1000 ? 0 : abs >= 100 ? Math.min(1, maxFrac) : maxFrac;
  return v.toLocaleString("en-US", { maximumFractionDigits: frac });
}

export type MeasurementTarget =
  | { kind: "entity"; id: string }
  | { kind: "point"; xy: XY }
  | { kind: "segment"; a: XY; b: XY }
  | { kind: "none" };

/**
 * Resolve a measurement fact ref to something drawable on the twin (docs/CONTRACTS.md "Measurement fact refs").
 * Fixed forms: "<entity id>" (primitive / threshold / waypoint / keep-out), "xy:<x>,<y>",
 * "seg:<x1>,<y1>,<x2>,<y2>". Forms agents/report/facts.py emits for twin facts:
 * "<twin_id>:threshold:<id>" (entity), "<twin_id>:hazard:<index>" (hazard point),
 * "<twin_id>:accuracy|floor|meta|traffic|hazards" and "<twin_id>:measurement:<name>" (nothing to draw).
 * Any ref containing "@<x>,<y>" also resolves to a point.
 */
export function measurementTarget(ref: string, twin?: Twin | null): MeasurementTarget {
  const nums = (s: string) => s.split(",").map((t) => Number(t.trim()));
  const seg = /^seg:(.+)$/.exec(ref);
  if (seg) {
    const n = nums(seg[1]);
    if (n.length === 4 && n.every(Number.isFinite)) return { kind: "segment", a: [n[0], n[1]], b: [n[2], n[3]] };
  }
  const xy = /^xy:(.+)$/.exec(ref) ?? /@\s*(-?[\d.]+\s*,\s*-?[\d.]+)/.exec(ref);
  if (xy) {
    const n = nums(xy[1]);
    if (n.length === 2 && n.every(Number.isFinite)) return { kind: "point", xy: [n[0], n[1]] };
  }
  if (twin) {
    const hz = /:hazard:(\d+)$/.exec(ref);
    if (hz) {
      const xy = twin.hazards?.[Number(hz[1])]?.xy;
      return xy ? { kind: "point", xy: [xy[0], xy[1]] } : { kind: "none" };
    }
    if (/:measurement:/.test(ref)) return { kind: "none" };
    const ids = new Set<string>([
      ...(twin.primitives ?? []).map((p) => p.id),
      ...(twin.thresholds ?? []).map((t) => t.id),
      ...(twin.waypoints ?? []).map((w) => w.id),
      ...(twin.keepout_zones ?? []).map((k) => k.id),
    ]);
    const bare = ref.replace(/^(obj|entity|id):/, "");
    if (ids.has(bare)) return { kind: "entity", id: bare };
    // e.g. "clearance:T4" -> T4
    const tail = bare.split(/[:/#]/).pop() ?? "";
    if (ids.has(tail)) return { kind: "entity", id: tail };
  }
  return { kind: "none" };
}

/** sim_batch refs may carry an episode: "sb_123", "sb_123#7", "sb_123/episodes/7". */
export function parseBatchRef(ref: string): { batchId: string; episode: number } {
  const m = /^([^#/]+)(?:(?:#|\/episodes\/)(\d+))?/.exec(ref);
  return { batchId: m?.[1] ?? ref, episode: m?.[2] ? Number(m[2]) : 0 };
}
