/**
 * Offline mode: the Api surface served from the recorded Clearance checks of the reference sites
 * (sample_sites/ served through services/api, exported by scripts/run_reference_checks.py --web).
 * Sites, twins, robots, reports, facts, investigations, batches, replays and Pinch Maps are the real pipeline
 * outputs. New Clearance checks need services/api; the live session and scan coach run local models.
 */
import type { OccGrid } from "../lib/grid";
import { hashStr, mulberry32 } from "../lib/rng";
import type { Api, CoachConnection, ConnState, LiveConnection } from "../api/types";
import { Emitter } from "../api/http";
import type { AnalysisDoc, CoachMessage, Density, EpisodeFrame, Fact, LiveFrame, SiteDetail, SiteSummary, Twin, UsageSummary, XY } from "../types";
import { CATALOG_ROBOTS } from "./catalog";
import { decodeGrid, decodePinch, findBatchFixture, hasSiteFixture, INDEX, loadAnalysisFixture, loadSiteFixture } from "./fixtures";
import { rasterizeTwinOffline, referenceSites } from "./sites";
import { LiveModel, makePlanner, missionPath } from "./sim";

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms));
const clone = <T,>(v: T): T => JSON.parse(JSON.stringify(v)) as T;

/** Planner radius for the local live model (m). */
const ROUTE_RADIUS = 0.18;

interface Store {
  created: SiteSummary[];
  /** site -> twins: the recorded version first, then versions saved in this browser */
  twins: Map<string, Twin[]>;
  grids: Map<string, OccGrid>; // `${site}@${version}`
}

const KEY = "clearance.offline.v2";
let store: Store | null = null;

function getStore(): Store {
  if (store) return store;
  const s: Store = { created: [], twins: new Map(), grids: new Map() };
  try {
    const saved = JSON.parse(localStorage.getItem(KEY) ?? "null") as { created?: SiteSummary[] } | null;
    for (const c of saved?.created ?? []) if (!hasSiteFixture(c.site_id)) addBlankSite(s, c);
  } catch {
    /* storage unavailable: start clean */
  }
  store = s;
  return s;
}

function persist() {
  try {
    localStorage.setItem(KEY, JSON.stringify({ created: getStore().created }));
  } catch {
    /* ignore */
  }
}

function addBlankSite(s: Store, summary: SiteSummary) {
  // A new site has no scan yet: an all-unknown grid until captures arrive.
  const grid: OccGrid = { data: new Uint8Array(80 * 60).fill(127), width: 80, height: 60, res: 0.1, origin: [-4, -3] };
  const twin: Twin = {
    twin_id: `twin_${summary.site_id}`,
    site_id: summary.site_id,
    version: 0,
    occupancy: { uri: "", resolution_m: 0.1, origin_xy: [-4, -3], width: 80, height: 60 },
    geometry_source: "scan",
  };
  s.created.push(summary);
  s.twins.set(summary.site_id, [twin]);
  s.grids.set(`${summary.site_id}@0`, grid);
}

async function twinList(siteId: string): Promise<Twin[]> {
  const s = getStore();
  let list = s.twins.get(siteId);
  if (!list) {
    if (!hasSiteFixture(siteId)) throw new Error(`site ${siteId} not found`);
    const f = await loadSiteFixture(siteId);
    list = [f.twin];
    s.twins.set(siteId, list);
    s.grids.set(`${siteId}@${f.twin.version ?? 1}`, decodeGrid(f.occupancy));
  }
  return list;
}

async function twinVersion(siteId: string, version?: number): Promise<Twin> {
  const list = await twinList(siteId);
  const t = version ? list.find((x) => (x.version ?? 1) === version) : list[list.length - 1];
  if (!t) throw new Error(`twin ${siteId} v${version} not found`);
  return t;
}

async function gridFor(twin: Twin): Promise<OccGrid> {
  const s = getStore();
  const list = await twinList(twin.site_id);
  const k = `${twin.site_id}@${twin.version ?? 1}`;
  let g = s.grids.get(k);
  if (!g) {
    const base = s.grids.get(`${twin.site_id}@${list[0]?.version ?? 1}`)!;
    g = rasterizeTwinOffline(twin, base);
    s.grids.set(k, g);
  }
  return g;
}

