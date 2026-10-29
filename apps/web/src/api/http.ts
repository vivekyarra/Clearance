/**
 * Live-mode client for services/api (docs/CONTRACTS.md). The Vite dev server
 * proxies /api/* to http://localhost:8000/* (prefix stripped), including WebSockets.
 */
import { twinAssetUrl } from "../lib/assets";
import { coachFromWire } from "../lib/coach";
import { loadOccupancyPng, type OccGrid } from "../lib/grid";
import { parseNpy } from "../lib/npy";
import type {
  AnalysisDoc,
  CaptureStatus,
  CoachMessage,
  EpisodeFrame,
  Fact,
  Investigation,
  LiveFrame,
  ReportResponse,
  SimBatch,
  SiteDetail,
  SiteSummary,
  Twin,
  UsageSummary,
} from "../types";
import { authHeader, captureJoinTokenFromUrl, deviceToken, operatorToken, setDeviceToken, siteToken, urlToken } from "./auth";
import { installOperatorTokenEntry, openOperatorTokenDialog } from "./OperatorTokenDialog";
import type { Api, CoachConnection, ConnState, LiveConnection } from "./types";

// A /join/<site>?token=... landing carries the site join token; keep it before the join page routes on.
if (typeof window !== "undefined" && typeof location !== "undefined") {
  captureJoinTokenFromUrl();
  installOperatorTokenEntry();
}

export const API_BASE = (import.meta.env.VITE_API_BASE as string | undefined) ?? "/api";

export class ApiError extends Error {
  constructor(
    public status: number,
    message: string,
  ) {
    super(message);
  }
}

interface ReqAuth {
  /** Bearer token for this call; defaults to the stored operator token. */
  token?: string | null;
  /** Operator-only route: a 401 opens the operator token dialog and the call is retried once. */
  operator?: boolean;
}

async function req<T>(path: string, init: RequestInit = {}, auth: ReqAuth = {}): Promise<T> {
  const token = auth.token !== undefined ? auth.token : operatorToken();
  const res = await fetch(API_BASE + path, {
    ...init,
    headers: { Accept: "application/json", ...(init.body && !(init.body instanceof Blob) ? { "Content-Type": "application/json" } : {}), ...authHeader(token), ...init.headers },
  });
  if (res.status === 401 && auth.operator) {
    const entered = await openOperatorTokenDialog(token ? "The saved operator token was not accepted." : "This action needs the operator token.");
    if (entered) return req<T>(path, init, { ...auth, token: entered, operator: false });
  }
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`;
    try {
      const j = await res.json();
      msg = typeof j.detail === "string" ? j.detail : JSON.stringify(j.detail ?? j);
    } catch {
      /* body was not JSON */
    }
    throw new ApiError(res.status, msg);
  }
  const ct = res.headers.get("content-type") ?? "";
  if (ct.includes("json")) return (await res.json()) as T;
  return (await res.text()) as unknown as T;
}

const json = (body: unknown): RequestInit => ({ method: "POST", body: JSON.stringify(body) });
const OP: ReqAuth = { operator: true };

/** Upload targets from the last registrations: S3 presigned POSTs need their form fields. */
interface UploadTarget {
  method: string;
  fields?: Record<string, string>;
  file_field?: string;
  headers?: Record<string, string>;
}
const uploadTargets = new Map<string, UploadTarget>();

function withToken(path: string, token: string | null): string {
  if (!token) return path;
  return `${path}${path.includes("?") ? "&" : "?"}token=${encodeURIComponent(token)}`;
}

export function parseEpisode(raw: unknown): EpisodeFrame[] {
  if (Array.isArray(raw)) return raw as EpisodeFrame[];
  if (raw && typeof raw === "object" && Array.isArray((raw as { frames?: unknown }).frames)) return (raw as { frames: EpisodeFrame[] }).frames;
  if (typeof raw === "string")
    return raw
      .split(/\r?\n/)
      .filter((l) => l.trim())
      .map((l) => JSON.parse(l) as EpisodeFrame);
  throw new Error("unrecognised episode payload");
}

function wsUrl(path: string): string {
  const base = API_BASE.startsWith("http") ? API_BASE.replace(/^http/, "ws") : `${location.protocol === "https:" ? "wss" : "ws"}://${location.host}${API_BASE}`;
  return base + path;
}

