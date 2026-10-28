/** Pure helpers for the Clearance check progress view: stage rows, live counters, hypothesis caption, recorded-run replay. */
import type { AnalysisDoc, Experiment, Fact, Feasibility, Investigation, StageStatus, XY } from "../../types";

export const STAGE_ORDER = ["candidates", "feasibility", "batches", "investigation", "optimizer", "verdicts", "report"] as const;

export const STAGE_LABELS: Record<string, string> = {
  candidates: "Screening robots",
  feasibility: "Checking routes",
  batches: "Simulating missions",
  simulation: "Simulating missions",
  investigation: "Nemotron investigation",
  optimizer: "Layout search",
  verdicts: "Verdicts",
  report: "Writing the Clearance Report",
};

export type RowState = "done" | "running" | "pending" | "skipped" | "failed";

export interface StageRow {
  key: string;
  label: string;
  state: RowState;
  /** 0..1 for a determinate bar, null for an indeterminate (running without progress) bar */
  progress: number | null;
  /** skip / failure reason, or the stage detail line */
  note?: string;
}

type RawStage = StageStatus & { detail?: unknown };

function prettyKey(k: string): string {
  const s = k.replace(/[_-]+/g, " ").trim();
  return s ? s[0].toUpperCase() + s.slice(1) : k;
}

/** Map the analysis.json `stages` object to display rows (canonical order first, unknown stages after). */
export function stageRows(stages: Record<string, StageStatus> | null | undefined): StageRow[] {
  const src = (stages ?? {}) as Record<string, RawStage>;
  const present = Object.keys(src);
  if (!present.length) return STAGE_ORDER.map((k) => stageRow(k, undefined));
  return present.sort((a, b) => order(a) - order(b)).map((k) => stageRow(k, src[k]));
}

export function stageRow(key: string, s: RawStage | undefined): StageRow {
  const label = STAGE_LABELS[key] ?? prettyKey(key);
  const raw = String(s?.state ?? "pending");
  const [head, ...rest] = raw.split(":");
  const reason = rest.join(":").trim() || undefined;
  const detail = typeof s?.detail === "string" ? s.detail : typeof s?.message === "string" ? s.message : undefined;
  const p = typeof s?.progress === "number" && Number.isFinite(s.progress) ? Math.max(0, Math.min(1, s.progress)) : null;
  switch (head.trim()) {
    case "done":
      return { key, label, state: "done", progress: 1, note: detail };
    case "running":
      return { key, label, state: "running", progress: p && p > 0 ? p : null, note: detail };
    case "skipped":
      return { key, label, state: "skipped", progress: 0, note: reason ?? detail ?? "not needed for this check" };
    case "failed":
      return { key, label, state: "failed", progress: p ?? 0, note: reason ?? detail };
    default:
      return { key, label, state: "pending", progress: 0 };
  }
}

/** Overall fraction for the header (each stage weighs the same; skipped counts as complete). */
export function overallProgress(rows: StageRow[]): number {
  if (!rows.length) return 0;
  const sum = rows.reduce((a, r) => a + (r.state === "done" || r.state === "skipped" ? 1 : r.state === "running" ? (r.progress ?? 0.3) : 0), 0);
  return sum / rows.length;
}

export interface Counters {
  candidates: number | null;
  batchesDone: number;
  batchesTotal: number | null;
  episodes: number | null;
  experiments: number | null;
}

type DocX = AnalysisDoc & {
  batch_index?: Record<string, { n_episodes?: number }>;
  episodes_per_batch?: number;
  investigations?: Record<string, Investigation>;
  started_at?: number | string;
};

