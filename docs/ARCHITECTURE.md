# Clearance architecture

**Clearance: Know if a robot fits before you buy it.**

Clearance starts in the real world and ends there. Ordinary phones scan a real café, storeroom or office; Clearance turns the scan into a measured twin, checks which commercial robots physically fit, runs thousands of simulated trials against the site's real foot traffic, and shows the chosen robot working in the real room through AR. Simulation is the engine in the middle: it is how we answer the question, not the product the customer sees.

Every number in the Clearance Report comes from code (a measurement, a simulation batch, a cited source or a user input). Nemotron models plan, investigate and explain; they never produce measurements, and they never set verdicts.

## 1. System overview

```
  REAL WORLD                                   NEBIUS AI CLOUD + TOKEN FACTORY
 ┌──────────────────────┐            ┌──────────────────────────────────────────────────────────┐
 │ iPhone (iOS app)     │  HTTPS     │  API service: FastAPI + web console                       │
 │  ARKit scan, marker  │──────────▶│  (Serverless Endpoint, image clearance-api)               │
 │  origin, face blur   │◀──────────│   ├─ Postgres or SQLite: sites, jobs, results, usage      │
 │  AR replay in room   │  presigned │   ├─ Object Storage (S3): blurred captures, twins, runs   │
 └──────────────────────┘  PUT/GET   │   └─ Job dispatcher (content-addressed, idempotent)        │
 ┌──────────────────────┐            │            │                                              │
 │ Android observers    │──────────▶│            ▼                                              │
 │  fixed stands, marker│            │  Serverless Jobs                                           │
 │  PnP pose, face blur │            │   ├─ J1 ingest + sync            (CPU)                     │
 └──────────────────────┘            │   ├─ J2 reconstruct + fuse       (GPU, clearance-jobs-gpu) │
 ┌──────────────────────┐            │   ├─ J3 splat train              (GPU)                     │
 │ Phone joystick (WS)  │──────────▶│   ├─ J4 segment + primitives     (GPU) ──▶ scene model     │
 │  chaos mode          │            │   ├─ J5 traffic tracks           (GPU/CPU)                 │
 └──────────────────────┘            │   ├─ J6 sim shards x N           (CPU, clearance-jobs-cpu) │
 ┌──────────────────────┐            │   └─ J7 layout optimizer         (CPU)                     │
 │ Laptop: web console  │◀─────────▶│                                                            │
 │  twin editor, report │            │  Scene model endpoint: Cosmos Reason 2 via vLLM (GPU)      │
 │  splat viewer        │            │   alternative: Nemotron 3.5 Super VL on Token Factory      │
 └──────────────────────┘            │                                                            │
                                     │  Token Factory: Nemotron Nano / Super / Ultra tiers        │
                                     └──────────────────────────────────────────────────────────┘
                                                     │
                                                     ▼
                                          Tavily Search / Extract (robot specs, prices, citations)
```

Model IDs per tier live in `config/models.yaml` and are confirmed against `GET /v1/models` (docs/VERIFIED.md Section 1A). The Nano and Super tiers run on `nvidia/Nemotron-3_5-Lightning` and `nvidia/Nemotron-3.5-Super-VL`, which serve through the judging period. The older Nano and Super IDs, which leave Token Factory Serverless on 2026-11-09, stay listed as second candidates.

## 2. End-to-end flow with the gate and the investigation loop