class Emitter<T> {
  private cbs: ((v: T) => void)[] = [];
  on(cb: (v: T) => void) {
    this.cbs.push(cb);
  }
  emit(v: T) {
    for (const cb of this.cbs) cb(v);
  }
}

/** WebSocket with automatic reconnect (backoff up to 5 s). */
function socket(path: string, onMsg: (data: unknown) => void, state: Emitter<ConnState>) {
  let ws: WebSocket | null = null;
  let closed = false;
  let delay = 500;
  const connect = () => {
    state.emit("connecting");
    ws = new WebSocket(wsUrl(path));
    ws.onopen = () => {
      delay = 500;
      state.emit("open");
    };
    ws.onmessage = (e) => {
      try {
        onMsg(JSON.parse(e.data as string));
      } catch {
        /* ignore malformed frame */
      }
    };
    ws.onerror = () => state.emit("error");
    ws.onclose = () => {
      if (closed) return state.emit("closed");
      state.emit("connecting");
      setTimeout(connect, delay);
      delay = Math.min(5000, delay * 2);
    };
  };
  connect();
  return {
    send: (m: unknown) => ws?.readyState === WebSocket.OPEN && ws.send(JSON.stringify(m)),
    close: () => {
      closed = true;
      ws?.close();
    },
  };
}

