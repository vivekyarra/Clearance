# Clearance cross-module contracts (frozen)

## Naming

- Product: **Clearance**. Python package `clearance_core`; reference rooms live in `jobs/reference/` (`Twin.geometry_source = "reference"`: rooms built from known ground-truth geometry).
- Measurements are **gaps**: `narrowest_gap_m`, `required_gap_m`, `min_gap_m`.
- Report vocabulary: "Clearance check", "Clearance Report", heatmap = "Pinch Map" (code may say `bottleneck`). Verdicts: **Cleared**, **Cleared with changes**, **Not cleared**. Simulation results are "simulation-based estimates"; open items read "measure on site", "price on request", "awaiting review".
- Environment: prefix `CLEARANCE_` (`CLEARANCE_DATA`, `CLEARANCE_DATABASE_URL`, `CLEARANCE_S3_BUCKET`, `CLEARANCE_VISION_BASE_URL`, ...); vendor keys stay `NEBIUS_API_KEY`, `TAVILY_API_KEY`.
- Modes: `CLEARANCE_LLM_MODE` and `CLEARANCE_TAVILY_MODE` are `offline` (deterministic policy, no network) or `live`.

Verdicts: `clearance_core/verdict.py::decide_verdict(robot_id, as_is: VerdictInputs, with_changes: VerdictInputs | None) -> Verdict` (thresholds in config/sim_defaults.yaml). Only code sets verdicts.

Data shapes: `clearance_core/schemas.py`. Grid I/O: `clearance_core/grid.py`. Storage: `clearance_core/storage.py` (keys relative to `CLEARANCE_DATA`, default `data/`).
Python runs from the repo root with `PYTHONPATH=.` (Python 3.11, `requirements.txt`).
Every package directory has an `__init__.py`. Tests live in `tests/unit/<module>/`, `tests/golden/`, `tests/integration/`.

Model weights, pip/npm caches and large downloads go outside the repo (`CLEARANCE_MODEL_CACHE`, default `~/.cache/clearance/models`). Never commit data blobs, weights or keys.

## Access control (services/api)
- **Operator token** `CLEARANCE_OPERATOR_TOKEN` (Bearer) on every write route: site create, twin PUT, catalog verify, Clearance checks, capture register/complete by the operator, live session create/delete. Unset locally: generated once, printed at startup and saved to `data/.operator_token` (gitignored). `CLEARANCE_DEPLOYED=1` refuses to start without it and without `PUBLIC_API_BASE` or an allowed-hosts list.
- **Site join token** (4 h, site-scoped) is carried in the site QR / join link (`?token=`); phones use it to register captures and join the coach session.
- **Device token** (48 h, one capture) is returned when a capture is registered; it authorizes that capture's chunk uploads and `complete`.
- **Live session token**: `POST /live` returns `joystick_url` with `?token=`; WebSocket clients present it (or the operator token).
- **Upload URLs** are signed and single-use per file. Read routes for sites, reports, replays and the catalog stay public so the hosted demo can be browsed without signing in.
- **WebSocket limits**: Origin allowlist, at most 8 clients per session, message rate limit, 128 KB max frame (`uvicorn --ws-max-size 131072`); at most 4 live sessions per site, unjoined sessions close after 120 s.

## Python entry points each module MUST expose (others code against these)