```
 Phones scan the real space
          │
          ▼
 J1 ingest ─▶ J2 geometry (ARKit poses + LiDAR or DA-V2 Small depth, TSDF, floor fit, occupancy)
          │
          ▼
 ┌───────────────────────────── SCAN GATE: can a scan become a trustworthy occupancy grid? ───────┐
 │  Reference rooms with known geometry (jobs/reference) + one tape-checked real room             │
 │  GO  : median error ≤ 3 cm (T1) / ≤ 5 cm (T2), 0.80 m aisle reads passable ±5 cm               │
 │        → scan geometry is the primary twin source                                              │
 │  ELSE: twin editor tracing over the scan/photo with tape-entered widths is the primary source; │
 │        the scan provides visuals. Everything downstream is unchanged.                         │
 │  Result recorded in docs/GATE.md                                                               │
 └───────────────────────────────────────────────────────────────────────────────────────────────┘
          │
          ▼
 J3 splat, J4 primitives + scene hazards, J5 traffic ─▶ Twin (versioned, editable)
          │
          ▼
 Twin editor (human): fix labels, movable flags, keep-out zones, waypoints, "measure on site" thresholds
          │
          ▼
 Clearance check
   1. Candidates: code filters the catalog (footprint vs narrowest gap, threshold limits, payload)
   2. Feasibility per task leg on optimistic / nominal / conservative geometry bounds
   3. Monte Carlo batches (J6 shards) at quiet / normal / peak traffic → SimBatch + Pinch Map
          │
          ▼
 ┌──────────────── Nemotron investigation loop (Super, ≤ 8 steps, ≤ 6 experiments per robot) ────┐
 │  observe     get_batch_summary, get_bottlenecks, get_episode_events, get_gap_at,              │
 │              get_objects_near                                                                 │
 │  hypothesize "fails at the counter corridor at peak traffic"                                  │
 │  probe       run_probe(overrides | {zone_id, ped_scale} | {density} | {robot_id}, ≤ 30 eps)    │
 │  conclude    record_finding(claim, evidence_fact_ids, object_ids, suggested_change) → finish  │
 │  A finding is confirmed only when its probe beats the CI half-width.                           │
 │  Every tool result is a Fact (origin.type = "investigation"); the model cites only fact IDs.   │
 │  Offline mode: a deterministic policy drives the same tools (CI, offline runs).                │
 └───────────────────────────────────────────────────────────────────────────────────────────────┘
          │ findings feed hint_object_ids
          ▼
 J7 layout optimizer ─▶ confirming batch at normal traffic, conservative bound
          │
          ▼
 Verdict (code only: clearance_core/verdict.py::decide_verdict, thresholds in config/sim_defaults.yaml)
   Cleared | Cleared with changes | Not cleared (+ main reason)
          │
          ▼
 Facts pack ─▶ Nemotron Ultra writes the Clearance Report ("How we investigated" from the trail)
          │
          ▼
 Fact checker: every number sits next to a valid [f_xxx] tag and matches it; verdict labels match
 the code verdict; failures regenerate once, then the sentence is removed and logged
          │
          ▼
 Clearance Report in the console + AR replay of the chosen robot in the real room
```

## 3. Components

| Component | Responsibility | Tech |
|---|---|---|
| `apps/ios` | Scan capture, marker anchoring, on-device face blur, AR replay | Swift, ARKit, RealityKit |
| `apps/web` | Console, twin editor, Clearance Report, splat viewer, observer page, joystick, coach | React, Vite, TypeScript, three.js, GaussianSplats3D, MediaPipe Tasks |
| `services/api` | Sites, captures, presigned uploads, job dispatch, results, live sessions, usage | FastAPI, SQLAlchemy, Pydantic |
| `clearance_core` | Shared schemas, grid I/O, storage keys, verdict rule | Pydantic, NumPy |
| `jobs/recon`, `jobs/splat`, `jobs/semantics`, `jobs/traffic` | J1–J5 | OpenCV, Open3D, DA-V2 Small, gsplat, Grounding DINO, SAM 2, ByteTrack |
| `jobs/reference` | Reference rooms with known ground-truth geometry for the gate and golden tests | NumPy, Shapely |
| `jobs/sim`, `jobs/optimizer` | Feasibility, Monte Carlo navigation sim, layout search | NumPy, Numba, Shapely |
| `agents/` | LLM client and usage log, catalog builder, orchestrator, investigation loop, report, fact checker | OpenAI-compatible client to Token Factory and vLLM, Tavily |
| `infra/` | Container images, Nebius job and endpoint definitions, deploy scripts, local compose | Docker, Nebius AI Cloud CLI |

