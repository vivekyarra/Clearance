/**
 * Hand-written mirror of clearance_core/schemas.py (frozen contracts, PLAN Section 6).
 * Field names, optionality and defaults follow the pydantic models exactly.
 * Python tuples become fixed-length TS tuples; Optional[...] becomes `T | null`.
 * Fields with pydantic defaults are optional (`?`) because the server may omit them
 * (it serialises with model_dump(mode="json", by_alias=True), so in practice they are present).
 *
 * Below the "API envelopes" marker are shapes the HTTP API wraps around the frozen models.
 * Those are NOT in clearance_core and are written against PLAN Section 11 + docs/CONTRACTS.md.
 */

export type XY = [number, number];

// ---------- Capture (6.1) ----------
export type DeviceTier = "T1_LIDAR" | "T2_VIO_MONO" | "T3_FIXED_OBSERVER" | "T0_COACH";

export interface Device {
  model: string;
  os?: string;
  tier: DeviceTier;
}

export interface Marker {
  family?: string; // "tag36h11"
  id?: number; // 0
  size_m?: number; // 0.160
}

export interface CaptureFiles {
  frames?: string | null;
  poses?: string | null; // "poses.jsonl"
  depth?: string | null;
  confidence?: string | null;
  feature_points?: string | null;
  video?: string | null;
  observer_pose?: string | null;
  relative_depth?: string | null; // T2: precomputed relative-depth maps named like frames
}

export interface Capture {
  capture_id: string;
  site_id: string;
  device: Device;
  role: "scanner" | "observer";
  marker?: Marker;
  files?: CaptureFiles;
  privacy?: Record<string, unknown>; // {"faces_blurred_on_device": true}
  started_at?: string;
  duration_s?: number;
}

export interface PoseRecord {
  t: number;
  frame: string;
  T_world_cam: number[]; // 16, row-major
  K: [number, number, number, number];
  w: number;
  h: number;
  tracking?: "normal" | "limited" | "not_available";
}

/** observer_pose json (CaptureFiles.observer_pose): {"T_site_cam": [16], "K": [fx,fy,cx,cy], "w":, "h":} */
export interface ObserverPose {
  T_site_cam: number[];
  K: [number, number, number, number];
  w: number;
  h: number;
  [extra: string]: unknown;
}

// ---------- Twin (6.2) ----------
export interface Accuracy {
  method?: "tape_check" | "estimated" | "reference_ground_truth";
  median_abs_err_m?: number | null;
  p90_abs_err_m?: number | null;
}

export interface Floor {
  plane?: number[]; // [a,b,c,d]
  bounds_polygon?: XY[];
}

export interface Occupancy {
  uri: string;
  resolution_m?: number; // 0.025
  origin_xy?: XY;
  width?: number;
  height?: number;
  height_band_m?: [number, number];
}

export interface Pose2 {
  xyz: [number, number, number];
  yaw?: number;
}

export interface Primitive {
  id: string;
  type: "box" | "cylinder";
  label: string;
  pose: Pose2;
  size: [number, number, number]; // box: l (along yaw), w, h ; cylinder: d, d, h
  movable?: boolean;
  fragile?: boolean;
  confidence?: number;
  source?: "auto" | "user" | "reference";
}

export type ThresholdStatus = "measured" | "flagged_manual" | "user_entered";

export interface Threshold {
  id: string;
  segment: [XY, XY];
  height_m?: number | null;
  status?: ThresholdStatus;
}

export interface KeepoutZone {
  id: string;
  polygon: XY[];
  reason?: string;
}

/** Floor the operator confirmed clear on site: unseen (grey) cells inside count as free; measured obstacles stay. */
export interface ClearZone {
  id: string;
  polygon: XY[];
  reason?: string;
  confirmed_by?: string | null;
  /** ISO 8601 UTC; the API stamps it on save when missing */
  confirmed_at?: string | null;
}

export type WaypointKind = "pickup" | "dock" | "dest" | "entrance" | "exit";

export interface Waypoint {
  id: string;
  xy: XY;
  yaw?: number;
  kind: WaypointKind;
}

export interface Hazard {
  label: string;
  xy?: XY | null;
  source?: string;
  confidence?: number;
  frame_refs?: string[];
}

export interface TrafficZone {
  id: string;
  polygon: XY[];
}