```python
# jobs/recon/pipeline.py               (gate owner)
def build_twin_from_capture(capture_dir: str, site_id: str, out_prefix: str, *, tier: str | None = None) -> Twin
#   out_prefix e.g. "sites/<site_id>/twin_v1/"; writes occ.png, mesh/pointcloud optional, twin.json

# jobs/reference/rooms.py              (gate owner)
def make_room(kind: Literal["cafe","storeroom","corridor","golden_*"], seed: int = 0) -> ReferenceRoom
#   ReferenceRoom has .twin (Twin with ground-truth occupancy + primitives + waypoints + traffic), .gt_measurements (list of {name, a_xy, b_xy, value_m})
def render_capture(room, out_dir: str, tier: Literal["T1_LIDAR","T2_VIO_MONO"], seed=0) -> str  # writes a Capture dir

# jobs/sim/api.py                      (sim owner)
def rasterize_twin(twin: Twin, bound: Bound = "nominal", p90_err_m: float | None = None, overrides: dict | None = None) -> Grid
def check_feasibility(twin: Twin, robot: RobotSpec, task: Task, bound: Bound = "nominal") -> Feasibility
def run_batch(twin: Twin, robot: RobotSpec, task: Task, density: Density, bound: Bound,
              n_episodes: int, seed_base: int, *, overrides: dict | None = None,
              record_episodes: bool = True, out_prefix: str | None = None) -> SimBatch
def run_shard(spec: dict) -> dict          # JSON-in/JSON-out for Serverless Jobs; spec = batch args + shard index
def merge_shards(shards: list[dict]) -> SimBatch
class LiveSession:                         # chaos mode, 20 Hz
    def __init__(self, twin, robot, task, density, seed): ...
    def set_joystick(self, vx: float, vy: float): ...
    def step(self) -> dict                 # state frame: {"t","robot":{x,y,yaw,v,state},"peds":[[x,y],..],"events":[..]}
# overrides = {"move": {obj_id: {"dx","dy","dyaw"}}, "remove": [obj_id], "add_boxes": [Primitive...]}
# Episode JSONL line @10 Hz: {"t","robot":[x,y,yaw,v],"peds":[[x,y],...],"events":[{"type":"stop|near_miss|collision|fail|success|replan","xy":[x,y],"detail":..}]}

# jobs/optimizer/search.py             (optimizer owner)
def optimize_layout(twin, robot, task, bottleneck: np.ndarray, *, budget: dict | None = None, seed=0,
                    hint_object_ids: list[str] | None = None) -> list[dict]
#   returns [{"change": overrides, "description": str, "before": BatchResults, "after": BatchResults, "batch_ids": [..]}]

# agents/llm/client.py                 (llm owner)
class LLM: chat(role: Literal["nano","super","ultra","vision"], messages, *, tools=None, response_format=None, **kw) -> response
#   OpenAI-compatible, base URL + IDs from config/models.yaml; CLEARANCE_LLM_MODE=live|offline; every call logged to usage table
# agents/llm/usage.py: log_usage(kind, model_id, tokens_in, tokens_out, duration_s, cost_usd, meta) ; summary() -> dict

# agents/orchestrator/run.py
def run_analysis(analysis_id, twin, robots: list[RobotSpec], task, business: dict, *, budget: dict) -> dict  # writes analysis artifacts
# agents/investigate/loop.py           (Nemotron investigation loop, PLAN 7.12)
def investigate(analysis_id, twin, robot, task, batches: list[SimBatch], *, max_steps=8, max_experiments=6, probe_episodes=30, other_robots=None) -> Investigation
#   fills Investigation.experiments (hypothesis/kind/change/batch_id/result facts/outcome/next_action/conclusion)

# agents/report/facts.py + writer.py, agents/factcheck/check.py
def build_facts(analysis: dict) -> list[Fact]
def write_report(facts, citations, findings, verdicts: list[Verdict], investigation: Investigation | None) -> str  # Clearance Report markdown with [f_xxx]; sections incl. "How we investigated"
def check_report(md: str, facts: list[Fact], tol=0.01) -> {"ok": bool, "errors": [...], "clean_md": str}
# agents/report/roi.py
def compute_roi(inputs: dict, success_rate: float, feasible_leg_frac: float) -> {"monthly_saving", "payback_months", "formula", "inputs"}

# agents/catalog_builder/build.py
def build_spec(vendor, model, robot_class) -> RobotSpec    # Tavily + Nano + verbatim-quote validator
def validate_field(value, quote, page_text) -> bool
```

## Analysis artifact layout (storage keys)
```
sites/<site_id>/site.json
sites/<site_id>/twin_v<N>/twin.json, occ.png, mesh.glb?, splat.ply?, occluder.usdz?, pointcloud.ply?
sites/<site_id>/twin_v<N>/traffic/traffic_stats.json, heatmap.npy, heatmap_w<k>.npy   (jobs/traffic, see below)
captures/<capture_id>/...                                   (raw upload, per Capture schema)
analyses/<analysis_id>/analysis.json                       {status, stages:{name:{state,progress}}, inputs, candidates, feasibility, batches:[batch_id], optimizer, investigation_id, verdicts:[Verdict], report}
analyses/<analysis_id>/batches/<batch_id>.json + .episodes.jsonl + .bottleneck.npy
analyses/<analysis_id>/investigation.json
analyses/<analysis_id>/facts.json, report.md, citations.json
```

## HTTP API (services/api) — PLAN Section 11, plus
- `GET /sites` list (reference sites carry `geometry_source: "reference"`), `GET /sites/{id}/twin/occupancy.png`
- `GET /analyses/{id}/investigation`, `GET /analyses/{id}/facts`
- `POST /catalog/robots/{id}/verify` (human review), `GET /catalog/robots?all=1`
- `POST /live` → `{session_id}`; `WS /live/{session}` server→client state frames, client→server `{"type":"joystick","vx","vy"}`
- `GET /usage/summary`
- `GET /sites/{id}/captures` per-capture status (see "Capture upload"); `GET /captures/{id}` one capture
- `GET /sites/{id}/twin/assets/{name}[?version=N]` twin asset files (see "Twin assets")
- `GET /sim/batches/{id}/bottleneck.npy` Pinch Map grid of a batch (see "Pinch Map grid")
- `GET /sites/{id}/qr.png` site QR code (see "Site QR code"); `POST /live {"site_id","mode":"coach"}` (see "Coach message")
Frontend dev server proxies `/api/*` → `http://localhost:8000/*`.