function parseEpisode(raw: unknown): EpisodeFrame[] {
  if (Array.isArray(raw)) return raw as EpisodeFrame[];
  if (raw && typeof raw === "object" && Array.isArray((raw as { frames?: unknown }).frames)) return (raw as { frames: EpisodeFrame[] }).frames;
  throw new Error("unrecognised episode payload");
}

export function usageFromServer(): UsageSummary {
  const u = INDEX.usage;
  return {
    mode: "offline",
    rows: (u.by_model ?? []).map((r) => ({ model_id: r.model_id, calls: r.calls, tokens_in: r.tokens_in, tokens_out: r.tokens_out, duration_s: r.duration_s, cost_usd: r.cost_usd ?? 0 })),
    totals: { calls: u.totals?.calls, tokens_in: u.totals?.tokens_in, tokens_out: u.totals?.tokens_out, cost_usd: u.totals?.cost_usd ?? 0 },
  };
}

async function analysisFixture(id: string) {
  const f = await loadAnalysisFixture(id);
  if (!f) throw new Error(`Clearance check ${id} not found (offline mode holds the recorded checks of the reference sites)`);
  return f;
}

export const offlineApi: Api = {
  mode: "offline",
  async listSites() {
    await wait(60);
    return [...referenceSites(), ...getStore().created];
  },
  async createSite(req) {
    await wait(150);
    const s = getStore();
    const id = `site_${req.name.toLowerCase().replace(/[^a-z0-9]+/g, "_").replace(/^_|_$/g, "").slice(0, 24) || "new"}_${(Date.now() % 100000).toString(36)}`;
    const summary: SiteSummary = { site_id: id, name: req.name, kind: req.kind, area_m2: req.area_m2 ?? null, geometry_source: "scan", created_at: new Date().toISOString(), twin_version: null, latest_analysis_id: null };
    addBlankSite(s, summary);
    persist();
    return { site_id: id, qr_payload: `${location.origin}/observe/${id}`, marker_pdf_url: offlineApi.markerPdfUrl(id), site: summary };
  },
  async getSite(id) {
    await wait(60);
    const created = getStore().created.find((c) => c.site_id === id);
    const versions = (await twinList(id)).map((t) => t.version ?? 1);
    if (created) {
      return { ...created, twin_version: null, captures: [], twin_versions: versions, analyses: [], twin_status: { state: "waiting_for_capture", progress: 0, message: "Scan the QR code with each phone to join" } };
    }
    const f = await loadSiteFixture(id);
    const analyses = [...f.site_analyses].reverse();
    const detail: SiteDetail = {
      ...f.site,
      ...referenceSites().find((x) => x.site_id === id),
      twin_version: versions[versions.length - 1] ?? null,
      captures: [],
      twin_versions: versions,
      analyses,
      latest_analysis_id: analyses[0]?.analysis_id ?? null,
      twin_status: { state: "ready", progress: 1 },
    };
    return detail;
  },
  async getTwin(siteId, version) {
    return clone(await twinVersion(siteId, version));
  },
  async saveTwin(siteId, twin) {
    await wait(150);
    const list = await twinList(siteId);
    const next: Twin = { ...clone(twin), version: (list[list.length - 1]?.version ?? 0) + 1 };
    next.occupancy = { ...next.occupancy, uri: `sites/${siteId}/twin_v${next.version}/occ.png` };
    list.push(next);
    await gridFor(next);
    return clone(next);
  },
  async occupancy(twin) {
    return gridFor(twin);
  },
  async listRobots() {
    return clone(CATALOG_ROBOTS);
  },
  async startAnalysis(siteId, req) {
    await wait(150);
    const list = await twinList(siteId);
    const recorded = list[0];
    const v = req.twin_version ?? list[list.length - 1]?.version;
    if (!hasSiteFixture(siteId) || (v ?? 1) !== (recorded.version ?? 1))
      throw new Error("Offline mode holds the recorded Clearance checks of the reference sites. Start services/api to run a new check on this twin.");
    const f = await loadSiteFixture(siteId);
    const latest = f.site_analyses[f.site_analyses.length - 1];
    if (!latest) throw new Error(`no recorded Clearance check for ${siteId}`);
    return { analysis_id: latest.analysis_id };
  },
  async getAnalysis(id) {
    const { analysis } = await analysisFixture(id);
    return clone(analysis.doc) as AnalysisDoc;
  },
  async getReport(id) {
    const { analysis } = await analysisFixture(id);
    const r = analysis.report;
    const facts = Array.isArray(analysis.facts) ? analysis.facts : analysis.facts.facts;
    return clone({ markdown: r.markdown ?? r.report_md ?? r.md ?? "", facts: (r.facts as Fact[] | undefined) ?? facts, citations: (r.citations ?? []) as never });
  },
  async getFacts(id) {
    const { analysis } = await analysisFixture(id);
    return clone(Array.isArray(analysis.facts) ? analysis.facts : analysis.facts.facts);
  },
  async getInvestigation(id) {
    const { analysis } = await analysisFixture(id);
    return analysis.investigation ? clone(analysis.investigation) : null;
  },
  async getBatch(id) {
    const f = await findBatchFixture(id);
    if (!f) throw new Error(`batch ${id} not found`);
    return clone(f.batch);
  },
  async getEpisode(batchId, n) {
    const f = await findBatchFixture(batchId);
    const eps = f?.analysis.episodes[batchId];
    if (!eps || !eps.length) throw new Error(`batch ${batchId} has no recorded replay in offline mode (each robot's replay batch is normal traffic, nominal geometry)`);
    if (n < 0 || n >= eps.length) throw new Error(`episode ${n} is not in the recorded replay set (episodes 0-${eps.length - 1})`);
    return parseEpisode(eps[n]);
  },
  async getPinchMap(batch, grid) {
    const f = await findBatchFixture(batch.batch_id);
    return decodePinch(f?.analysis.pinch[batch.batch_id], grid);
  },
  async usageSummary() {
    return usageFromServer();
  },
  async registerCapture(siteId, req) {
    await wait(150);
    const id = `cap_${siteId}_${(Date.now() % 1e6).toString(36)}`;
    const files = Object.values(req.files ?? {}).filter(Boolean) as string[];
    return { capture_id: id, upload_urls: Object.fromEntries(files.map((f) => [f, `offline://upload/${id}/${f}`])) };
  },
  async uploadFile(_url, body, onProgress) {
    const steps = Math.max(4, Math.min(20, Math.round(body.size / 200000)));
    for (let i = 1; i <= steps; i++) {
      await wait(90);
      onProgress?.(i / steps);
    }
  },
  async completeCapture() {
    await wait(100);
  },
  async startLive(req) {
    await wait(120);
    return { session_id: `lv.${req.site_id}.${req.robot_id}.${req.density}.${Math.random().toString(36).slice(2, 7)}` };
  },
  async getLiveInfo(session) {
    const [, site_id, robot_id, density] = session.split(".");
    if (!site_id) throw new Error(`live session ${session} not found`);
    return { site_id, robot_id, density, mode: "chaos" };
  },
  openLive(session, role): LiveConnection {
    const frames = new Emitter<LiveFrame>();
    const state = new Emitter<ConnState>();
    const chan = typeof BroadcastChannel !== "undefined" ? new BroadcastChannel(`clearance-live-${session}`) : null;
    let timer: ReturnType<typeof setInterval> | null = null;
    let model: LiveModel | null = null;
    if (role === "viewer") {
      const [, siteId, robotId, density] = session.split(".");
      void (async () => {
        try {
          const twin = await twinVersion(siteId);
          const grid = await gridFor(twin);
          const robot = CATALOG_ROBOTS.find((r) => r.robot_id === robotId) ?? CATALOG_ROBOTS[0];
          const seq = (twin.waypoints ?? []).filter((w) => w.kind !== "entrance" && w.kind !== "exit").map((w) => w.id);
          const dock = seq.find((id) => id.startsWith("DOCK")) ?? seq[0];
          const path = missionPath(twin, makePlanner(grid, ROUTE_RADIUS), [...seq, dock]);
          if (!path) throw new Error("no route");
          model = new LiveModel(twin, grid, path, { seed: hashStr(session), density: (density as Density) || "normal", speed: robot.nominal_speed_mps ?? 0.5, safetyStop: robot.safety_stop_distance_m ?? 0.5 });
          state.emit("open");
          let k = 0;
          timer = setInterval(() => {
            const f = model!.step();
            frames.emit(f);
            if (chan && (k++ % 4 === 0 || f.events.length)) chan.postMessage({ type: "frame", frame: f });
          }, 50);
        } catch {
          state.emit("error");
        }
      })();
      chan?.addEventListener("message", (e: MessageEvent) => {
        if (e.data?.type === "joystick") model?.setJoystick(e.data.vx, e.data.vy);
      });
    } else {
      queueMicrotask(() => state.emit(chan ? "open" : "error"));
      chan?.addEventListener("message", (e: MessageEvent) => {
        if (e.data?.type === "frame") frames.emit(e.data.frame as LiveFrame);
      });
    }
    return {
      onFrame: (cb) => frames.on(cb),
      onState: (cb) => state.on(cb),
      sendJoystick: (vx, vy) => {
        if (model) model.setJoystick(vx, vy);
        else chan?.postMessage({ type: "joystick", vx, vy });
      },
      close: () => {
        if (timer) clearInterval(timer);
        chan?.close();
        state.emit("closed");
      },
    };
  },
  openCoach(siteId): CoachConnection {
    const msgs = new Emitter<CoachMessage>();
    const state = new Emitter<ConnState>();
    const rnd = mulberry32(hashStr(siteId));
    const g = getStore().grids.get(`${siteId}@1`);
    const cell = 0.5;
    const w = g ? Math.ceil((g.width * g.res) / cell) : 16;
    const h = g ? Math.ceil((g.height * g.res) / cell) : 12;
    const origin: XY = g ? g.origin : [-4, -3];
    const cells = new Array(w * h).fill(0);
    const prompts = [
      "Keep the marker in view for 2 seconds to anchor the scan",
      "Walk slowly along the counter",
      "Left corner not covered yet",
      "Too fast: slow down to keep frames sharp",
      "Point the phone at the floor near the door",
      "Scan the gap between the tables from both sides",
      "Coverage looks good: finish with one loop around the room",
    ];
    let walker: XY = [w / 2, h / 2];
    let k = 0;
    let timer: ReturnType<typeof setInterval> | null = null;
    queueMicrotask(() => {
      state.emit("open");
      timer = setInterval(() => {
        walker = [Math.max(0, Math.min(w - 1, walker[0] + (rnd() - 0.5) * 3)), Math.max(0, Math.min(h - 1, walker[1] + (rnd() - 0.5) * 3))];
        for (let dr = -1; dr <= 1; dr++)
          for (let dc = -1; dc <= 1; dc++) {
            const r = Math.round(walker[1]) + dr;
            const c = Math.round(walker[0]) + dc;
            if (r >= 0 && c >= 0 && r < h && c < w && rnd() < 0.6) cells[r * w + c] = Math.min(4, cells[r * w + c] + 1);
          }
        msgs.emit({ type: "coverage", cell_m: cell, origin, width: w, height: h, cells: cells.slice() });
        if (k % 8 === 0) msgs.emit({ type: "prompt", text: prompts[(k / 8) % prompts.length], level: k % 24 === 16 ? "warn" : "info", t: Date.now() / 1000 });
        if (k % 4 === 0) msgs.emit({ type: "status", device: "iPhone 13", frames: 40 + k * 6, tracking: "normal" });
        k++;
      }, 700);
    });
    return {
      onMessage: (cb) => msgs.on(cb),
      onState: (cb) => state.on(cb),
      close: () => {
        if (timer) clearInterval(timer);
        state.emit("closed");
      },
    };
  },
  markerPdfUrl: () => "/marker-tag36h11-id0.svg",
};
