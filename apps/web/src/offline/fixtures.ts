/**
 * Recorded Clearance checks for the reference sites, as served by services/api.
 * The JSON files in ./fixtures are written by `scripts/run_reference_checks.py --web` from the real pipeline
 * outputs in sample_sites/ (never edited by hand). Site bundles load lazily so each one is its own chunk.
 */
import type { OccGrid } from "../lib/grid";
import type { AnalysisDoc, Fact, Investigation, RobotSpec, SimBatch, Twin, XY } from "../types";
import indexJson from "./fixtures/index.json";

export interface ServerSite {
  site_id: string;
  name: string;
  type?: string;
  kind?: string;
  area_m2?: number | null;
  geometry_source?: "scan" | "reference";
  created_at?: string;
  latest_twin_version?: number | null;
  twin_version?: number | null;
}

export interface ServerUsage {
  by_model?: { model_id: string; calls: number; tokens_in: number; tokens_out: number; cost_usd?: number; duration_s?: number }[];
  by_kind?: { kind: string; calls: number; tokens_in: number; tokens_out: number; cost_usd?: number; duration_s?: number }[];
  totals?: { calls?: number; tokens_in?: number; tokens_out?: number; cost_usd?: number; duration_s?: number };
}

export interface FixtureIndex {
  generated_by: string;
  generated_at: string;
  sites: ServerSite[];
  robots: RobotSpec[];
  usage: ServerUsage;
  /** analysis id -> site id */
  analyses: Record<string, string>;
}

export interface AnalysisFixture {
  doc: AnalysisDoc;
  report: { markdown?: string; report_md?: string; md?: string; facts?: Fact[]; citations?: unknown[] } & Record<string, unknown>;
  facts: Fact[] | { facts: Fact[] };
  investigation: Investigation | null;
  batches: Record<string, SimBatch>;
  /** batch id -> recorded replay episodes (index n = position) */
  episodes: Record<string, unknown[]>;
  /** batch id -> Pinch Map as relative intensity: flat [row, col, value, ...] */
  pinch: Record<string, { shape: [number, number]; cells: number[] }>;
}

export interface SiteFixture {
  site: ServerSite & Record<string, unknown>;
  site_analyses: { analysis_id: string; status: string; created_at?: string }[];
  twin: Twin;
  occupancy: { width: number; height: number; res: number; origin: XY; rle: number[] };
  analyses: Record<string, AnalysisFixture>;
}

export const INDEX = indexJson as unknown as FixtureIndex;

const loaders = import.meta.glob<SiteFixture>(["./fixtures/*.json", "!./fixtures/index.json"], { import: "default" });
const cache = new Map<string, Promise<SiteFixture>>();

export function hasSiteFixture(siteId: string): boolean {
  return `./fixtures/${siteId}.json` in loaders;
}

export function loadSiteFixture(siteId: string): Promise<SiteFixture> {
  let p = cache.get(siteId);
  if (!p) {
    const load = loaders[`./fixtures/${siteId}.json`];
    if (!load) return Promise.reject(new Error(`no recorded data for site ${siteId}`));
    p = load();
    cache.set(siteId, p);
  }
  return p;
}

export async function loadAnalysisFixture(analysisId: string): Promise<{ site: SiteFixture; analysis: AnalysisFixture } | null> {
  const siteId = INDEX.analyses[analysisId];
  if (!siteId) return null;
  const site = await loadSiteFixture(siteId);
  const analysis = site.analyses[analysisId];
  return analysis ? { site, analysis } : null;
}

export async function findBatchFixture(batchId: string): Promise<{ analysisId: string; analysis: AnalysisFixture; batch: SimBatch } | null> {
  for (const aid of Object.keys(INDEX.analyses)) {
    const f = await loadAnalysisFixture(aid);
    const batch = f?.analysis.batches[batchId];
    if (f && batch) return { analysisId: aid, analysis: f.analysis, batch };
  }
  return null;
}

/** Occupancy grid from run-length pairs [value, count, value, count, ...] (row 0 = top, like occ.png). */
export function decodeGrid(o: SiteFixture["occupancy"]): OccGrid {
  const data = new Uint8Array(o.width * o.height);
  let k = 0;
  for (let i = 0; i + 1 < o.rle.length; i += 2) {
    data.fill(o.rle[i], k, k + o.rle[i + 1]);
    k += o.rle[i + 1];
  }
  if (k !== data.length) throw new Error(`occupancy run lengths cover ${k} of ${data.length} cells`);
  return { data, width: o.width, height: o.height, res: o.res, origin: [o.origin[0], o.origin[1]] };
}

/** Pinch Map (relative intensity) as a dense grid, or null when the shape differs from the occupancy grid. */
export function decodePinch(p: AnalysisFixture["pinch"][string] | undefined, grid: Pick<OccGrid, "width" | "height">): Float32Array | null {
  if (!p || p.shape[0] !== grid.height || p.shape[1] !== grid.width) return null;
  const out = new Float32Array(grid.width * grid.height);
  for (let i = 0; i + 2 < p.cells.length; i += 3) out[p.cells[i] * grid.width + p.cells[i + 1]] = p.cells[i + 2];
  return out;
}