## capture.json (capture manifest)
Every capture directory holds `capture.json` (schema `Capture`) next to `poses.jsonl`. The API stamps `capture_id`, `site_id` and `role` into it when it assembles the upload; other keys are kept.
- `files` (`CaptureFiles`): `frames`, `poses`, `depth`, `confidence`, `feature_points`, `video`, `observer_pose`, and optional `relative_depth` (T2: folder of precomputed relative-depth maps named like the frames; jobs/recon reads it via `files.relative_depth`, falling back to a `reldepth/` folder).
- Optional details written by the iOS app, kept verbatim: `anchoring {"method": "arkit_image_leveled"|"arkit_image", "T_arkitworld_site": [16], "relocks"}`, `clock {"t0_uptime_s", "t0_unix_s"}`, `app {...}`, `stats {"keyframes", "rejected_blur", "limited_tracking_keyframes", "covered_cells", "seen_cells"}`.
- `observer_pose` json: `{"T_site_cam": [16 row-major, camera-to-site], "K": [fx, fy, cx, cy], "w", "h", "convention"?: "arkit"|"opencv"}`. Without `convention`, jobs/traffic detects it (only one reading sends the bottom-centre ray down to the floor).

## Capture upload (iOS app, web observer, services/api)
1. `POST /sites/{site_id}/captures` body `{"device": {"model","os","tier"}, "role": "scanner"|"observer", "marker"?, "n_chunks", "chunking": "independent"|"split", "files": [extra file names], "started_at", "duration_s", "privacy", "capture_files": CaptureFiles}` -> `201 {capture status..., "upload": {"chunks": [{"part","url","method":"PUT","headers","key","expires_in_s"}], "files": [{"name", ...same}]}, "complete_url"}`.
2. `PUT` each chunk (a complete ZIP; `capture.json` first in part 0) to its `url` with its `headers`. Fresh URLs for the same capture: `POST /captures/{id}/upload-urls {"parts": [i,...], "files": [...]}` -> `{"capture_id", "upload": {...}}`.
3. `POST /captures/{id}/complete` -> `202` (any JSON body, e.g. the app's part receipts, is ignored).
Capture status (`GET /captures/{id}`, items of `GET /sites/{id}/captures`): `{"capture_id","site_id","role","tier","status": "registered"|"processing"|"done"|"stored"|"failed", "upload": {"status": "waiting"|"uploading"|"uploaded", "n_expected", "n_uploaded", "progress"}, "processing": {"status": "waiting"|"processing"|"done"|"stored"|"failed", "twin_version", "error"}, "device","marker","started_at","duration_s","privacy","missing":[keys],"twin_version","error","job","created_at","updated_at"}`.

## Site QR code
`GET /sites/{id}/qr.png` encodes `<web base>/join/<site_id>?api=<percent-encoded API base>&name=<percent-encoded site name>` (`join_url` in the site payload; spaces as `%20`). A phone camera opens it as the web `/join` page, which offers the app link `clearance://join?api=...&site=<site_id>&name=...`; the iOS app (`SiteJoinParser`) parses the join URL, the app link and the JSON `qr` object `{"v":1,"kind":"clearance-site","site_id","name","api_base","join_url","marker"}`. Without `api=`, the API base is `<web origin>/api`.

## Coach message
Scanner and coach screens meet in one coach session per site: `POST /live {"site_id","mode":"coach"}` returns the open one (`200`) or opens it (`201`); the scanner joins `WS /live/{session}?role=scanner`, coach screens `?role=coach`. Message (both directions, validated and relayed by the API; the latest one is replayed to clients that join later):
```
{"type": "coach", "site_id": str, "coverage": {"cell_m": float, "origin": [x, y], "width": int, "height": int, "cells": [int]} | null,
 "prompt": str | null, "t": float (unix s), "status"?: {"device", "frames", "tracking", "keyframes", "covered_frac"}}
```
`cells`: row-major, row 0 = max y (like the occupancy grid), 0 = unseen, 1 = seen from one direction, 2+ = seen from two or more; `origin` = min-x/min-y corner; at most 40 000 cells and 128 KiB per message.

## Twin assets
`Twin.assets` keys: `splat` (.ply/.splat/.ksplat), `mesh` / `mesh_glb` (.glb), `occluder_usdz` (.usdz, site frame, Z up), `pointcloud` (.ply); other keys (e.g. `capture_id`) are metadata. Values are storage keys under `sites/<site_id>/` or absolute URLs. `GET /sites/{id}/twin/assets/{name}` takes the key (`splat`) or a file name (`splat.ply`, `occluder.usdz` -> `occluder_usdz`; the extension must match the stored file), serves only keys under the site's prefix, and redirects (307) for absolute URLs. The web viewer and the iOS replay load assets through this route.

## Pinch Map grid
`GET /sim/batches/{id}/bottleneck.npy`: float32 `.npy` with the twin occupancy grid's shape (row 0 = max y), from `SimBatch.bottleneck_uri` (or `<batch>.bottleneck.npy` beside the batch JSON); 404 when the batch recorded none.

## Operator-confirmed floor (Twin.clear_zones)
`ClearZone{id, polygon, reason, confirmed_by, confirmed_at}`. Inside a zone, unseen (UNKNOWN) cells are treated as free; measured obstacles stay occupied. Applied before geometry bounds in every rasterization (feasibility, batches, live sessions, optimizer, route-gap prescreen). API: at most 20 zones, each ≤ 4 m², simple polygons inside the grid; `confirmed_by`/`confirmed_at` stamped on save. The analysis records a note with count, area and who confirmed.

## Geometry conventions
- `Primitive.pose.xyz` is the **box center**: `z = h/2` for an object standing on the floor; `size = (l along yaw, w, h)`.

## Measurement fact refs
`Fact.origin.ref` for `type: "measurement"` uses one of the fixed forms `<entity id>` (primitive / threshold / waypoint / keep-out id), `xy:<x>,<y>`, `seg:<x1>,<y1>,<x2>,<y2>` (site metres). `agents/report/facts.py` passes `analysis.measurements[].ref` through verbatim and otherwise emits twin-scoped refs: `<twin_id>:threshold:<id>`, `<twin_id>:hazard:<index>`, `<twin_id>:measurement:<name>`, `<twin_id>:accuracy|floor|meta|traffic|hazards`. The web report resolves thresholds to the entity, hazards to `Twin.hazards[index].xy`, and draws nothing for the rest.

## Traffic (jobs/traffic)
`<out_prefix>/traffic_stats.json` (out_prefix e.g. `sites/<site_id>/twin_v<N>/traffic/`): `{"source": "observed"|"user_estimated", "t_range", "window_s", "heatmap_mean", "heatmap_windows": [{"start_s","key"}], "heatmap_units", "arrival_per_zone_per_min", "window_rates_per_min", "density_levels_per_min", "walk_speed_mps", "walk_speed_observed", "observers": [...], "n_floor_tracks", "n_people_after_merge", "wall_time_s", ...}`; the user-estimated fallback writes `{"source": "user_estimated", "reason", "observers"}`. `TrafficModel.heatmap_uri` points at `heatmap.npy`.

## Optimizer output (jobs/optimizer)
Each `optimize_layout` entry carries, besides `change`, `description`, `before`, `after`, `batch_ids`: `summary` (one-line text), `uplift` (after - before success rate), `significant` (95% CIs do not overlap), `status` ("significant" | "not significant"), `objects` (moved object ids), `bound`, `density`; the best entry also has `before_conservative` / `after_conservative` (BatchResults on the verdict bound) and `verdict_batch_ids`.

## analysis.json extra keys (agents/orchestrator)
Besides the layout above: `candidate_details` [candidate dicts], `robots` {robot_id: RobotSpec}, `feasibility_by_bound` {robot_id: {bound: Feasibility}}, `investigations` {robot_id: Investigation}, `investigation_ids`, `optimizer_hints` {robot_id: [object ids]}, `confirmation_batches` {robot_id: {"batch_id","change","description","results","legs"}}, `citations` [{"used_for","url",...}], plus `excluded`, `candidate_explanations`, `batch_index`, `skipped_batches`, `roi`, `tool_calls`, `sim_source`.

## Investigation
`Investigation.facts`: every Fact registered by the investigation tools; experiment `baseline_fact_ids` / `result_fact_ids`, step `fact_ids` and `[f_xxx]` tags in conclusions refer to these ids.

## Job spec (services/api/tasks.py)
`python -m services.api.tasks` and `infra/nebius/entry.py task` run one job from `CLEARANCE_JOB_SPEC` (raw JSON), `CLEARANCE_JOB_SPEC_B64` (base64 JSON) or `CLEARANCE_JOB_SPEC_KEY` (storage key under `CLEARANCE_DATA`), first match wins; spec `{"job_id", "entrypoint": "module:function", "kwargs"}`; result -> `jobs/<job_id>/result.json`.
