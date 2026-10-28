# Clearance — Project Plan v1.2

> **Clearance: Know if a robot fits before you buy it.**

> v1.2 changes: product renamed to Clearance; the measurement formerly called "clearance" is now "gap"; product vocabulary (Clearance check, Clearance Report, Cleared / Cleared with changes / Not cleared, Pinch Map); code-decided verdicts (7.13); Cosmos Reason 2 is the primary scene model; investigation trail shown as "How we investigated"; real-world-first positioning (Section 2.6).
> v1.1 changes: added 7.12 Nemotron investigation loop; Phase 0.5 go/no-go gate (4-hour scan-to-occupancy test, run first).

> **One line:** Scan a space with ordinary phones. Clearance builds a measured digital twin, finds which commercial robots can actually work there, simulates them thousands of times against your real foot traffic, and tells you what to buy, what to move, and what it pays back — with every number traceable to a simulation run or a cited source.

**Hackathon:** Nebius x NVIDIA Global AI Hackathon (Devpost)
**Track:** Physical AI
**Also competing for:** Grand/2nd/3rd, Best Use of Tavily, Most Valuable Feedback, City Winner (only if we attended an IRL event)
**Hard deadline:** Oct 30, 2026, 10:30 PM IST (GMT+5:30). See Section 14 for the cut line.

---

## 0. How to use this document

- Every team member reads Sections 1–6 fully before writing code.
- Each workstream owner then lives in their section (7.x) plus Sections 12–16.
- Anything marked **VERIFY** must be confirmed on Day 1 and the result written into `docs/VERIFIED.md`. Nothing marked VERIFY goes into the README, video, or Devpost text until it is confirmed.
- Priorities: **P0** = required for submission. **P1** = strongly improves winning odds. **P2** = only after P0 and P1 are done.

---

## 1. Stress test: what survives, what changes, what gets cut

We reviewed our own earlier plan and the external ("Argon") review line by line. Decisions below are final unless new evidence appears.

### 1.1 Decisions on the external review