export interface TrafficModel {
  source?: "observed" | "user_estimated";
  zones?: TrafficZone[];
  od_pairs?: [string, string, number][];
  persons_per_min?: Record<string, number>;
  walk_speed_mps?: [number, number];
  heatmap_uri?: string | null;
}

export interface Twin {
  twin_id: string;
  site_id: string;
  version?: number;
  accuracy?: Accuracy;
  floor?: Floor;
  occupancy: Occupancy;
  primitives?: Primitive[];
  thresholds?: Threshold[];
  keepout_zones?: KeepoutZone[];
  clear_zones?: ClearZone[];
  waypoints?: Waypoint[];
  hazards?: Hazard[];
  traffic?: TrafficModel;
  assets?: Record<string, string | null>;
  /** "reference" = room built from known ground-truth geometry */
  geometry_source?: "scan" | "reference";
}

// ---------- RobotSpec (6.3) ----------
export interface FieldSource {
  url: string;
  quote: string;
  retrieved_at: string;
}

export interface Price {
  value?: number | null;
  currency?: string;
  is_public?: boolean;
  note?: string;
  source_url?: string | null;
}

export interface RobotSpec {
  robot_id: string;
  /** serialised by alias */
  class: "A_service" | "B_amr";
  vendor: string;
  model: string;
  footprint_polygon_m?: XY[] | null;
  footprint_approx?: boolean;
  height_m?: number | null;
  drive?: "diff_drive" | "omni" | "ackermann";
  in_place_rotation?: boolean;
  min_turn_radius_m?: number;
  max_speed_mps?: number | null;
  nominal_speed_mps?: number | null;
  max_threshold_m?: number | null;
  max_slope_deg?: number | null;
  min_aisle_width_vendor_m?: number | null;
  payload_kg?: number | null;
  runtime_h?: number | null;
  safety_stop_distance_m?: number | null;
  price?: Price;
  fields_sources?: Record<string, FieldSource>;
  verified_by_human?: boolean;
  notes?: string;
}

// ---------- Sim (6.4) ----------
export interface Task {
  type?: "delivery";
  sequence: string[];
}

export type Density = "none" | "quiet" | "normal" | "peak";
export type Bound = "optimistic" | "nominal" | "conservative";

export interface BatchResults {
  success_rate: number;
  /** median over episodes of the smallest robot-to-obstacle gap */
  min_gap_m?: number | null;
  ci95: [number, number];
  median_mission_s?: number | null;
  near_miss_per_mission?: number;
  stops_per_mission?: number;
  fail_reasons?: Record<string, number>;
  n_success?: number;
  n_episodes?: number;
}

export interface SimBatch {
  batch_id: string;
  twin_id: string;
  twin_version: number;
  robot_id: string;
  task: Task;
  density: Density;
  geometry_bound: Bound;
  n_episodes: number;
  seed_base: number;
  code_version?: string;
  results?: BatchResults | null;
  episodes_uri?: string | null;
  bottleneck_uri?: string | null;
  wall_time_s?: number | null;
}

export interface LegFeasibility {
  from_wp: string;
  to_wp: string;
  reachable: boolean | null;
  /** free width at the tightest point of the best path */
  narrowest_gap_m?: number | null;
  /** robot width (footprint at path heading) needed there */
  required_gap_m?: number | null;
  narrowest_xy?: XY | null;
  path_length_m?: number | null;
  blocking_threshold?: string | null;
  reason?: string;
}

export interface Feasibility {
  robot_id: string;
  twin_id: string;
  twin_version: number;
  legs: LegFeasibility[];
  all_reachable: boolean | null;
}

// ---------- Fact (6.5) ----------
export type FactOriginType =
  | "sim_batch"
  | "measurement"
  | "source_url"
  | "user_input"
  | "formula"
  | "investigation";

export interface FactOrigin {
  type: FactOriginType;
  ref: string;
}

export interface Fact {
  fact_id: string;
  value: number | string | null;
  unit?: string;
  label: string;
  origin: FactOrigin;
}

// ---------- Investigation loop (PLAN 7.12) ----------
export interface InvestigationStep {
  i: number;
  thought?: string;
  tool?: string | null;
  args?: Record<string, unknown>;
  result_summary?: Record<string, unknown>;
  fact_ids?: string[];
}

