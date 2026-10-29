"""Clearance frozen data contracts (docs/CONTRACTS.md). SI units, site frame: origin at marker center, Z up, floor Z=0.

Change these only with a note in docs/CONTRACTS.md. Every module imports from here; never redefine.
"""
from __future__ import annotations

from typing import Literal, Optional

from pydantic import BaseModel, Field

XY = tuple[float, float]


# ---------- Capture (6.1) ----------
class Device(BaseModel):
    model: str
    os: str = ""
    tier: Literal["T1_LIDAR", "T2_VIO_MONO", "T3_FIXED_OBSERVER", "T0_COACH"]


class Marker(BaseModel):
    family: str = "tag36h11"
    id: int = 0
    size_m: float = 0.160


class CaptureFiles(BaseModel):
    frames: Optional[str] = None      # dir of JPEGs, relative to capture root
    poses: Optional[str] = "poses.jsonl"
    depth: Optional[str] = None       # dir of uint16 mm PNGs named like frames (T1)
    confidence: Optional[str] = None  # dir of uint8 PNGs 0/1/2 (ARKit low/med/high)
    feature_points: Optional[str] = None  # jsonl: {"frame": "...", "points_world": [[x,y,z],...]}
    video: Optional[str] = None       # observer.mp4 / webm (observer)
    observer_pose: Optional[str] = None  # json: {"T_site_cam": [16], "K": [fx,fy,cx,cy], "w":, "h":, "convention"?}
    relative_depth: Optional[str] = None  # T2: dir of precomputed relative-depth maps named like frames


class Capture(BaseModel):
    capture_id: str
    site_id: str
    device: Device
    role: Literal["scanner", "observer"]
    marker: Marker = Marker()
    files: CaptureFiles = CaptureFiles()
    privacy: dict = {"faces_blurred_on_device": True}
    started_at: str = ""
    duration_s: float = 0.0
    # optional details written by the iOS app (kept verbatim; see docs/CONTRACTS.md "capture.json")
    anchoring: Optional[dict] = None  # {"method", "T_arkitworld_site": [16], "relocks"}
    clock: Optional[dict] = None      # {"t0_uptime_s", "t0_unix_s"}
    app: Optional[dict] = None        # {"version", "build", ...}
    stats: Optional[dict] = None      # {"keyframes", "rejected_blur", "limited_tracking_keyframes", ...}


class PoseRecord(BaseModel):
    """One line of poses.jsonl. T_world_cam is row-major 4x4, camera frame = ARKit convention
    (x right, y up, z backward / camera looks along -Z). World = site frame after marker anchoring."""
    t: float
    frame: str
    T_world_cam: list[float] = Field(min_length=16, max_length=16)
    K: list[float] = Field(min_length=4, max_length=4)  # fx, fy, cx, cy (pixels, at w x h)
    w: int
    h: int
    tracking: Literal["normal", "limited", "not_available"] = "normal"


# ---------- Twin (6.2) ----------
class Accuracy(BaseModel):
    method: Literal["tape_check", "estimated", "reference_ground_truth"] = "estimated"
    median_abs_err_m: Optional[float] = None
    p90_abs_err_m: Optional[float] = None


class Floor(BaseModel):
    plane: list[float] = [0.0, 0.0, 1.0, 0.0]
    bounds_polygon: list[XY] = []


class Occupancy(BaseModel):
    """PNG: 0 = occupied, 255 = free, 127 = unknown. Row 0 = max y (image convention, north up).
    Cell (row r, col c) center = (origin_x + (c+0.5)*res, origin_y + (H - r - 0.5)*res)."""
    uri: str
    resolution_m: float = 0.025
    origin_xy: XY = (0.0, 0.0)
    width: int = 0
    height: int = 0
    height_band_m: tuple[float, float] = (0.03, 2.0)


class Pose2(BaseModel):
    xyz: tuple[float, float, float]
    yaw: float = 0.0


class Primitive(BaseModel):
    id: str
    type: Literal["box", "cylinder"]
    label: str
    pose: Pose2
    size: tuple[float, float, float]  # box: l (along yaw), w, h ; cylinder: diameter, diameter, h
    movable: bool = False
    fragile: bool = False
    confidence: float = 1.0
    source: Literal["auto", "user", "reference"] = "auto"


