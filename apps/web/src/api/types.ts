import type { TwinAssetKey } from "../lib/assets";
import type { OccGrid } from "../lib/grid";
import type {
  AnalysisDoc,
  CoachMessage,
  CreateSiteRequest,
  CreateSiteResponse,
  EpisodeFrame,
  Fact,
  Investigation,
  LiveFrame,
  RegisterCaptureRequest,
  RegisterCaptureResponse,
  ReportResponse,
  RobotSpec,
  SimBatch,
  SiteDetail,
  SiteSummary,
  StartAnalysisRequest,
  StartLiveRequest,
  Twin,
  UsageSummary,
} from "../types";

export type ConnState = "connecting" | "open" | "closed" | "error";

export interface LiveConnection {
  onFrame(cb: (f: LiveFrame) => void): void;
  onState(cb: (s: ConnState) => void): void;
  sendJoystick(vx: number, vy: number): void;
  close(): void;
}

export interface CoachConnection {
  onMessage(cb: (m: CoachMessage) => void): void;
  onState(cb: (s: ConnState) => void): void;
  close(): void;
}

export interface Api {
  mode: "live" | "offline";
  listSites(): Promise<SiteSummary[]>;
  createSite(req: CreateSiteRequest): Promise<CreateSiteResponse>;
  getSite(id: string): Promise<SiteDetail>;
  getTwin(siteId: string, version?: number): Promise<Twin>;
  saveTwin(siteId: string, twin: Twin): Promise<Twin>;
  occupancy(twin: Twin): Promise<OccGrid>;
  listRobots(): Promise<RobotSpec[]>;
  startAnalysis(siteId: string, req: StartAnalysisRequest): Promise<{ analysis_id: string }>;
  getAnalysis(id: string): Promise<AnalysisDoc>;
  getReport(id: string): Promise<ReportResponse>;
  getFacts(id: string): Promise<Fact[]>;
  getInvestigation(id: string): Promise<Investigation | null>;
  getBatch(id: string): Promise<SimBatch>;
  getEpisode(batchId: string, n: number): Promise<EpisodeFrame[]>;
  /** Pinch Map grid (same shape as occupancy) or null when not available. */
  getPinchMap(batch: SimBatch, grid: OccGrid): Promise<Float32Array | null>;
  usageSummary(): Promise<UsageSummary>;
  registerCapture(siteId: string, req: RegisterCaptureRequest): Promise<RegisterCaptureResponse>;
  uploadFile(url: string, body: Blob, onProgress?: (frac: number) => void): Promise<void>;
  completeCapture(captureId: string): Promise<void>;
  startLive(req: StartLiveRequest): Promise<{ session_id: string }>;
  getLiveInfo(session: string): Promise<{ site_id: string; robot_id?: string | null; density?: string; mode?: string }>;
  openLive(session: string, role: "viewer" | "controller"): LiveConnection;
  openCoach(siteId: string): CoachConnection;
  markerPdfUrl(siteId: string): string;
  /** URL of a twin asset (GET /sites/{id}/twin/assets/{name}); absent in offline mode, which uses twin.assets as is. */
  twinAssetUrl?(twin: Twin, name: TwinAssetKey): string | null;
}