| # | Claim in review | Verdict | Decision |
|---|---|---|---|
| 1 | ARKit/ARCore visual-inertial odometry gives metric-scale camera poses on non-LiDAR phones | **Correct** | Adopt. iPhone 13 uses ARKit poses (meters) directly. The printed marker becomes a site origin and an accuracy check, not the only source of scale. |
| 2 | Use Depth Anything V2 Metric + MASt3R/DUSt3R for dense depth | **License problem** | DUSt3R/MASt3R are CC BY-NC-SA (non-commercial). Depth Anything V2 Base/Large/Giant are CC BY-NC; only **Small is Apache-2.0**. We use DA-V2 Small (relative depth) aligned per-frame to ARKit's metric feature points, or LiDAR depth when available. **VERIFY** license files before use. |
| 3 | Decouple visuals (Gaussian splat) from physics (clean primitives) | **Correct, and we go further** | Adopt. Plus: our MVP robot classes are wheeled, so the primary simulator is a footprint-accurate 2D navigation sim on an occupancy grid. Fast, honest, and debuggable. 3D physics is P2. |
| 4 | "500 parallel rollouts at 10,000+ FPS on Serverless Jobs" | **Unverified, irrelevant** | Not a claim we make. We report measured episodes/minute and cost per report from our own logs. |
| 5 | Exact model names: "Nemotron 3.5 Super VL", "Nemotron 3 Nano Omni", "Cosmos 3 two-tower", "Ultra 550B", "Super 120B" | **Partly confirmed** | Other teams' public repos for this hackathon reference Nemotron 3.5 Super VL, Cosmos Reason 2 (self-hosted on Nebius AI Cloud via vLLM), Cosmos 3, and GR00T N1.7, so these likely exist. Parameter counts and "Nano Omni" are still unconfirmed. Everything stays **VERIFY** against the Token Factory model list (`GET /v1/models`) on Day 1. README lists only model IDs we actually called, with call counts from our logs. |
| 6 | Nano Omni as a real-time capture coach | **Wrong layer** | Blur, coverage, and marker visibility are checked on-device with deterministic code (Laplacian variance, pose coverage grid). The LLM only turns the coverage report into short instructions. Cloud LLM latency in a capture loop is bad UX. |
| 7 | Tavily pulls real prices for every robot | **Partly true** | Many vendors don't publish prices ("contact sales"). The system must output "price not public" with the source, accept user-entered quotes, and never invent a number. |
| 8 | Robot list: Unitree G1, Agility Digit, Boston Dynamics Stretch, Bear Servi, Locus Origin | **Mixed** | Humanoids and truck-unloading robots are out of MVP scope (no reliable public specs for the tasks, and manipulation can't be credibly simulated from a phone scan). MVP = wheeled service/delivery robots and indoor AMRs. |
| 9 | "Robot A: 38% collision rate" framing | **Dangerous** | All results are labeled **simulated estimates**, shown as ranges across scan uncertainty, with sample size. Never phrased as real-world performance. |
| 10 | Joystick "chaos override" | **Good demo, wrong hardware** | Keep as P1, but the controller is a phone web page (touch joystick). No purchase needed. |
| 11 | NVIDIA Omniverse NuRec | **Optional** | Our baseline is `gsplat`. NuRec only if access and license are confirmed (**VERIFY**). |
| 12 | Phones satisfy the Physical AI hardware clip rule | **Correct** | We still record at least 60 continuous seconds of phones operating in the real space. |

### 1.2 Decisions on our own earlier plan

| Earlier idea | Verdict | Decision |
|---|---|---|
| GR00T humanoid policies trialed in the twin | Research-grade, not credible in our scope | **Cut from MVP.** P2 experiment at most. Not mentioned in the video unless it works. |
| Cosmos generates lighting/clutter variations for the sim | Visual variation doesn't change 2D navigation outcomes | **Replace.** Geometry variation (chair jitter, clutter, pedestrian density, scan error) is done in the sim. Cosmos Reason is used for **scene understanding** (hazards, doors, fragile items). Cosmos video generation is P2. |
| Phone LiDAR required | No | Adaptive capture tiers (Section 7.1). |
| Android phones as handheld scanners | Browser access to ARCore poses is unreliable | Android phones are **fixed observers** (traffic + extra views), localized exactly via the printed marker. Handheld Android scanning is P2. |
| AR hero shot needs LiDAR occlusion | No | Occlusion from our own reconstructed mesh anchored to the marker, plus ARKit people occlusion (iPhone 13 supports it). |

### 1.3 Things the review missed that can sink us

1. **Judges can't scan their own room.** The hosted demo must ship with 3 preloaded real sites and an interactive report, plus optional upload.
2. **Privacy and consent.** Filming real cafés/shops means filming people. We need written owner permission, on-device face blurring, and no raw faces stored.
3. **Threshold detection limits.** A 2.5 cm door lip is near the noise floor of a ±2–4 cm scan. The product must flag "measure manually" instead of pretending.
4. **Robot 3D models are vendor IP.** We render robots as parametric shapes generated from their spec dimensions. No vendor meshes, logos, or photos in the app.
5. **Waypoints don't come from a scan.** "Kitchen pass" and "Table 7" must be placed by a human. This makes the twin editor a P0 feature, not polish.
6. **Ultralytics YOLO is AGPL-3.0.** Use Apache/MIT-licensed detectors.

---

## 2. Product definition

### 2.1 Problem

Restaurants, cafés, clinics, small warehouses, and storerooms are being sold service robots and AMRs. Whether a robot will work depends on the specific site: aisle widths, turning space, door thresholds, clutter, and human traffic at peak hours. Today that answer comes from a vendor site visit (biased toward selling) or a paid pilot (slow and costly). The owner has no independent, cheap, fast way to answer "will this robot work here, and which one?"

### 2.2 Users

| User | Job to be done | MVP? |
|---|---|---|
| **Operator** (café/restaurant owner, small warehouse manager) | "Tell me if a robot can do this job here, which one, what I must change, and the payback." | Yes, primary |
| **Integrator / vendor pre-sales** | "Pre-qualify a site remotely before sending an engineer." | Secondary; mentioned in pitch as the business channel |

### 2.3 Value proposition

From a 10-minute phone scan to a cited Clearance Report in about 20 minutes, covering:
- Which robots can physically reach every required location (pass/fail per robot per task).
- Simulated success rate, delivery time, and near-misses at quiet/normal/peak traffic.
- A Pinch Map showing where robots get stuck.
- Specific layout changes ("move table T4 18 cm toward the wall") with their simulated effect.
- Payback period from the owner's own numbers.
- An AR replay of the chosen robot doing the job in the real room.

### 2.4 MVP scope

**Robot classes in scope:**
- **Class A: Wheeled service/delivery robots** (restaurant tray robots).
- **Class B: Indoor AMRs** (tote/cart movers in storerooms and small warehouses).

**Task type in scope:** point-to-point delivery missions (pickup → destination(s) → return to dock).

**Out of scope (non-goals):** humanoids, robot arms/manipulation, outdoor sites, multi-floor/elevators, stairs, real-time fleet management, vendor certification. Say so plainly in the README.

### 2.5 Honesty principles (these are product features)

1. Every number in the report links to its origin: a simulation batch ID, a measured scan value, a cited URL, or a user input.
2. Results are shown as ranges across scan uncertainty (optimistic/conservative geometry).
3. Unknowns are shown as unknown ("price not public", "threshold: measure manually").
4. Simulated results are labeled as simulated estimates everywhere.

### 2.6 Positioning: the real world first (decision, v1.2)

Many Physical AI entries are simulation-only stress-testing tools. Our differentiator is the real world. In the video, README and hosted demo, lead with phones scanning a real space and the AR robot in the real room. Keep simulation dashboards brief. The cardboard robot validation (15.3) and, if possible, a real site owner on camera are **P1 and not optional polish**.

### 2.7 Vocabulary

- The product is **Clearance**. "Clearance" alone only ever means the product or its verdicts. The measured free width is a **gap** (`narrowest_gap_m`, `min_gap_m`; UI: "narrowest gap on route: 0.74 m").
- The analysis is a **Clearance check** (button: "Run Clearance check"). The output is the **Clearance Report**.
- Each robot gets exactly one verdict: **Cleared**, **Cleared with changes**, or **Not cleared** (7.13).
- The bottleneck heatmap is the **Pinch Map**.

---

## 3. Hackathon fit

### 3.1 Requirements checklist (from the hackathon page)

| Requirement | How we meet it | Owner |
|---|---|---|
| Runs on Nebius Token Factory or AI Cloud | Nemotron via Token Factory; reconstruction, sim jobs, and self-hosted vision model on Nebius AI Cloud / Serverless | Backend |
| At least one NVIDIA open model | Nemotron 3 Nano/Super/Ultra (confirmed on page); Cosmos Reason (**VERIFY** availability) | Agents |
| Track choice | Physical AI | — |
| Project description | Devpost text, Section 18 | Product |
| Working demo URL | Optional for Physical AI; we ship one anyway with 3 preloaded sites | Frontend |
| Demo video ≤3 min, public YouTube | Section 17 script | Product |
| ≥1 min footage of hardware operating (Physical AI) | 60+ s continuous clip of phones scanning/observing + AR | Capture |
| Public repo with OSI license visible at top | Apache-2.0, `LICENSE` at root | All |
| README with setup + how Nemotron, Token Factory, Nebius services are used | Section 18.1 outline | All |
| Feedback on Token Factory, AI Cloud, NVIDIA tools | Running `FEEDBACK.md` log during build | All |
| Pre-existing project disclosure | Not applicable (new project). State "built during submission period." | Product |
| City selection | Only if we attended an IRL event | Product |

**VERIFY on Day 1:** read the full Official Rules for (a) whether one project can win a placement prize plus track and special prizes, (b) team size limits, (c) any rules on third-party data and filming people.

### 3.2 Judging criteria mapping

| Criterion | What judges must see | Where |
|---|---|---|
| Technological implementation | Real pipeline on Nebius: capture → twin → batch sim on Serverless Jobs → Nemotron agents with tools → report. Model tiering with logged call counts and cost. Tests. | Video 1:15–2:00, README metrics table |
| Design | Complete product flow: capture app → console → twin editor → report → AR. Looks like something you'd pay for. | Video and hosted demo |
| Potential impact | Specific buyer, specific expensive decision, validated against a real-world check (cardboard footprint test). | Video 0:00–0:15 and 2:30–3:00, README validation section |
| Quality of idea | Non-obvious use: Nemotron + Tavily as an independent robot procurement analyst grounded in a measured twin. | Whole pitch |

### 3.3 Tavily prize strategy

Tavily must be load-bearing, not decorative:
- **Catalog builder:** Tavily Search finds datasheets/spec pages; Tavily Extract pulls page content; Nemotron Nano converts it to structured specs with a supporting quote per field.
- **Live market check in each report:** current pricing/lease info where public, deployment reviews, and applicable safety-standard references, all cited.
- **Shown in the UI:** every spec value has a source chip linking to the URL and retrieval date.
- **VERIFY:** current Tavily API endpoints, rate limits, and free credits for the hackathon.

---

## 4. User journey (the product, end to end)

1. **Create site** in the web console: name, type (café/restaurant or storeroom/warehouse), floor area estimate. Console shows a QR code and a printable marker PDF.
2. **Place marker** (printed AprilTag, measured size) flat on the floor near the entrance. This defines the site origin.
3. **Join devices:** each phone scans the QR. The app detects its capabilities and assigns a tier (Section 7.1).
4. **Fixed observers:** Android phones go on stands with the marker in view for 2 seconds (auto-localization), then record traffic (default 30 min, blurred on-device).
5. **Scan:** the iPhone walks the space. The capture coach (on the M30s screen and on the iPhone) shows coverage and prompts ("left corner not covered", "too fast").
6. **Upload** happens in the background. Console shows progress per device.
7. **Twin ready** (~10 min): console shows the top-down twin and the splat view. Detected objects are listed with confidence.
8. **Review twin:** user fixes labels, marks movable objects, adds keep-out zones, places waypoints (dock, pickup, tables/shelves), and confirms or manually enters any flagged threshold.
9. **Define job:** choose a task template ("deliver from pickup to tables") and enter business inputs (staff cost/hour, deliveries/day, hours open).
10. **Run Clearance check:** system selects candidate robots from the catalog, runs static feasibility, then Monte Carlo batches on Serverless Jobs, then the layout optimizer.
11. **Report:** ranked robots, ranges, Pinch Map, recommended moves, payback, citations. Every number is clickable.
12. **Replay:** watch any episode in the splat viewer, or open the iPhone app and see it in AR in the real room.
13. **Export:** PDF and share link.

---

## 5. System architecture

```
                         ┌──────────────────────────────────────────────┐
  PHONES                 │                 NEBIUS                       │
 ┌───────────────┐       │                                              │
 │ iPhone (iOS   │──────▶│  API service (FastAPI, container)            │
 │ app: ARKit    │ HTTPS │   on Serverless Endpoint or AI Cloud VM      │
 │ scan + AR)    │◀──────│   ├─ Postgres (sites, jobs, results)         │
 └───────────────┘       │   ├─ Object Storage (S3): captures, twins    │
 ┌───────────────┐       │   └─ Job dispatcher ──────────────┐          │
 │ Android PWA   │──────▶│                                   ▼          │
 │ fixed observer│       │  Serverless Jobs / GPU VM                    │
 │ (blur on-dev) │       │   ├─ J1 ingest+sync         (CPU)            │
 └───────────────┘       │   ├─ J2 reconstruct+fuse    (GPU)            │
 ┌───────────────┐       │   ├─ J3 splat train         (GPU)            │
 │ Phone joystick│──WS──▶│   ├─ J4 segment+primitives  (GPU)            │
 │ (chaos mode)  │       │   ├─ J5 traffic tracks      (GPU/CPU)        │
 └───────────────┘       │   ├─ J6 sim shards x N      (CPU, parallel)  │
                         │   └─ J7 optimizer           (CPU, parallel)  │
  LAPTOP                 │                                              │
 ┌───────────────┐       │  Vision model endpoint (Cosmos Reason or     │
 │ Web console   │◀─────▶│   Nemotron VL, self-hosted vLLM) VERIFY      │
 │ (Next.js +    │       │                                              │
 │ three.js +    │       │  Token Factory: Nemotron Nano / Super / Ultra│
 │ splat viewer) │       └──────────────────────────────────────────────┘
 └───────────────┘                    │
                                      ▼
                              Tavily Search / Extract
```

### 5.1 Components

| Component | Responsibility | Tech |
|---|---|---|
| `apps/ios` | Scan capture (frames, poses, intrinsics, LiDAR depth if present), marker anchoring, AR replay | Swift, SwiftUI, ARKit, RealityKit |
| `apps/web` | Console, twin editor, report, splat viewer, Android observer PWA, phone joystick page | Next.js, React, TypeScript, three.js, GaussianSplats3D, MediaPipe Tasks (web) |
| `services/api` | Auth-light sessions, site CRUD, presigned uploads, job orchestration, results API, WebSocket for live sim | Python 3.11, FastAPI, SQLAlchemy, Pydantic |
| `jobs/recon` | Sync, depth alignment, TSDF fusion, floor fit, occupancy grid, mesh export | Open3D, OpenCV, NumPy, DA-V2 Small |
| `jobs/splat` | Gaussian splat training from posed frames | gsplat |
| `jobs/semantics` | Open-vocabulary segmentation, lift to 3D, primitive fitting, hazard tagging | Grounding DINO, SAM 2, Open3D, vision LLM |
| `jobs/traffic` | Person detection, tracking, floor projection, heatmaps, origin-destination pairs | Apache/MIT detector, ByteTrack, OpenCV |
| `jobs/sim` | Static feasibility, Monte Carlo navigation sim, metrics | NumPy, Numba, Shapely, pysocialforce |
| `jobs/optimizer` | Layout change search | Uses `jobs/sim` |
| `agents/` | Catalog builder, orchestrator, report writer, fact checker | Token Factory (OpenAI-compatible client), Tavily SDK |
| `catalog/` | Verified robot specs with sources | JSON + review UI |

### 5.2 Key design decisions and trade-offs

| Decision | Chosen | Rejected | Why |
|---|---|---|---|
| Sim fidelity | 2D footprint-accurate nav sim | Full 3D physics (Isaac/MuJoCo) | Wheeled robots on flat floors: outcomes are dominated by geometry and traffic. 2D is 100x faster, debuggable, and explainable. 3D is P2 for validation only. |
| Geometry source | Fused depth + editable primitives | Raw mesh into physics | Raw meshes are noisy; humans can verify primitives. |
| Android role | Fixed observers localized by marker | Handheld scanning | Exact pose from marker; no ARCore browser dependency. |
| LLM role | Planning, extraction, tool orchestration, writing | Geometry or numbers | LLMs don't produce measurements. All numbers come from code. |
| Robot rendering | Parametric shapes from specs | Vendor 3D models | IP-safe, and visually honest about what's simulated. |

---

## 6. Data model (freeze these schemas on Day 1)

All units are SI (meters, seconds, kilograms, radians). Coordinate frame: site frame, origin at marker center, Z up, floor at Z=0 after floor fit.

### 6.1 `Capture` (per device upload)

```json
{
  "capture_id": "cap_...",
  "site_id": "site_...",
  "device": {"model": "iPhone13,2", "os": "iOS 18", "tier": "T2_VIO_MONO"},
  "role": "scanner | observer",
  "marker": {"family": "tag36h11", "id": 0, "size_m": 0.160},
  "files": {"frames": "s3://.../frames/", "poses": "poses.jsonl", "depth": "depth/ (optional)", "video": "observer.mp4 (observer)"},
  "privacy": {"faces_blurred_on_device": true},
  "started_at": "ISO8601", "duration_s": 412
}
```

`poses.jsonl` line: `{"t": 12.345, "frame": "000123.jpg", "T_world_cam": [16 floats, row-major], "K": [fx, fy, cx, cy], "w": 1920, "h": 1440, "tracking": "normal"}`

### 6.2 `Twin`

```json
{
  "twin_id": "twin_...", "site_id": "site_...", "version": 3,
  "accuracy": {"method": "tape_check | estimated", "median_abs_err_m": 0.021, "p90_abs_err_m": 0.038},
  "floor": {"plane": [a, b, c, d], "bounds_polygon": [[x, y], ...]},
  "occupancy": {"uri": "s3://.../occ.png", "resolution_m": 0.025, "origin_xy": [x, y], "height_band_m": [0.03, 2.0]},
  "primitives": [
    {"id": "T4", "type": "box | cylinder", "label": "table", "pose": {"xyz": [x, y, z], "yaw": 0.12},
     "size": [l, w, h], "movable": true, "fragile": false, "confidence": 0.87, "source": "auto | user"}
  ],
  "thresholds": [{"id": "D1", "segment": [[x1, y1], [x2, y2]], "height_m": 0.022, "status": "measured | flagged_manual | user_entered"}],
  "keepout_zones": [{"id": "K1", "polygon": [[x, y], ...], "reason": "kitchen line"}],
  "waypoints": [{"id": "PICKUP", "xy": [x, y], "yaw": 0.0, "kind": "pickup | dock | dest"}],
  "hazards": [{"label": "cable on floor", "xy": [x, y], "source": "vision_llm", "confidence": 0.7}],
  "assets": {"splat": "s3://.../scene.splat", "occluder_usdz": "s3://.../occluder.usdz", "mesh_glb": "s3://.../mesh.glb"}
}
```

### 6.3 `RobotSpec` (catalog entry)

```json
{
  "robot_id": "vendor_model_slug", "class": "A_service | B_amr",
  "vendor": "...", "model": "...",
  "footprint_polygon_m": [[x, y], ...], "height_m": 1.2,
  "drive": "diff_drive | omni", "in_place_rotation": true, "min_turn_radius_m": 0.0,
  "max_speed_mps": 1.2, "nominal_speed_mps": 0.7,
  "max_threshold_m": 0.02, "max_slope_deg": 5,
  "min_aisle_width_vendor_m": 0.7, "payload_kg": 40, "runtime_h": 10,
  "safety_stop_distance_m": 0.5,
  "price": {"value": null, "currency": "USD", "is_public": false, "note": "contact sales"},
  "fields_sources": {"max_speed_mps": {"url": "...", "quote": "Max speed 1.2 m/s", "retrieved_at": "ISO8601"}},
  "verified_by_human": true
}
```

Rule: a field without a supporting quote is `null`, never estimated. If the footprint is only a bounding box, use a rectangle and mark `"footprint_approx": true`.

### 6.4 `SimBatch` and `Episode`

```json
{
  "batch_id": "sb_...", "twin_id": "...", "twin_version": 3, "robot_id": "...",
  "task": {"type": "delivery", "sequence": ["DOCK", "PICKUP", "T1", "T4", "DOCK"]},
  "density": "quiet | normal | peak", "geometry_bound": "optimistic | nominal | conservative",
  "n_episodes": 200, "seed_base": 1000, "code_version": "git sha",
  "results": {"success_rate": 0.93, "ci95": [0.89, 0.96], "median_mission_s": 74.2,
              "near_miss_per_mission": 0.31, "fail_reasons": {"blocked_timeout": 9, "no_path_static": 0, "collision": 5}}
}
```

Episode files (one JSONL per batch) store the trajectory at 10 Hz for replay: robot pose, pedestrian positions, events (stop, near_miss, collision, fail).

### 6.5 `Fact` (feeds the report)

```json
{"fact_id": "f_042", "value": 0.93, "unit": "ratio", "label": "Success rate, Robot X, normal traffic",
 "origin": {"type": "sim_batch | measurement | source_url | user_input | formula", "ref": "sb_..."}}
```

---

## 7. Workstream specifications

### 7.1 Capture (iOS app + Android observer PWA) — Owner: Capture lead

**Capability tiers (auto-detected):**

| Tier | Devices | Geometry source | Expected accuracy (to be measured) |
|---|---|---|---|
| T1_LIDAR | iPhone Pro (12 Pro+), iPad Pro | ARKit poses + `sceneDepth` | best |
| T2_VIO_MONO | Any ARKit iPhone (our iPhone 13) | ARKit poses + monocular depth aligned to ARKit feature points | good |
| T3_FIXED_OBSERVER | Any phone with a browser camera (Realme Narzo 12, iQOO Z7s) | Static pose from marker (PnP) | used for traffic and extra views |
| T0_COACH | Any phone (Samsung M30s) | none | coaching display only |

Accuracy numbers are **not** written anywhere until measured in Section 15.

**iOS app (P0):**
- `ARWorldTrackingConfiguration`; enable `.sceneDepth` if supported. Detect marker with ARKit image detection (reference image with physical width = printed size) to set site origin.
- Keyframe selection: save a frame when the camera moved >10 cm or rotated >10° since the last keyframe; cap at ~15 fps.
- Per keyframe save: JPEG (quality 0.85), intrinsics, `T_world_cam`, timestamp, tracking state, `rawFeaturePoints` (metric sparse points, used to align monocular depth), depth + confidence maps if T1.
- On-device checks: blur (variance of Laplacian below threshold → reject frame and warn), tracking-limited warnings, coverage grid (top-down 0.5 m cells seen from ≥2 angles).
- On-device face blur: Apple Vision face detection, Gaussian blur on face boxes before saving.
- Upload: zip chunks to presigned S3 URLs, resumable.
- AR replay mode (P1): see 7.9.

**Android observer PWA (P0):**
- Page `/observe/:site`: `getUserMedia` rear camera, 1280x720.
- Localization: detect AprilTag with OpenCV.js (aruco module supports AprilTag 36h11), solvePnP with known marker size → camera pose in site frame. Require 2 s of stable detection, then lock pose (phone must not move after).
- Face blur on-device: MediaPipe Tasks Face Detector (web), blur on a canvas, record the canvas with `MediaRecorder` at 5–10 fps. Raw frames are never uploaded.
- Also capture 10–20 still frames for the splat (extra viewpoints).
- Screen shows: "Locked ✓", recording time, battery warning.

**Coach page (P1):** `/coach/:site` on the M30s shows live coverage map and the latest prompt from the scanner, pushed via WebSocket.

**Exit criteria:** a 50 m² room scanned by iPhone 13 plus 2 observers uploads completely, with poses, without manual file handling.

### 7.2 Reconstruction (J1–J3) — Owner: 3D/CV lead

**J1 Ingest and sync (P0):** unzip, validate schema, align device clocks using server receive timestamps plus marker first-sighting events; reject captures with >20% tracking-limited frames.

**J2 Geometry (P0):**
1. For T1 frames: use LiDAR depth (confidence ≥ medium).
2. For T2 frames: run Depth Anything V2 **Small** (relative depth). For each frame, fit scale and shift with RANSAC between predicted depth and ARKit feature points projected into the frame. Drop frames with poor fit (inlier ratio < 0.5).
3. TSDF fusion (Open3D `ScalableTSDFVolume`, voxel 2 cm) with ARKit poses. Extract mesh and point cloud.
4. Floor: RANSAC plane on low points, cross-checked against ARKit horizontal plane anchors. Rotate so floor is Z=0.
5. Height map at 2.5 cm cells. Occupancy = any point in height band [0.03 m, 2.0 m]. Free = floor observed and empty band. Unknown = not observed (treated as blocked in sim, shown grey in UI).
6. Threshold candidates: step discontinuities of 0.5–6 cm along narrow linear features (door lines). If |height| is less than 2x the estimated noise, status = `flagged_manual`.
7. Export: occupancy PNG + metadata, decimated mesh (GLB) for the viewer, occluder USDZ for AR (via `usd-core`), point cloud.

**J3 Splat (P1):** train `gsplat` from posed keyframes (poses fixed from ARKit, optional pose refinement). Target 7k–15k iterations. Export `.splat`/`.ply` for the web viewer. Must align to site frame (same poses, so automatic).

**Exit criteria:** occupancy grid of a real room where a tape-measured aisle reads within the accuracy band measured in Section 15.

### 7.3 Semantics and twin extraction (J4) — Owner: 3D/CV lead (+ Agents lead for the vision LLM)

1. Select ~40 diverse keyframes (coverage-maximizing).
2. Grounding DINO with prompts: `table, chair, stool, shelf, counter, door, cart, box, trash bin, plant, cable, glass, step`. SAM 2 for masks.
3. Lift masks to 3D using depth and pose; vote points across views; DBSCAN cluster per label.
4. Fit primitives: oriented bounding box (Open3D) for tables/shelves/counters/carts; cylinder for bins/pillars/stools. Default `movable` by label (chair, stool, cart, table = movable; shelf, counter = fixed). User can override.
5. Vision LLM pass (Cosmos Reason or Nemotron VL, **VERIFY** which is available): given 8–12 keyframes plus the detected object list, return JSON hazards (`cable on floor`, `glass partition`, `wet area`, `door swings into aisle`) with frame references. These become `hazards` with confidence. **The vision model never outputs geometry.**
6. Every primitive keeps its source (`auto`) and confidence; low-confidence items are highlighted in the editor.

### 7.4 Twin editor (P0) — Owner: Frontend lead

- Top-down canvas: occupancy grid + primitives + hazards + thresholds.
- Actions: select, move, rotate, resize, relabel, toggle movable/fragile, delete, add box; draw keep-out polygons; place waypoints (dock, pickup, destinations with names); enter threshold heights.
- Saving creates a new twin version (immutable versions; sims reference a version).
- Rasterization: primitives are re-rasterized into the occupancy grid on save (user edits override raw occupancy inside primitive footprints).
- Side panel: 3D splat/mesh preview of the selected object.

### 7.5 Traffic modeling (J5, P1) — Owner: Sim lead

1. Person detection on blurred observer video (5–10 fps). Use an Apache/MIT-licensed detector (e.g., RT-DETR from a permissive source; **VERIFY** license). Do **not** use Ultralytics (AGPL).
2. Track with ByteTrack (MIT).
3. Foot point = bottom-center of box; project to floor with the homography from the marker-localized camera.
4. Merge tracks across cameras by proximity in floor coordinates and time.
5. Outputs: occupancy heatmap per 15-min window; origin-destination pairs between zones; arrival rate per zone; walking speed distribution.
6. Density levels: quiet = 25th percentile window, normal = median, peak = 90th percentile.
7. **Fallback if traffic capture fails:** generated pedestrians between user-marked zones at user-entered counts. The report must say "traffic: user-estimated" in that case.

### 7.6 Robot catalog (P0) — Owner: Agents lead

1. Seed list: 8–12 Class A and Class B robots chosen by the team (popular, with public spec pages).
2. For each: Tavily Search (`"<vendor> <model> specifications"`, `"<model> datasheet pdf"`), Tavily Extract on the top 3–5 URLs.
3. Nemotron Nano (structured JSON output) fills `RobotSpec` fields, each with an exact supporting quote.
4. Validator: the quote must appear verbatim in the extracted text and contain the number; units normalized by code, not by the LLM. Failing fields → `null`.
5. Human review screen: approve/edit each field, then `verified_by_human = true`. Only verified robots enter simulations.
6. Price: from a public page only; otherwise `is_public=false`. The report asks the user to enter a quote.

### 7.7 Simulation (J6) — Owner: Sim lead

**Static feasibility (P0, runs first, seconds):**
- Build configuration space per robot: rasterize the footprint at K headings (K=36 for diff-drive), dilate obstacles per heading.
- Diff-drive with in-place rotation: A* over (x, y, heading) with rotation cost. Car-like robots: Hybrid A*.
- For each task leg: reachable or not, with the narrowest gap on the best path and its location.
- Threshold check: any path crossing a threshold higher than `max_threshold_m` fails; `flagged_manual` thresholds produce "unknown" until measured.

**Monte Carlo (P0 core, P1 pedestrians):**
- Episode = full mission sequence with a timeout (3x nominal path time).
- Global path: from static planner. Local control: Dynamic Window Approach with footprint collision checks against static grid + pedestrian discs.
- Safety behavior: stop if any pedestrian is within `safety_stop_distance_m` in the motion direction; resume when clear; replan after 10 s blocked.
- Pedestrians: pysocialforce, spawned from the traffic model's origin-destination pairs at the chosen density. Pedestrians treat the robot as an obstacle.
- Variation per episode (seeded): movable objects jittered ±0.10 m and ±10°; chairs ±0.20 m; random clutter boxes (0–2) placed in free zones not on waypoints; pedestrian schedule.
- Geometry bounds: run each batch on three grids: nominal, conservative (obstacles dilated by the measured p90 scan error), optimistic (eroded by it). Report shows the conservative–optimistic range.
- Outcome per episode: success, or fail reason ∈ {no_path_static, blocked_timeout, collision}. Metrics: mission time, path length, stops, near-misses (pedestrian within 0.5 m while robot speed > 0.3 m/s), min gap.
- Determinism: same twin version + robot + seed → identical result. Enforced by test.
- Parallelism: batches split into shards of 25 episodes; each shard is one Serverless Job invocation (or one process on a VM). Results written to S3, aggregated by the API.

**Default analysis budget:** candidates (≤6 robots) × densities (3) × bounds (3) × 100 episodes. Measure actual runtime and cost; tune down if needed.

**Pinch Map** (internal name: bottleneck grid): accumulate stop events, near-misses, and blocked time per grid cell across episodes → heatmap layer.

### 7.8 Layout optimizer (J7, P1) — Owner: Sim lead

- Candidate objects: `movable=true` primitives within 1.5 m of the top 3 bottleneck cells.
- Candidate moves: translations on a 5 cm grid up to 0.30 m, rotations {0, ±15°}. Constraints: no overlap, stay inside floor bounds, don't enter keep-out zones, keep ≥0.9 m clear path from entrance to exits (configurable human-egress rule), don't block waypoints.
- Search: evaluate each single move with 30 episodes at normal density (nominal geometry); take the top 5, confirm each with 200 episodes; then try pairwise combinations of the top 3.
- Output: "Move T4 0.18 m toward wall (east): success 71% → 93% (n=200, normal traffic)". Shown as a before/after overlay.

### 7.9 AR replay (P1, hero shot) — Owner: Capture lead

- RealityKit scene anchored with the same marker image (`ARImageTrackingConfiguration` combined with world tracking).
- Load `occluder.usdz` (decimated twin mesh) with an occlusion material so real furniture hides the robot.
- Enable people occlusion (`.personSegmentationWithDepth`; supported on A12+ chips, iPhone 13 qualifies).
- Robot = parametric model from `RobotSpec` (footprint extrusion, height, tray tiers), neutral color. No vendor branding.
- Play an episode trajectory at real time; show events (stop = amber halo, near-miss = red pulse, failure = red marker + label).
- Toggle: "before layout change / after layout change".

### 7.10 Chaos mode (P1) — Owner: Sim lead + Frontend lead

- Console starts a live sim session (server steps the sim at 20 Hz, streams state over WebSocket).
- Phone opens `/joystick/:session`, touch joystick controls one pedestrian (max 1.4 m/s).
- Robot runs its normal local controller and safety stop. Events are logged into a live session record and can be added to the report as "stress-test observations" (clearly labeled as manual tests).

### 7.11 Agents, report, and ROI — Owner: Agents lead

**Model tiering (all model IDs VERIFY on Day 1, write them to `config/models.yaml`):**

| Role | Model tier | Input | Output |
|---|---|---|---|
| Spec extraction, log summarizing, coach text | Nemotron 3 Nano | Extracted page text, coverage stats | Strict JSON |
| Orchestrator (tool calling) | Nemotron 3 Super | Site summary, job definition | Tool calls: `list_candidates`, `run_feasibility`, `run_batch`, `run_optimizer`, `tavily_search`, `tavily_extract`, `compute_roi` |
| Report writer and trade-off reasoning | Nemotron 3 Ultra | Facts pack (JSON), citations | Markdown report with `[f_xxx]` fact tags |
| Scene hazards | Primary: Cosmos Reason 2 (self-hosted on Nebius AI Cloud via vLLM). Alternative: Nemotron 3.5 Super VL on Token Factory. Confirm both on Day 1 (VERIFY), record in docs/VERIFIED.md | Keyframes + object list | Hazards JSON |

**Orchestrator rules:**
- Candidate selection is code first (static filters: footprint vs. narrowest required aisle, threshold limits, payload vs. task), then the LLM explains and orders them.
- The orchestrator may not write numbers into the report; it only triggers tools.

**ROI (deterministic code, P1):**
- Inputs (user): staff hourly cost, hours/day spent on the task, days/week, robot price or monthly lease, maintenance % per year (default shown and editable).
- Automatable share = success rate (nominal, normal traffic) × fraction of task legs feasible.
- Monthly saving = staff cost × task hours × automatable share × days/month.
- Payback months = price / (monthly saving − monthly running costs). Shown with the formula and every input.

**Report fact-checking (P0):**
1. Code builds the facts pack (every number with a `fact_id`).
2. Ultra writes the report, required to tag each number with `[f_xxx]`.
3. Post-processor: every numeric token must sit next to a valid fact tag and match the fact value (with rounding tolerance). Untagged or mismatched numbers → one regeneration with the error list; if still failing, the sentence is removed and logged.
4. UI renders tags as clickable chips (sim batch → replay; URL → source; measurement → twin view).

**Clearance Report sections:** verdict per robot (Cleared / Cleared with changes / Not cleared, with the rule that produced it, fact-tagged); How we investigated (the 7.12 trail); site measurements and accuracy; robot comparison table (ranges); Pinch Map; recommended changes; payback; hazards and manual checks; methodology and limitations; sources.

### 7.12 Nemotron investigation loop (P0 for the analysis, added in v1.1, extended v1.2) — Owner: Agents lead

**v1.2 decision:** after the first batch results, Nemotron 3 Super forms a hypothesis about why a robot fails (e.g., "fails at the counter corridor at peak traffic"), chooses the next experiment (fewer pedestrians in that zone, a chair moved, a different robot), runs it through the existing sim tools, reads the results, and decides whether to dig further or conclude. **Cap: 6 experiments per robot.** The trail (hypothesis → experiment → result → conclusion; `Experiment` records in `Investigation.experiments`) is stored and shown in the Clearance Report as **"How we investigated"**. Nemotron 3 Ultra writes the report from this trail. Numbers still come only from tools. Extra experiment tools: `run_probe` accepts `{"zone_id", "ped_scale"}` (reduce pedestrians in one zone), `{"density"}`, object overrides, or `{"robot_id"}` (swap robot).

After the Monte Carlo batches for a candidate robot finish, Nemotron 3 Super runs a bounded **observe → hypothesize → probe → conclude** loop that explains *why* the robot fails and tests the explanation before anything reaches the report.

- **Tools (all numbers come from code):** `get_batch_summary`, `get_bottlenecks(top_k)` (cells ranked by stop/near-miss/blocked time, with nearby object IDs), `get_gap_at(xy)`, `get_episode_events(batch_id, type, limit)`, `get_objects_near(xy, r)`, `run_probe(overrides, density, n_episodes)` (small counterfactual batch: move/remove an object, change density), `record_finding(claim, evidence_fact_ids, object_ids, suggested_change)`, `finish(reason)`.
- **Loop rules:** at most 8 steps and 6 experiments per robot (each probe ≤30 episodes, nominal geometry). Each tool result is registered as a Fact (`origin.type = "investigation"`), and the model may cite only those fact IDs. A finding counts as **confirmed** only when a probe that applies its counterfactual improves the success rate by more than the probe's CI half-width.
- **Outputs:** an `Investigation` record with every step (tool, args, result), and `Finding`s that (a) seed the layout optimizer's candidate objects (`hint_object_ids`), (b) feed the report's "Why it fails" section, and (c) are shown in the UI as an inspectable trace.
- **Offline mode:** a deterministic policy uses the same tools, so CI and offline demos run without Token Factory. The trace records which mode produced it.
- **Budget:** logged in the usage table like any other call. The loop stops early when Super calls `finish` or when no new bottleneck remains above threshold.

### 7.13 Verdicts (P0, decided by code, v1.2) — Owner: Agents lead

`clearance_core/verdict.py::decide_verdict`, thresholds in `config/sim_defaults.yaml` (`verdict.success_min`, default 0.90):
- **Cleared:** every task leg is feasible on the conservative geometry bound, no unresolved `flagged_manual` thresholds on the route, and simulated success at normal traffic (conservative bound) ≥ `success_min`.
- **Cleared with changes:** not Cleared as-is, but meets the Cleared rule after applying the optimizer's recommended layout changes (confirmed by a batch at normal traffic on the conservative bound) and/or once the user measures the flagged thresholds.
- **Not cleared:** everything else, with the main reason (e.g., "no path: narrowest gap 0.62 m vs 0.70 m needed", "success 64% at normal traffic").

The report shows the rule that produced each verdict with fact tags. Nemotron explains verdicts but never sets or changes them; the fact-checker rejects any report sentence whose verdict label differs from the code verdict.

---

## 8. Infrastructure on Nebius

| Need | Primary | Fallback | VERIFY |
|---|---|---|---|
| LLM inference | Token Factory (OpenAI-compatible API) | — | Base URL, model IDs, rate limits, credits |
| API service | Serverless Endpoint (container) | AI Cloud CPU VM | Container limits, WebSocket support |
| GPU jobs (recon, splat, segmentation) | Serverless Jobs (GPU) | AI Cloud GPU VM running a job worker | GPU types available, max runtime, image size |
| CPU sim shards | Serverless Jobs (CPU) | Same GPU VM's CPU cores with a process pool | CPU-only job support, concurrency limits |
| Vision LLM | Self-hosted with vLLM on Serverless Endpoint / VM | Nemotron VL on Token Factory if listed | Which vision model is available and its license |
| Storage | Nebius Object Storage (S3-compatible) | — | Bucket setup, presigned URLs |
| Database | Managed PostgreSQL if available | Postgres container on the API VM | Availability in our account |

**Cost and usage logging (P0):** every model call and job logs model ID, tokens, duration, and estimated cost to a `usage` table. The README shows a real table: calls per model, tokens, and cost per full site report. This is our proof of model tiering.

---

## 9. Tech stack and licenses

Our repository license: **Apache-2.0**.

| Dependency | Use | License (VERIFY each before use) |
|---|---|---|
| gsplat | Gaussian splat training | Apache-2.0 |
| Open3D | TSDF fusion, point clouds, OBB | MIT |
| OpenCV / OpenCV.js | AprilTag, PnP, homography | Apache-2.0 |
| Depth Anything V2 **Small** | Relative monocular depth | Apache-2.0 (Base/Large/Giant are non-commercial: do not use) |
| Grounding DINO | Open-vocabulary detection | Apache-2.0 |
| SAM 2 | Segmentation | Apache-2.0 |
| ByteTrack | Tracking | MIT |
| pysocialforce | Pedestrian model | MIT |
| Numba, NumPy, Shapely | Sim | BSD-style |
| GaussianSplats3D (mkkellogg) | Web splat viewer | MIT |
| three.js | 3D web | MIT |
| MediaPipe Tasks | On-device face detection (web) | Apache-2.0 |
| usd-core | USDZ export | Modified Apache (TOST); VERIFY |
| Nemotron, Cosmos models | LLM / vision | NVIDIA model licenses; VERIFY terms |
| **Avoid:** Ultralytics YOLO (AGPL-3.0), DUSt3R/MASt3R (CC BY-NC-SA), DA-V2 Base/Large/Giant (CC BY-NC) | | |

Record final versions and licenses in `THIRD_PARTY_LICENSES.md`.

---

## 10. Repository structure

```
clearance/
├── LICENSE                     # Apache-2.0
├── README.md
├── FEEDBACK.md                 # running log: Token Factory, AI Cloud, NVIDIA models
├── THIRD_PARTY_LICENSES.md
├── docs/
│   ├── VERIFIED.md             # Day-1 verification results
│   ├── ARCHITECTURE.md
│   ├── EVAL.md                 # measured accuracy and validation results
│   └── PRIVACY.md
├── config/
│   ├── models.yaml             # exact Token Factory model IDs
│   └── sim_defaults.yaml
├── schemas/                    # JSON Schemas for Capture, Twin, RobotSpec, SimBatch, Fact
├── apps/
│   ├── ios/                    # Swift app: capture + AR
│   └── web/                    # Next.js: console, editor, report, observer, joystick, coach
├── services/
│   └── api/                    # FastAPI
├── jobs/
│   ├── common/                 # S3 IO, schema validation, logging
│   ├── recon/  splat/  semantics/  traffic/  sim/  optimizer/
├── agents/
│   ├── catalog_builder/  orchestrator/  report/  factcheck/
├── catalog/robots/*.json       # verified RobotSpec files
├── sample_sites/               # 3 processed demo sites (twins + results, no raw faces)
├── tests/
│   ├── unit/  integration/  golden/   # golden = reference rooms with known answers
└── infra/                      # Dockerfiles, job definitions, deploy scripts
```

---

## 11. API (core endpoints)

| Method | Path | Purpose |
|---|---|---|
| POST | `/sites` | Create site; returns QR payload and marker PDF URL |
| POST | `/sites/{id}/captures` | Register a device capture; returns presigned upload URLs |
| POST | `/captures/{id}/complete` | Mark upload done; triggers J1 |
| GET | `/sites/{id}/twin` | Latest twin (or `?version=`) |
| PUT | `/sites/{id}/twin` | Save edited twin as a new version |
| GET | `/catalog/robots` | Verified robots |
| POST | `/sites/{id}/analyses` | Start analysis (job definition, business inputs) |
| GET | `/analyses/{id}` | Status, progress per stage, results |
| GET | `/analyses/{id}/report` | Report (Markdown + facts) |
| GET | `/sim/batches/{id}/episodes/{n}` | Episode trajectory for replay |
| WS | `/live/{session}` | Chaos mode and coach updates |
| GET | `/usage/summary` | Model and job usage for the README |

Jobs are idempotent: inputs are content-addressed; re-running a stage with the same inputs returns the cached result.

---

## 12. Team and ownership

Assumes 5 people. With 4, Frontend and Agents leads share the product/video role.

| Role | Owns | Also responsible for |
|---|---|---|
| **Capture lead** (iOS) | 7.1, 7.9 | Hardware clip filming, privacy on-device |
| **3D/CV lead** | 7.2, 7.3 | Accuracy measurements (Section 15.1) |
| **Sim lead** | 7.5, 7.7, 7.8, 7.10 | Golden tests, cardboard validation |
| **Agents/backend lead** | 7.6, 7.11, Section 8, API | Model verification, usage logging, FEEDBACK.md |
| **Frontend/product lead** | 7.4, console, report UI, hosted demo | Video edit, Devpost text, README polish |

**Rituals:** 15-min standup twice a day; a single integration branch merged at least twice a day; the schemas in `schemas/` change only with a note to everyone.

---

## 13. Build phases with exit criteria

### Phase 0 — Foundations (first half day)
- Accounts and credits: Nebius (Token Factory + AI Cloud), Tavily, GitHub repo (public, Apache-2.0 at top).
- Complete `docs/VERIFIED.md`: model IDs, Serverless Jobs GPU/CPU options, vision model availability, Official Rules prize-stacking answer.
- Freeze schemas. Print markers (AprilTag 36h11, measure printed size with a ruler, record it).
- Pick and get written permission for demo sites (Section 16).
- **Exit:** a hello-world call to each Nemotron tier from the API, and a Serverless Job that writes to S3.

### Phase 0.5 — Go/no-go gate: 4-hour scan-to-occupancy test (runs FIRST, added in v1.1)
Time-box: 4 hours of work. Question: can a phone scan become a trustworthy occupancy grid?
- **Reference-room accuracy run (no hardware needed):** render reference ARKit-format captures (T1 LiDAR and T2 monocular) of rooms with known geometry → J1/J2 → occupancy. Measure tape-style reference distances against ground truth.
- **On-site scan run:** a real iPhone scan of one room (our capture format, or an imported third-party ARKit recorder export, VERIFY) → occupancy. Tape-check 10 distances.
- **GO** if: the pipeline runs end to end without manual file handling, reference-room median error ≤ 3 cm (T1) and ≤ 5 cm (T2), and a 0.80 m reference aisle reads as passable at the correct width ±5 cm. **NO-GO fallback:** the twin editor becomes the primary geometry source (trace a floor plan over the scan or a photo, with tape-entered widths), and the scan is used only for visuals. The rest of the product (sim, agents, report) is unchanged.
- Result written to `docs/GATE.md`, whichever way it goes.

### Phase 1 — Ugly end-to-end slice (P0)
iPhone capture → upload → J1/J2 → occupancy grid → editor with waypoints → static feasibility for 2 hand-entered robots → 50-episode Monte Carlo without pedestrians → plain report with fact tags.
- **Exit:** one real room goes from scan to report with no manual file handling.

### Phase 2 — Real product core (P0)
Segmentation + primitives, catalog builder with Tavily (≥8 verified robots), Android observers + traffic model, pedestrians in sim, geometry bounds, sharded batches on Serverless Jobs, fact-checker, usage logging, accuracy measurement.
- **Exit:** full analysis on a real site with traffic, ≥6 candidate robots, results with ranges, all numbers tagged and verified.

### Phase 3 — Wow layer (P1)
Splat viewer with trajectory overlay, layout optimizer, AR replay with occlusion, ROI, chaos mode, coach screen, PDF export.
- **Exit:** the AR hero shot works in a real site with the robot hidden correctly behind real furniture.

### Phase 4 — Proof and ship
Three demo sites processed and loaded into the hosted demo, cardboard validation (15.3), metrics written to `docs/EVAL.md`, video, README, Devpost submission, feedback write-up.

### P2 backlog (only after everything above)
Handheld Android scanning via native ARCore app, 3D physics validation of the top recommendation in Isaac Sim or MuJoCo, Cosmos video variants, multi-floor support, vendor-shareable links.

---

## 14. Real deadline cut line

The submission deadline is **Oct 30, 2026, 10:30 PM IST**. If the team is submitting to this hackathon, build in this order and stop adding scope at the line:

1. Phase 1 complete (non-negotiable).
2. Catalog with Tavily (≥6 verified robots) + sharded Monte Carlo on Serverless Jobs + fact-checked report.
3. AR replay with occlusion (the hero shot).
4. Splat viewer.
5. Traffic from observers (fallback: user-estimated traffic, labeled).
6. **— cut line for a 72-hour build —**
7. Optimizer, ROI, chaos mode, coach screen, PDF.

Reserve the final **8 hours** only for: video, README, Devpost form, feedback section, and a fresh-clone test of the setup instructions. Submit at least 2 hours before the deadline.

---

## 15. Testing and evaluation

### 15.1 Scan accuracy (P0, measured, goes in README)
- Per demo site, tape-measure 20 reference distances: aisle widths, table-to-table gaps, door widths, shelf depths, room width.
- Measure the same distances in the twin.
- Report median and p90 absolute error per tier (T2 iPhone 13; T1 if a Pro iPhone is borrowed for one session).
- **Target:** T2 median error ≤ 3 cm. If missed, report the real number honestly and widen geometry bounds accordingly.

### 15.2 Golden simulation tests (P0, automated in CI)
Reference rooms with analytically known answers:
- Aisle narrower than robot width → 0% success, fail reason `no_path_static`.
- Aisle exactly width + 2 cm with in-place rotation → path exists; without rotation and with a tight turn → no path.
- Threshold above robot limit → fail at threshold.
- Empty room, no pedestrians → 100% success; mission time within 5% of path length / nominal speed.
- Same seed → bit-identical episode output.

### 15.3 Cardboard robot validation (P1, our strongest credibility asset)
- Build cardboard footprint models at true dimensions of 2 catalog robots (one large, one small), mounted on a rolling base or carried flat at floor level.
- In the real demo site, push each along the planned routes and record pass/fail at every pinch point flagged by static feasibility.
- **Target:** ≥ 90% agreement between predicted and observed pass/fail at pinch points.
- Film it. 10 seconds of this in the video makes judges trust everything else.

### 15.4 Agent and report quality
- Catalog extraction: on 8 robots, compare extracted fields to manual reading of datasheets. Target ≥ 95% precision on non-null fields (wrong values are worse than nulls).
- Fact-check: 100% of numbers in final reports carry valid fact tags (automated check in CI over sample sites).
- Robustness: report generation on 3 sites × 3 runs completes without manual fixes.

### 15.5 Performance (measure and publish; targets for a ~50–100 m² site)
- Upload to twin ready: ≤ 15 min.
- Full analysis (≤6 robots): ≤ 10 min.
- Cost per full report (models + jobs): measured from the usage table.

---

## 16. Demo sites, privacy, and consent

**Sites (aim for 3, minimum 2):**
1. A café or small restaurant (Class A robots).
2. A storeroom, college lab, or small warehouse (Class B robots).
3. A corridor/office space as a control site.

**Consent and privacy (P0):**
- Written permission from the site owner, including filming and publishing the processed twin.
- Signage during traffic recording ("Camera recording for a research project; faces are blurred on the device").
- Faces blurred on-device; raw unblurred frames never leave the phone. iPhone scans should avoid people where possible.
- Published sample sites contain only twins, blurred imagery, and aggregate traffic heatmaps.
- `docs/PRIVACY.md` documents all of the above.

---

## 17. Demo video (≤ 3:00, public YouTube)

| Time | Shot | Notes |
|---|---|---|
| 0:00–0:15 | Café owner's problem: "Will a $X robot actually work here?" | Use a real quote if the owner received one; otherwise say "robots like these cost tens of thousands" without an invented number |
| 0:15–1:20 | **Continuous hardware clip (≥60 s):** marker on floor, Android observers locking pose, M30s coach prompting, iPhone 13 scanning with coverage map, console receiving streams with blurred faces | Satisfies the Physical AI footage rule |
| 1:20–1:45 | Twin appears (splat + primitives), user drags a chair, places waypoints | Shows the editor and human-in-the-loop |
| 1:45–2:10 | Analysis: Tavily sources populating robot specs, Serverless Jobs shards running, ranges filling in, Pinch Map | Show Nemotron tier labels and the usage counter |
| 2:10–2:35 | **AR hero shot:** robot in the real room, hidden behind a real table, stopping for a real person, failing at the pinch point; then "after" layout succeeds | Then 5–10 s of the cardboard validation at the same spot |
| 2:35–3:00 | Report: verdict, payback, clickable citations; architecture card; validation numbers | End on the product name and URL |

Record narration last, after the edit is locked. Captions on.

---

## 18. Submission package

### 18.1 README outline
1. What it is (one paragraph + hero GIF).
2. Try it: hosted demo link and the 3 sample sites.
3. How it works (architecture diagram).
4. **How we used NVIDIA models and Nebius:** table of exact model IDs, role, number of calls, tokens; Serverless Jobs and Endpoints usage; where Token Factory sped up the workflow.
5. How we used Tavily.
6. Measured results: scan accuracy, golden tests, cardboard validation, performance, cost per report.
7. Setup and run instructions (tested from a fresh clone).
8. Limitations and non-goals.
9. Privacy.
10. License and third-party licenses.

### 18.2 Devpost form
- Track: Physical AI. Built with: list actual tools only.
- Description: problem, who it's for, what we built, how it works, what we measured, what's next.
- Feedback section: drawn from `FEEDBACK.md` (specific, reproducible notes with what worked, what didn't, and suggested fixes). Aim for the Most Valuable Feedback prize.
- City: only if attended.

### 18.3 Final checklist
- [ ] Repo public, Apache-2.0 visible at the top.
- [ ] README setup tested from a fresh clone by someone who didn't write it.
- [ ] Hosted demo loads the 3 sample sites without login friction.
- [ ] Video ≤ 3:00, public, with ≥60 s of hardware footage.
- [ ] Every model named anywhere was actually called (check against usage logs).
- [ ] No vendor logos/3D models; no unblurred faces.
- [ ] `docs/EVAL.md` numbers match the README and video.
- [ ] Submitted ≥ 2 hours before deadline.

---

## 19. Risk register

| Risk | Likelihood | Impact | Mitigation |
|---|---|---|---|
| A VERIFY item fails (model not available, Serverless limits) | Medium | High | Day-1 verification; fallbacks in Section 8 |
| Monocular depth too noisy on iPhone 13 | Medium | High | Feature-point alignment, TSDF averaging, textured demo sites, borrow a Pro iPhone for one session, widen geometry bounds |
| Featureless walls/glass break reconstruction | Medium | Medium | Site choice; editor lets users add/fix walls |
| Segmentation misses objects | High | Medium | Raw occupancy is always the safety net; editor fixes labels |
| Vendor specs missing or contradictory | High | Medium | Nulls not guesses; human review; only verified robots simulated |
| Sim too slow for the batch budget | Medium | Medium | Numba, smaller budgets, shard more, cache feasibility |
| AR alignment drifts | Medium | High (hero shot) | Re-detect marker continuously; film in short takes; keep marker in view |
| Traffic recording not possible (permission) | Medium | Low | User-estimated traffic fallback, labeled |
| Report hallucinates numbers | Medium | High | Fact-tag enforcement and removal of unverifiable sentences |
| Privacy complaint | Low | High | Consent, on-device blur, no raw faces stored |
| Scope creep | High | High | Cut line in Section 14; P2 stays P2 |
| Integration breaks late | Medium | High | Twice-daily merges; end-to-end test on a sample site in CI |

---

## 20. What success looks like (definition of done)

**Product success (true at submission):**
1. A non-team member scans a real room with an iPhone 13 and gets a cited report without help.
2. Three real sites processed and available in the hosted demo.
3. Measured scan accuracy published per tier.
4. Cardboard validation ≥ 90% agreement at pinch points, published with footage.
5. 100% of report numbers fact-tagged and verified.
6. AR replay with correct occlusion filmed in a real site.
7. Usage table proves Nano/Super/Ultra tiering with real call counts and cost per report.

**Hackathon success:**
1. Every requirement in Section 3.1 checked.
2. Video lands the three moments: hardware clip, AR robot in the real room, cardboard validation.
3. Tavily visibly drives the catalog and market data, with citations in the UI.
4. Feedback section written from real logged experience.

**Stretch success:** an actual operator or integrator says on camera that they would use it before buying a robot.

---

## 21. Day-1 checklist (do these first, in order)

1. Create repo, add Apache-2.0 `LICENSE`, push this file as `docs/PLAN.md`.
2. Get Nebius and Tavily credentials into a shared secret store (never commit keys).
3. Call `GET /v1/models` on Token Factory; record exact IDs for Nano, Super, Ultra, and any vision model in `config/models.yaml` and `docs/VERIFIED.md`.
4. Check Serverless Jobs: GPU types, CPU-only option, max runtime, concurrency. Record.
5. Check whether Cosmos Reason (or another NVIDIA vision model) is available on Token Factory; if not, plan self-hosting and confirm its license.
6. Read the Official Rules: prize stacking, team size, filming/consent requirements. Record.
7. Verify licenses of every dependency in Section 9.
8. Print and measure markers; test ARKit image detection with the printed marker.
9. Contact demo sites; get written permission.
10. Commit frozen JSON schemas to `schemas/`.
11. Start `FEEDBACK.md` and log every friction point from this moment.