export type ExperimentKind =
  | "reduce_pedestrians_zone"
  | "move_object"
  | "remove_object"
  | "change_density"
  | "swap_robot"
  | "measure_threshold"
  | "other";

/** One entry of the investigation trail ("How we investigated"). */
export interface Experiment {
  experiment_id: string;
  robot_id: string;
  hypothesis: string;
  kind: ExperimentKind;
  change?: Record<string, unknown>;
  batch_id?: string | null;
  baseline_fact_ids?: string[];
  result_fact_ids?: string[];
  outcome?: "supported" | "refuted" | "inconclusive";
  next_action?: "dig_further" | "conclude";
  conclusion?: string;
}

export interface Finding {
  finding_id: string;
  robot_id: string;
  claim: string;
  evidence_fact_ids: string[];
  location_xy?: XY | null;
  object_ids?: string[];
  confirmed_by_probe?: boolean;
  suggested_change?: Record<string, unknown> | null;
}

export interface Investigation {
  investigation_id: string;
  analysis_id: string;
  model_id: string;
  mode?: "live" | "offline";
  steps?: InvestigationStep[];
  experiments?: Experiment[];
  findings?: Finding[];
  stop_reason?: string;
}

// ---------- Verdict (decided by code: clearance_core/verdict.py) ----------
export type VerdictLabel = "Cleared" | "Cleared with changes" | "Not cleared";

export interface VerdictCheck {
  rule: string;
  passed: boolean | null; // null = unknown (e.g. threshold awaiting measurement)
  value?: number | string | null;
  threshold?: number | null;
  fact_ids?: string[];
}

export interface Verdict {
  robot_id: string;
  label: VerdictLabel;
  main_reason: string;
  as_is: VerdictCheck[];
  with_changes?: VerdictCheck[];
  required_changes?: Record<string, unknown>[];
  config?: Record<string, unknown>;
}

// ---------- Episode JSONL (CONTRACTS.md, 10 Hz) ----------
export type EpisodeEventType = "stop" | "near_miss" | "collision" | "fail" | "success" | "replan";

export interface EpisodeEvent {
  type: EpisodeEventType;
  xy: XY;
  detail?: unknown;
}

export interface EpisodeFrame {
  t: number;
  robot: [number, number, number, number]; // x, y, yaw, v
  peds: XY[];
  events: EpisodeEvent[];
}

// =====================================================================
// API envelopes (not in clearance_core; built against PLAN Section 11 / CONTRACTS.md)
// =====================================================================

export type SiteKind = "cafe" | "restaurant" | "storeroom" | "warehouse" | "corridor" | "office" | "other";

export interface SiteSummary {
  site_id: string;
  name: string;
  kind?: SiteKind | string;
  area_m2?: number | null;
  geometry_source?: "scan" | "reference";
  created_at?: string;
  twin_version?: number | null;
  latest_analysis_id?: string | null;
  /** The string in the site QR code: <web>/join/<site_id>?api=<api base>&name=<name> */
  join_url?: string;
}

export interface CreateSiteRequest {
  name: string;
  kind: SiteKind | string;
  area_m2?: number | null;
}

export interface CreateSiteResponse {
  site_id: string;
  qr_payload: string;
  marker_pdf_url: string;
  site?: SiteSummary;
}

/** Capture as tracked by the API (Capture + upload progress). */
export interface CaptureStatus extends Capture {
  status?: "registered" | "uploading" | "uploaded" | "processing" | "done" | "failed";
  progress?: number; // 0..1
  bytes_uploaded?: number;
  bytes_total?: number;
  error?: string | null;
  upload?: { status: "waiting" | "uploading" | "uploaded"; n_expected: number; n_uploaded: number; progress: number };
  processing?: { status: string; twin_version?: number | null; error?: string | null };
  twin_version?: number | null;
}

export interface SiteDetail extends SiteSummary {
  captures?: CaptureStatus[];
  twin_versions?: number[];
  analyses?: { analysis_id: string; status: string; created_at?: string }[];
  twin_status?: { state: string; progress?: number; message?: string } | null;
}

export interface RegisterCaptureRequest {
  device: Device;
  role: "scanner" | "observer";
  marker?: Marker;
  files?: CaptureFiles;
  privacy?: Record<string, unknown>;
  started_at?: string;
  duration_s?: number;
  /** additional file names to upload (e.g. still frames) */
  extra_files?: string[];
}