## 4. Deployment on Nebius

| Piece | Where it runs | Definition |
|---|---|---|
| API + web console | Serverless Endpoint, `cpu-d3` (`4vcpu-16gb`), managed HTTPS URL, bucket mounted at `/data` | `infra/Dockerfile.api`, `infra/nebius/endpoints/api.env`, `infra/nebius/deploy_api_endpoint.sh` |
| Recon / splat / semantics (J2–J4) | Serverless Job, `gpu-l40s-a` (`1gpu-8vcpu-32gb`) | `infra/Dockerfile.jobs-gpu`, `infra/jobs/recon.yaml` |
| Clearance check (analysis) | Serverless Job, `cpu-d3` (`16vcpu-64gb`) | `infra/Dockerfile.jobs-cpu`, `infra/jobs/analysis.yaml` |
| Simulation shards (J6) | N Serverless Jobs, `cpu-d3` (`4vcpu-16gb`), one `run_shard` each | `infra/jobs/sim_shard.yaml`, `infra/nebius/submit_sim_shards.py` |
| Scene model | Serverless Endpoint, `gpu-h100-sxm` (`1gpu-16vcpu-200gb`), vLLM, token auth | `infra/nebius/endpoints/cosmos_reason2.env`, `infra/nebius/deploy_vision_endpoint.sh` |
| Language models | Token Factory, `https://api.tokenfactory.nebius.com/v1/` | `config/models.yaml` |
| Storage | Object Storage, `https://storage.eu-north1.nebius.cloud`, private bucket, CORS for presigned PUT | `infra/nebius/create_bucket.sh` |

How a job travels: the API's `NebiusDispatcher` reads `infra/jobs/<kind>.yaml` and runs its `submit_command`, which is `infra/nebius/submit_job.py`. That script builds `nebius ai job create` with the platform, preset, timeout, bucket volume and a base64 job spec (large specs go to the bucket and travel as a key). Inside the container `infra/nebius/entry.py` decodes the spec and runs `services.api.tasks` (or `jobs.sim.api.run_shard`). Results are written under `/data`, which is the mounted bucket, so the API reads them where it reads everything else. `infra/nebius/job_status.py` maps the Nebius job state to the dispatcher's status vocabulary.

Locally the same API runs with the in-process dispatcher (`CLEARANCE_DISPATCHER=local`), SQLite and the filesystem, in offline mode without any keys: `bash scripts/dev.sh dev` or `docker compose -f infra/docker-compose.yml up --build`.

## 5. Key decisions

| Decision | Chosen | Why |
|---|---|---|
| What leads the story | Real world first: phones in a real room, AR robot in the real room | It is what a buyer trusts; simulation explains the result |
| Simulation fidelity | 2D footprint-accurate navigation on an occupancy grid | Wheeled robots on flat floors are dominated by geometry and traffic; fast enough for thousands of trials and easy to explain |
| Geometry | Fused depth + editable primitives, checked by the scan gate (`docs/GATE.md`) | Humans can verify primitives; the gate decides how much we trust the scan |
| LLM role | Planning, investigation, extraction, writing | Numbers and verdicts come from code |
| Scene model | Cosmos Reason 2 self-hosted; Nemotron 3.5 Super VL alternative | Physical-AI reasoning model on our own endpoint, with a managed option that needs no GPU |
| Robot rendering | Parametric shapes from spec dimensions | No vendor IP; visibly a model of the robot |
| Jobs | Content-addressed and idempotent | Re-running a stage with the same inputs returns the cached result |

## 6. Scope

- Built for wheeled service and delivery robots and indoor AMRs doing point-to-point delivery tasks.
- Built for indoor sites on one floor. Humanoids, manipulation, outdoor sites, elevators, fleet management and vendor certification are outside what this version checks.
- Results are simulation-based estimates, shown as ranges across scan uncertainty with sample sizes.