export const httpApi: Api = {
  mode: "live",
  listSites: async () => {
    const r = await req<ServerSite[] | { sites: ServerSite[] }>("/sites");
    return (Array.isArray(r) ? r : r.sites).map(siteFromServer);
  },
  createSite: async (b) => {
    const r = await req<ServerSite & { join_url?: string; marker_pdf_url: string }>("/sites", json({ name: b.name, type: b.kind, area_m2: b.area_m2 ?? null }), OP);
    return { site_id: r.site_id, qr_payload: r.join_url ?? "", marker_pdf_url: r.marker_pdf_url, site: siteFromServer(r) };
  },
  getSite: async (id) => {
    const enc = encodeURIComponent(id);
    const [s, analyses, captures] = await Promise.all([
      req<ServerSite & SiteDetail>(`/sites/${enc}`),
      req<NonNullable<SiteDetail["analyses"]>>(`/sites/${enc}/analyses`).catch(() => []),
      req<ServerCapture[]>(`/sites/${enc}/captures`).catch(() => [] as ServerCapture[]),
    ]);
    const base = siteFromServer(s);
    return {
      ...s,
      ...base,
      captures: captures.map(captureFromServer),
      analyses: [...analyses].reverse(),
      latest_analysis_id: analyses.length ? analyses[analyses.length - 1].analysis_id : null,
    };
  },
  twinAssetUrl: (twin, name) => twinAssetUrl(API_BASE, twin, name),
  getTwin: (siteId, version) => req<Twin>(`/sites/${encodeURIComponent(siteId)}/twin${version ? `?version=${version}` : ""}`),
  saveTwin: (siteId, twin) => req<Twin>(`/sites/${encodeURIComponent(siteId)}/twin`, { method: "PUT", body: JSON.stringify(twin) }, OP),
  occupancy: (twin: Twin): Promise<OccGrid> =>
    loadOccupancyPng(`${API_BASE}/sites/${encodeURIComponent(twin.site_id)}/twin/occupancy.png?version=${twin.version ?? 1}`, twin.occupancy),
  listRobots: async () => {
    const r = await req<unknown>("/catalog/robots");
    return (Array.isArray(r) ? r : (r as { robots: [] }).robots) as never;
  },
  startAnalysis: (siteId, b) =>
    req(
      `/sites/${encodeURIComponent(siteId)}/clearance-checks`,
      json({ twin_version: b.twin_version, robot_ids: b.robots?.length ? b.robots : null, task: b.task, business: b.business, budget: { ...(b.budget ?? {}), densities: b.densities, template: b.template } }),
      OP,
    ),
  getAnalysis: (id) => req<AnalysisDoc>(`/analyses/${encodeURIComponent(id)}`),
  getReport: async (id) => {
    const r = await req<Partial<ReportResponse> & { report_md?: string; md?: string }>(`/analyses/${encodeURIComponent(id)}/report`);
    return { markdown: r.markdown ?? r.report_md ?? r.md ?? "", facts: r.facts ?? [], citations: r.citations ?? [] };
  },
  getFacts: async (id) => {
    const r = await req<Fact[] | { facts: Fact[] }>(`/analyses/${encodeURIComponent(id)}/facts`);
    return Array.isArray(r) ? r : r.facts;
  },
  getInvestigation: async (id) => {
    try {
      return await req<Investigation>(`/analyses/${encodeURIComponent(id)}/investigation`);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) return null;
      throw e;
    }
  },
  getBatch: (id) => req<SimBatch>(`/sim/batches/${encodeURIComponent(id)}`),
  getEpisode: async (batchId, n) => parseEpisode(await req<unknown>(`/sim/batches/${encodeURIComponent(batchId)}/episodes/${n}`)),
  getPinchMap: async (batch, grid) => {
    const res = await fetch(`${API_BASE}/sim/batches/${encodeURIComponent(batch.batch_id)}/bottleneck.npy`);
    if (!res.ok) return null;
    const arr = parseNpy(await res.arrayBuffer());
    if (arr.shape[0] !== grid.height || arr.shape[1] !== grid.width) return null;
    return arr.data;
  },
  usageSummary: async () => {
    const r = await req<UsageSummary | { by_model?: UsageSummary["rows"] }>("/usage/summary");
    if ("rows" in r && r.rows) return r as UsageSummary;
    return { rows: (r as { by_model?: UsageSummary["rows"] }).by_model ?? [] };
  },
  registerCapture: async (siteId, b) => {
    const names = Object.values(b.files ?? {}).filter((x): x is string => !!x);
    const token = siteToken(siteId);
    let r: { capture_id: string; device_token?: string; upload?: { files?: ({ name: string; url: string } & UploadTarget)[] } };
    try {
      r = await req(
        `/sites/${encodeURIComponent(siteId)}/captures`,
        json({ device: b.device, role: b.role, marker: b.marker, n_chunks: 0, files: [...names, ...(b.extra_files ?? [])], started_at: b.started_at ?? "", duration_s: b.duration_s ?? 0, privacy: b.privacy, capture_files: b.files }),
        { token },
      );
    } catch (e) {
      if (e instanceof ApiError && (e.status === 401 || e.status === 403))
        throw new ApiError(e.status, token ? "this join link has expired or is for another site; scan the site QR code again" : "open this page from the site QR code (it carries the upload permission)");
      throw e;
    }
    if (r.device_token) setDeviceToken(r.capture_id, r.device_token);
    for (const f of r.upload?.files ?? []) uploadTargets.set(f.url, { method: f.method ?? "PUT", fields: f.fields, file_field: f.file_field, headers: f.headers });
    return { capture_id: r.capture_id, upload_urls: Object.fromEntries((r.upload?.files ?? []).map((f) => [f.name, f.url])) };
  },
  uploadFile: (url, body, onProgress) =>
    new Promise((resolve, reject) => {
      // signed URLs authorize themselves: no bearer token is sent
      const target = uploadTargets.get(url) ?? { method: "PUT" };
      const xhr = new XMLHttpRequest();
      xhr.open(target.method, url);
      xhr.upload.onprogress = (e) => e.lengthComputable && onProgress?.(e.loaded / e.total);
      xhr.onload = () => (xhr.status < 300 ? resolve() : reject(new ApiError(xhr.status, xhr.status === 409 ? "already uploaded" : xhr.statusText)));
      xhr.onerror = () => reject(new Error("upload failed"));
      if (target.method === "POST" && target.fields) {
        // S3 presigned POST: policy fields first, the file last
        const form = new FormData();
        for (const [k, v] of Object.entries(target.fields)) form.append(k, v);
        form.append(target.file_field ?? "file", body);
        xhr.send(form);
        return;
      }
      for (const [k, v] of Object.entries(target.headers ?? {})) xhr.setRequestHeader(k, v);
      if (!Object.keys(target.headers ?? {}).some((k) => k.toLowerCase() === "content-type")) xhr.setRequestHeader("Content-Type", body.type || "application/octet-stream");
      xhr.send(body);
    }),
  completeCapture: async (id) => {
    await req(`/captures/${encodeURIComponent(id)}/complete`, { method: "POST" }, { token: deviceToken(id) ?? operatorToken() });
  },
  startLive: (b) => req("/live", json({ ...b, mode: "chaos" }), OP),
  getLiveInfo: async (session) => {
    const r = await req<{ site_id: string; robot_id?: string | null; mode?: string; params?: { density?: string }; density?: string }>(`/live/${encodeURIComponent(session)}`);
    return { site_id: r.site_id, robot_id: r.robot_id, mode: r.mode, density: r.density ?? r.params?.density };
  },
  openLive(session, role): LiveConnection {
    const frames = new Emitter<LiveFrame>();
    const state = new Emitter<ConnState>();
    let s: ReturnType<typeof socket> | null = null;
    queueMicrotask(() => {
      // joystick links carry the session token (?token=); the console uses the operator token
      const token = urlToken() ?? operatorToken();
      s = socket(withToken(`/live/${encodeURIComponent(session)}?role=${role === "viewer" ? "console" : "joystick"}`, token), (d) => {
        const m = d as LiveFrame & { type?: string };
        if (!m.type || m.type === "state") frames.emit(m);
      }, state);
    });
    return {
      onFrame: (cb) => frames.on(cb),
      onState: (cb) => state.on(cb),
      sendJoystick: (vx, vy) => s?.send({ type: "joystick", vx, vy }),
      close: () => s?.close(),
    };
  },
  openCoach(siteId): CoachConnection {
    const msgs = new Emitter<CoachMessage>();
    const state = new Emitter<ConnState>();
    let s: ReturnType<typeof socket> | null = null;
    const given = new URLSearchParams(location.search).get("session");
    const site = siteToken(siteId);
    const sessionP: Promise<{ sid: string; token: string | null }> = given
      ? Promise.resolve({ sid: given, token: urlToken() ?? site })
      : req<{ session_id: string; token?: string }>("/live", json({ site_id: siteId, mode: "coach" }), { token: site }).then((r) => ({ sid: r.session_id, token: r.token ?? site }));
    sessionP.then(
      ({ sid, token }) => {
        s = socket(withToken(`/live/${encodeURIComponent(sid)}?role=coach`, token), (d) => {
          for (const m of coachFromWire(d)) msgs.emit(m);
        }, state);
      },
      () => state.emit("error"),
    );
    return { onMessage: (cb) => msgs.on(cb), onState: (cb) => state.on(cb), close: () => s?.close() };
  },
  markerPdfUrl: (siteId) => `${API_BASE}/sites/${encodeURIComponent(siteId)}/marker.pdf`,
};