class Threshold(BaseModel):
    id: str
    segment: tuple[XY, XY]
    height_m: Optional[float] = None
    status: Literal["measured", "flagged_manual", "user_entered"] = "measured"


class KeepoutZone(BaseModel):
    id: str
    polygon: list[XY]
    reason: str = ""


class ClearZone(BaseModel):
    """Floor the operator confirmed clear on site: inside it, unseen (UNKNOWN) cells count as free.
    Measured obstacles (OCCUPIED cells) are never cleared by a zone."""
    id: str
    polygon: list[XY]
    reason: str = "floor confirmed clear by operator"
    confirmed_by: Optional[str] = None
    confirmed_at: Optional[str] = None  # ISO 8601 UTC


class Waypoint(BaseModel):
    id: str
    xy: XY
    yaw: float = 0.0
    kind: Literal["pickup", "dock", "dest", "entrance", "exit"]


class Hazard(BaseModel):
    label: str
    xy: Optional[XY] = None
    source: str = "vision_llm"
    confidence: float = 0.5
    frame_refs: list[str] = []


class TrafficZone(BaseModel):
    id: str
    polygon: list[XY]


class TrafficModel(BaseModel):
    """Output of J5 or user estimate. od_pairs: (from_zone, to_zone, weight)."""
    source: Literal["observed", "user_estimated"] = "user_estimated"
    zones: list[TrafficZone] = []
    od_pairs: list[tuple[str, str, float]] = []
    persons_per_min: dict[str, float] = {"quiet": 2.0, "normal": 6.0, "peak": 14.0}
    walk_speed_mps: tuple[float, float] = (1.2, 0.25)  # mean, std
    heatmap_uri: Optional[str] = None


class Twin(BaseModel):
    twin_id: str
    site_id: str
    version: int = 1
    accuracy: Accuracy = Accuracy()
    floor: Floor = Floor()
    occupancy: Occupancy
    primitives: list[Primitive] = []
    thresholds: list[Threshold] = []
    keepout_zones: list[KeepoutZone] = []
    clear_zones: list[ClearZone] = []
    waypoints: list[Waypoint] = []
    hazards: list[Hazard] = []
    traffic: TrafficModel = TrafficModel()
    assets: dict[str, Optional[str]] = {}
    geometry_source: Literal["scan", "reference"] = "scan"  # "reference" = room built from known ground-truth geometry


# ---------- RobotSpec (6.3) ----------
class FieldSource(BaseModel):
    url: str
    quote: str
    retrieved_at: str


class Price(BaseModel):
    value: Optional[float] = None
    currency: str = "USD"
    is_public: bool = False
    note: str = "price on request"
    source_url: Optional[str] = None


class RobotSpec(BaseModel):
    robot_id: str
    robot_class: Literal["A_service", "B_amr"] = Field(alias="class")
    vendor: str
    model: str
    footprint_polygon_m: Optional[list[XY]] = None  # centered on rotation center, +x = forward
    footprint_approx: bool = False
    height_m: Optional[float] = None
    drive: Literal["diff_drive", "omni", "ackermann"] = "diff_drive"
    in_place_rotation: bool = True
    min_turn_radius_m: float = 0.0
    max_speed_mps: Optional[float] = None
    nominal_speed_mps: Optional[float] = None
    max_threshold_m: Optional[float] = None
    max_slope_deg: Optional[float] = None
    min_aisle_width_vendor_m: Optional[float] = None
    payload_kg: Optional[float] = None
    runtime_h: Optional[float] = None
    safety_stop_distance_m: Optional[float] = None
    price: Price = Price()
    fields_sources: dict[str, FieldSource] = {}
    verified_by_human: bool = False
    notes: str = ""

    model_config = {"populate_by_name": True}


# ---------- Sim (6.4) ----------
class Task(BaseModel):
    type: Literal["delivery"] = "delivery"
    sequence: list[str]  # waypoint ids


Density = Literal["none", "quiet", "normal", "peak"]
Bound = Literal["optimistic", "nominal", "conservative"]


class BatchResults(BaseModel):
    success_rate: float
    min_gap_m: Optional[float] = None  # median over episodes of the smallest robot-to-obstacle gap
    ci95: tuple[float, float]
    median_mission_s: Optional[float] = None
    near_miss_per_mission: float = 0.0
    stops_per_mission: float = 0.0
    fail_reasons: dict[str, int] = {}
    n_success: int = 0
    n_episodes: int = 0