export function counters(doc: AnalysisDoc, inv?: Investigation | null): Counters {
  const d = doc as DocX;
  const st = (d.stages?.batches ?? d.stages?.simulation) as RawStage | undefined;
  const ids = (d.batches ?? []).map((b) => (typeof b === "string" ? b : b.batch_id));
  const done = ids.length;
  const p = typeof st?.progress === "number" ? st.progress : 0;
  const total = String(st?.state ?? "").startsWith("done") ? done : p > 0 && done > 0 ? Math.round(done / p) : null;
  let episodes: number | null = null;
  if (done) {
    const idx = d.batch_index ?? {};
    let sum = 0;
    let known = 0;
    for (const b of d.batches ?? []) {
      const n = typeof b === "string" ? idx[b]?.n_episodes : b.n_episodes;
      if (typeof n === "number") (sum += n), known++;
    }
    episodes = known === done ? sum : d.episodes_per_batch ? done * d.episodes_per_batch : known ? sum : null;
  }
  const exps = allExperiments(doc, inv).length;
  return { candidates: d.candidates?.length ? d.candidates.length : null, batchesDone: done, batchesTotal: total, episodes, experiments: exps || null };
}

function allExperiments(doc: AnalysisDoc, inv?: Investigation | null): Experiment[] {
  const byId = new Map<string, Experiment>();
  for (const i of Object.values((doc as DocX).investigations ?? {})) for (const e of i?.experiments ?? []) byId.set(e.experiment_id, e);
  for (const e of inv?.experiments ?? []) byId.set(e.experiment_id, e);
  return [...byId.values()];
}

/** Replace `[f_xxx]` tags with the fact value when known, otherwise drop the tag. */
export function resolveFactTags(text: string, facts: Fact[] | undefined): string {
  const map = new Map((facts ?? []).map((f) => [f.fact_id, f]));
  return text
    .replace(/\[(f_[A-Za-z0-9_]+)\]/g, (_, id: string) => {
      const f = map.get(id);
      if (!f || f.value == null) return "";
      const v = typeof f.value === "number" ? (Math.abs(f.value) < 1 && f.unit !== "m" ? f.value.toFixed(2) : String(+f.value.toFixed(2))) : f.value;
      return f.unit && f.unit !== "count" ? `${v} ${f.unit}` : String(v);
    })
    .replace(/\s{2,}/g, " ")
    .replace(/\s+([.,;])/g, "$1")
    .trim();
}

/** Latest experiment hypothesis (live investigation first, then those in analysis.json). */
export function latestHypothesis(doc: AnalysisDoc, inv?: Investigation | null): string | null {
  const facts: Fact[] = [];
  for (const i of [...Object.values((doc as DocX).investigations ?? {}), inv]) facts.push(...(((i as { facts?: Fact[] } | null | undefined)?.facts) ?? []));
  const exps = inv?.experiments?.length ? inv.experiments : allExperiments(doc, null);
  const last = [...exps].reverse().find((e) => e.hypothesis?.trim());
  return last ? resolveFactTags(last.hypothesis, facts) : null;
}

/** Tightest point along the route over all candidate robots (smallest margin), if any leg reports one. */
export function narrowestGap(doc: AnalysisDoc): { xy: XY; gap: number | null; leg: string } | null {
  const f = doc.feasibility;
  const list: Feasibility[] = Array.isArray(f) ? f : f ? Object.values(f) : [];
  let best: { xy: XY; gap: number | null; leg: string; margin: number } | null = null;
  for (const r of list)
    for (const l of r?.legs ?? []) {
      if (!l.narrowest_xy) continue;
      const margin = l.narrowest_gap_m != null ? l.narrowest_gap_m - (l.required_gap_m ?? 0) : Infinity;
      if (!best || margin < best.margin) best = { xy: l.narrowest_xy, gap: l.narrowest_gap_m ?? null, leg: `${l.from_wp} → ${l.to_wp}`, margin };
    }
  return best ? { xy: best.xy, gap: best.gap, leg: best.leg } : null;
}

export function startedAtMs(doc: AnalysisDoc): number | null {
  const s = (doc as DocX).started_at;
  if (typeof s === "number" && s > 0) return s < 1e12 ? s * 1000 : s;
  const c = typeof s === "string" ? s : doc.created_at;
  const t = c ? Date.parse(c) : NaN;
  return Number.isFinite(t) ? t : null;
}

export function formatElapsed(ms: number): string {
  const s = Math.max(0, Math.floor(ms / 1000));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const ss = String(s % 60).padStart(2, "0");
  return h ? `${h}:${String(m).padStart(2, "0")}:${ss}` : `${m}:${ss}`;
}