/** One row of GET /sites/{id}/captures. */
export interface ServerCapture {
  capture_id: string;
  site_id: string;
  role: "scanner" | "observer";
  tier: CaptureStatus["device"]["tier"];
  status: "registered" | "processing" | "done" | "stored" | "failed";
  device?: CaptureStatus["device"] | null;
  marker?: CaptureStatus["marker"] | null;
  privacy?: Record<string, unknown> | null;
  started_at?: string | null;
  duration_s?: number | null;
  error?: string | null;
  twin_version?: number | null;
  upload: NonNullable<CaptureStatus["upload"]>;
  processing: NonNullable<CaptureStatus["processing"]>;
}

export function captureFromServer(c: ServerCapture): CaptureStatus {
  const status: CaptureStatus["status"] =
    c.status === "registered" ? (c.upload.status === "waiting" ? "registered" : c.upload.status) : c.status === "stored" ? "done" : c.status;
  return {
    capture_id: c.capture_id,
    site_id: c.site_id,
    role: c.role,
    device: c.device ?? { model: c.tier, tier: c.tier },
    marker: c.marker ?? undefined,
    privacy: c.privacy ?? undefined,
    started_at: c.started_at ?? undefined,
    duration_s: c.duration_s ?? undefined,
    status,
    progress: c.status === "registered" ? c.upload.progress : undefined,
    error: c.error,
    upload: c.upload,
    processing: c.processing,
    twin_version: c.twin_version,
  };
}

interface ServerSite {
  site_id: string;
  name: string;
  join_url?: string;
  type?: string;
  kind?: string;
  area_m2?: number | null;
  geometry_source?: "scan" | "reference";
  created_at?: string;
  latest_twin_version?: number | null;
  twin_version?: number | null;
}

function siteFromServer(s: ServerSite): SiteSummary {
  return {
    site_id: s.site_id,
    name: s.name,
    kind: s.kind ?? s.type,
    area_m2: s.area_m2 ?? null,
    geometry_source: s.geometry_source,
    created_at: s.created_at,
    twin_version: s.twin_version ?? s.latest_twin_version ?? null,
    join_url: s.join_url,
  };
}

export { Emitter };