class SimBatch(BaseModel):
    batch_id: str
    twin_id: str
    twin_version: int
    robot_id: str
    task: Task
    density: Density
    geometry_bound: Bound
    n_episodes: int
    seed_base: int
    code_version: str = "dev"
    results: Optional[BatchResults] = None
    episodes_uri: Optional[str] = None
    bottleneck_uri: Optional[str] = None  # .npy float32 grid same shape as occupancy
    wall_time_s: Optional[float] = None


class LegFeasibility(BaseModel):
    from_wp: str
    to_wp: str
    reachable: Optional[bool]  # None = unknown (flagged threshold)
    narrowest_gap_m: Optional[float] = None   # free width at the tightest point of the best path
    required_gap_m: Optional[float] = None     # robot width (footprint at path heading) needed there
    narrowest_xy: Optional[XY] = None
    path_length_m: Optional[float] = None
    blocking_threshold: Optional[str] = None
    reason: str = ""


class Feasibility(BaseModel):
    robot_id: str
    twin_id: str
    twin_version: int
    legs: list[LegFeasibility]
    all_reachable: Optional[bool]


# ---------- Fact (6.5) ----------
class FactOrigin(BaseModel):
    type: Literal["sim_batch", "measurement", "source_url", "user_input", "formula", "investigation"]
    ref: str


class Fact(BaseModel):
    fact_id: str
    value: float | str | None
    unit: str = ""
    label: str
    origin: FactOrigin


# ---------- Investigation loop ----------
class InvestigationStep(BaseModel):
    """One tool call inside the loop. Numbers in result_summary come from code only."""
    i: int
    thought: str = ""
    tool: Optional[str] = None
    args: dict = {}
    result_summary: dict = {}
    fact_ids: list[str] = []


class Experiment(BaseModel):
    """One entry of the investigation trail shown as "How we investigated" in the Clearance Report."""
    experiment_id: str
    robot_id: str
    hypothesis: str               # e.g. "fails at the counter corridor at peak traffic"
    kind: Literal["reduce_pedestrians_zone", "move_object", "remove_object", "change_density",
                  "swap_robot", "measure_threshold", "other"]
    change: dict = {}             # overrides / density / robot_id / zone_id used by the probe
    batch_id: Optional[str] = None
    baseline_fact_ids: list[str] = []
    result_fact_ids: list[str] = []
    outcome: Literal["supported", "refuted", "inconclusive"] = "inconclusive"
    next_action: Literal["dig_further", "conclude"] = "conclude"
    conclusion: str = ""          # may cite only [f_xxx] tags from this trail


class Finding(BaseModel):
    finding_id: str
    robot_id: str
    claim: str                      # text with [f_xxx] tags
    evidence_fact_ids: list[str]
    location_xy: Optional[XY] = None
    object_ids: list[str] = []
    confirmed_by_probe: bool = False  # a probe batch tested the counterfactual
    suggested_change: Optional[dict] = None  # overrides dict (see docs/CONTRACTS.md)


class Investigation(BaseModel):
    investigation_id: str
    analysis_id: str
    model_id: str
    mode: Literal["live", "offline"] = "offline"
    steps: list[InvestigationStep] = []
    experiments: list[Experiment] = []   # cap: 6 per robot
    findings: list[Finding] = []
    facts: list[Fact] = []               # every fact registered by investigation tools
    stop_reason: str = ""


# ---------- Verdict (decided by code: clearance_core/verdict.py) ----------
VerdictLabel = Literal["Cleared", "Cleared with changes", "Not cleared"]


class VerdictCheck(BaseModel):
    rule: str                      # human-readable rule, e.g. "success at normal traffic (conservative) >= 0.90"
    passed: Optional[bool]         # None = unknown (e.g. flagged threshold not measured)
    value: Optional[float | str] = None
    threshold: Optional[float] = None
    fact_ids: list[str] = []


class Verdict(BaseModel):
    robot_id: str
    label: VerdictLabel
    main_reason: str               # e.g. "no path: narrowest gap 0.62 m vs 0.70 m needed"
    as_is: list[VerdictCheck]
    with_changes: list[VerdictCheck] = []
    required_changes: list[dict] = []   # optimizer changes and/or "measure threshold D1"
    config: dict = {}              # thresholds used (from config/sim_defaults.yaml)