// ---------------------------------------------------------------- recorded-run replay (offline mode)

/** Seconds each stage takes in the replay of a recorded Clearance check. */
export const REPLAY_TIMINGS: Record<string, number> = { candidates: 2.2, feasibility: 2.6, batches: 9, investigation: 7, optimizer: 3, verdicts: 1.4, report: 2.2 };
const SKIPPED_REPLAY_S = 0.5;

function replayPlan(final: AnalysisDoc): { key: string; start: number; end: number }[] {
  let t = 0.6;
  return Object.keys(final.stages ?? {})
    .sort((a, b) => order(a) - order(b))
    .map((key) => {
      const skipped = String(final.stages[key]?.state ?? "").startsWith("skipped");
      const dur = skipped ? SKIPPED_REPLAY_S : (REPLAY_TIMINGS[key] ?? 1.5);
      const r = { key, start: t, end: t + dur };
      t += dur;
      return r;
    });
}

const order = (k: string) => {
  const i = (STAGE_ORDER as readonly string[]).indexOf(k === "simulation" ? "batches" : k);
  return i < 0 ? 99 : i;
};

export function replayDuration(final: AnalysisDoc): number {
  const p = replayPlan(final);
  return (p.length ? p[p.length - 1].end : 0) + 0.4;
}

/** The recorded analysis as it looked `t` seconds into the run; null once the replay is over. */
export function replayDoc(final: AnalysisDoc, t: number): AnalysisDoc | null {
  if (t >= replayDuration(final)) return null;
  const d = JSON.parse(JSON.stringify(final)) as DocX;
  const plan = replayPlan(final);
  const frac = (k: string) => {
    const s = plan.find((p) => p.key === k);
    return !s ? 1 : t <= s.start ? 0 : t >= s.end ? 1 : (t - s.start) / (s.end - s.start);
  };
  const stages: Record<string, StageStatus> = {};
  for (const s of plan) {
    const fin = final.stages[s.key];
    if (t >= s.end) stages[s.key] = fin;
    else if (t <= s.start) stages[s.key] = { state: "pending", progress: 0 };
    else {
      const f = (t - s.start) / (s.end - s.start);
      const tracked = ["feasibility", "batches", "simulation", "investigation", "optimizer"].includes(s.key);
      stages[s.key] = { state: "running", progress: tracked ? Math.max(0.02, f) : 0 };
    }
  }
  d.stages = stages;
  d.status = "running";
  d.started_at = undefined;
  // reveal the outputs in step with the stages
  const cands = final.candidates ?? [];
  d.candidates = cands.slice(0, Math.floor(cands.length * frac("candidates")));
  const fe = final.feasibility;
  const ff = frac("feasibility");
  if (fe && !Array.isArray(fe)) {
    const ents = Object.entries(fe);
    d.feasibility = Object.fromEntries(ents.slice(0, Math.ceil(ents.length * ff - 1e-9)));
  } else if (Array.isArray(fe)) d.feasibility = fe.slice(0, Math.ceil(fe.length * ff - 1e-9));
  const bs = final.batches ?? [];
  const bkey = final.stages.batches ? "batches" : "simulation";
  d.batches = bs.slice(0, Math.floor(bs.length * frac(bkey)));
  // like the orchestrator: batch progress = completed / planned
  if (stages[bkey]?.state === "running" && bs.length) stages[bkey] = { state: "running", progress: Math.max(0.02, d.batches.length / bs.length) };
  const fi = frac("investigation");
  const invs = Object.entries((final as DocX).investigations ?? {});
  const totalExp = invs.reduce((a, [, i]) => a + (i?.experiments?.length ?? 0), 0);
  let budget = Math.min(totalExp, Math.floor(totalExp * fi) + (fi > 0.15 ? 1 : 0));
  d.investigations = Object.fromEntries(
    invs.map(([k, i]) => {
      const n = Math.min(budget, i?.experiments?.length ?? 0);
      budget -= n;
      return [k, { ...i, experiments: (i?.experiments ?? []).slice(0, n) }];
    }),
  );
  if (frac("verdicts") < 1) d.verdicts = [];
  if (frac("report") < 1) d.report = null;
  return d;
}