export interface RegisterCaptureResponse {
  capture_id: string;
  /** file name -> presigned PUT URL */
  upload_urls?: Record<string, string>;
}

export interface BusinessInputs {
  staff_cost_per_h: number;
  task_hours_per_day: number;
  days_per_week: number;
  price_mode: "purchase" | "lease" | "public_price";
  robot_price?: number | null;
  monthly_lease?: number | null;
  maintenance_pct_per_year: number;
  currency: string;
}

export interface StartAnalysisRequest {
  task: Task;
  template: string;
  robots?: string[];
  business: BusinessInputs;
  densities?: Density[];
  budget?: Record<string, unknown>;
  twin_version?: number;
}

export type StageState = "pending" | "running" | "done" | "failed" | "skipped";

export interface StageStatus {
  state: StageState;
  progress?: number; // 0..1
  message?: string;
}

export interface RoiResult {
  monthly_saving: number | null;
  payback_months: number | null;
  formula: string;
  inputs: Record<string, number | string | null>;
}

export interface OptimizerChange {
  change: {
    move?: Record<string, { dx?: number; dy?: number; dyaw?: number }>;
    remove?: string[];
    add_boxes?: Primitive[];
  };
  description: string;
  before: BatchResults;
  after: BatchResults;
  batch_ids?: string[];
  robot_id?: string;
}

/** analyses/<id>/analysis.json */
export interface AnalysisDoc {
  analysis_id: string;
  site_id?: string;
  status: "queued" | "running" | "done" | "failed" | string;
  created_at?: string;
  stages: Record<string, StageStatus>;
  inputs?: Partial<StartAnalysisRequest> & { site_id?: string; twin_version?: number };
  candidates?: (string | { robot_id: string; reason?: string; excluded?: boolean })[];
  feasibility?: Feasibility[] | Record<string, Feasibility>;
  /** CONTRACTS: list of batch ids; the UI also accepts inlined SimBatch objects. */
  batches?: (string | SimBatch)[];
  optimizer?: OptimizerChange[] | { changes?: OptimizerChange[] } | null;
  investigation_id?: string | null;
  verdicts?: Verdict[];
  roi?: Record<string, RoiResult> | null;
  report?: unknown;
  error?: string | null;
}

export interface Citation {
  id?: string;
  url: string;
  title?: string;
  retrieved_at?: string;
  quote?: string;
  quotes?: string[];
  used_for?: string[];
}

export interface ReportResponse {
  markdown: string;
  facts: Fact[];
  citations: Citation[];
}

export interface UsageRow {
  kind?: string;
  role?: string;
  model_id: string;
  calls: number;
  tokens_in: number;
  tokens_out: number;
  duration_s?: number;
  cost_usd?: number | null;
}

export interface UsageSummary {
  rows: UsageRow[];
  totals?: { calls?: number; tokens_in?: number; tokens_out?: number; cost_usd?: number | null };
  mode?: string;
}

/** WS /live/{session} server -> client (CONTRACTS LiveSession.step) */
export interface LiveFrame {
  t: number;
  robot: { x: number; y: number; yaw: number; v: number; state: string };
  peds: XY[];
  events: EpisodeEvent[];
  controlled_ped?: number;
}

export interface StartLiveRequest {
  site_id: string;
  robot_id: string;
  density: Density;
  task?: Task;
}

/** Coach message on the wire (WS /live/{session}, docs/CONTRACTS.md "Coach message"). */
export interface CoachWire {
  type: "coach";
  site_id: string;
  /** cells row-major, row 0 = max y; 0 unseen, 1 one direction, 2+ two or more directions */
  coverage: { cell_m: number; origin: XY; width: number; height: number; cells: number[] } | null;
  prompt: string | null;
  t: number;
  status?: { device?: string; frames?: number; tracking?: string; keyframes?: number; covered_frac?: number };
}

/** Coach page updates (lib/coach.ts splits a CoachWire message into these). */
export type CoachMessage =
  | { type: "coverage"; cell_m: number; origin: XY; width: number; height: number; cells: number[] }
  | { type: "prompt"; text: string; level?: "info" | "warn"; t?: number }
  | { type: "status"; device?: string; frames?: number; tracking?: string };
