# Clearance evaluation: measured results

Every number on this page comes from a run on the development machine (Windows 11, Intel x86-64, 12 logical
cores, CPU only) on 2026-10-27/28, plus one on-site phone scan (section 1b). Sources: `docs/GATE.md` (scan-to-occupancy gate), `pytest`, `docs/PROOF.md`
(usage table of the live run) and the three reference Clearance checks produced by `scripts/release_proof.py
--fresh-usage --force` through `scripts/run_reference_checks.py` (run records in
`sample_sites/<site>/analyses/<analysis_id>/run.json`). Simulation results are simulation-based estimates.

## 1. Scan accuracy on reference rooms (PLAN 15.1)

From `scripts/gate_scan_to_occupancy.py` (see `docs/GATE.md`, regenerated 2026-10-27 23:53, wall time 1.9 min):
reference rooms rendered into phone-like captures with sensor noise, pose drift and tracking dropouts,
reconstructed by `jobs/recon`, and compared with tape-style ground-truth distances on the 2.5 cm occupancy grid.

| set | tier | n distances | median abs err | p90 abs err | max abs err | GO limit (median) |
|---|---|---|---|---|---|---|
| gate (cafe + storeroom) | T1_LIDAR | 60 | 1.2 cm | 4.6 cm | 27.6 cm | 3 cm |
| gate (cafe + storeroom) | T2_VIO_MONO | 60 | 0.9 cm | 11.1 cm | 26.4 cm | 5 cm |
| held-out (corridor) | T1_LIDAR | 22 | 0.6 cm | 3.7 cm | 10.1 cm | 3 cm |
| held-out (corridor) | T2_VIO_MONO | 22 | 2.4 cm | 9.2 cm | 265.3 cm | 5 cm |

- The 0.80 m storeroom aisle reads passable within +-5 cm on all four runs (median 0.793-0.800 m, worst error 3.2 cm).
- This run uses the current reference rooms (cafe dock station on the west wall, table serving spots in the aisle).
  The table waypoints are not gate distances, so the occupancy grids and ground-truth distances are unchanged.
- Reference-room software half of the gate: GO (all GO criteria hold). The largest errors are occlusion shadows
  (floor hidden behind a cart or chair reads as unknown, which the simulator treats as blocked) and one T2 corridor
  length (-265.3 cm).

## 1b. On-site scan accuracy (real room)

The gate's hardware half (`docs/GATE.md`): an iPhone 13 (iPhone14,5, iOS 18, tier T2_VIO_MONO, no LiDAR) scanned a
real room on 2026-10-28: 397 keyframes, 232 depth frames, Depth Anything V2 Small relative depth aligned to ARKit
feature points, reconstructed by `jobs/recon` into a 2.5 cm occupancy grid. Eight tape measurements of object
extents (`docs/onsite_tape_check.json`):

| name | scan | tape | error |
|---|---|---|---|
| O1_length | 1.950 m | 1.910 m | 4.0 cm |
| O1_depth | 0.650 m | 0.640 m | 1.0 cm |
| O2_width | 0.750 m | 0.740 m | 1.0 cm |
| O2_length | 2.400 m | 2.410 m | 1.0 cm |
| O3_width | 0.430 m | 0.440 m | 1.0 cm |
| O3_depth | 0.350 m | 0.350 m | 0.0 cm |
| O6_width | 0.880 m | 0.855 m | 2.5 cm |
| O6_depth | 0.450 m | 0.440 m | 1.0 cm |

Median abs error 1.0 cm, p90 3.0 cm, max 4.0 cm (T2 GO limit: 5 cm median). **On-site half of the gate: GO**, so
both halves of the scan-to-occupancy gate are GO. The protocol asks for 10 or more distances including an aisle
width; the next on-site run adds two taped aisle widths.

## 1c. Clearance check in the real room

The twin from section 1b (version 4, one operator-confirmed clear-floor patch of 0.57 m2) ran a full Clearance check
on 2026-10-28 with live models: five café robots, task DOCK -> PICKUP -> TBL1 -> DOCK, 100 episodes per batch,
202 s end to end on the laptop.

| Robot | Verdict | Success, quiet / normal / peak (nominal) | Conservative, normal | Main reason |
|---|---|---|---|---|
| Keenon DINERBOT T8 | **Cleared** | 100% / 99% / 95% | 100% | meets every rule; narrowest gap 0.43 m vs 0.38 m needed |
| Bear Robotics Servi | Not cleared | not simulated | not simulated | no path DOCK -> PICKUP: narrowest gap 0.52 m vs 0.62 m needed to turn |
| Bear Robotics Servi Plus | Not cleared | not simulated | not simulated | stop point DOCK sits 0.19 m from a wall; the footprint needs 0.27 m |
| Keenon DINERBOT T9 | Not cleared | not simulated | not simulated | stop point DOCK sits 0.19 m from a wall; the footprint needs 0.25 m |
| Pudu BellaBot | Not cleared | not simulated | not simulated | stop point DOCK sits 0.19 m from a wall; the footprint needs 0.27 m |

The DINERBOT T8 replay was then shown in AR on the iPhone, anchored to the same floor marker. Full report:
[`docs/onsite_clearance_report.md`](onsite_clearance_report.md); fact-check 0 errors.

## 2. Golden simulation tests (PLAN 15.2)

`pytest tests/golden`: **7 passed** (9.8 s). Full suite `pytest -q tests`: 2698 passed, 2 skipped (symlink tests need extra privileges on Windows).

| test | status |
|---|---|
| aisle narrower than the robot -> 0 % success, `no_path_static` | passed |
| aisle 2 cm wider than the robot with in-place rotation -> path exists | passed |
| tight turn needs in-place rotation | passed |
| threshold above the robot limit -> fails at the threshold | passed |
| threshold within the limit passes; a flagged threshold is unknown | passed |
| empty room, no pedestrians -> 100 % success, mission time within 5 % | passed |
| same seed -> bit-identical episode output | passed |

## 3. Reference Clearance checks (full pipeline, live models)

Each check ran the whole pipeline in live mode (`CLEARANCE_LLM_MODE=live`, `CLEARANCE_TAVILY_MODE=live`) via
`python scripts/release_proof.py --fresh-usage --force` (usage DB archived first, so `docs/PROOF.md` counts only
this run): code candidate filters -> Nemotron Super candidate ordering (with live Tavily search) -> static
feasibility on three geometry bounds -> Monte Carlo batches on a local process pool -> Nemotron Super investigation
loop -> layout optimizer -> code verdicts (`clearance_core/verdict.py`) -> facts -> Clearance Report from the
hybrid writer (code renders every table and verdict block; Nemotron Ultra writes the narrative sections, each
fact-checked on its own) -> fact-check. The catalog robots are real vendor products; their specs were reviewed by
the site operator on 2026-10-28, so the reports label them "human-reviewed". The checks keep the
`include_awaiting_review=True` option, under which an unreviewed entry would be simulated and labelled
"awaiting review".

Business inputs are example operator inputs entered by the user (staff cost per hour, task hours per day, days per
week, maintenance %). Every robot's price is "price on request", so the payback section asks for a quote.

**Budget** (identical for all three sites, `scripts/run_reference_checks.py`): up to 6 robots x 3 traffic levels
(quiet, normal, peak) x 3 geometry bounds (optimistic, nominal, conservative) x 40 episodes per batch;
investigation on up to 2 robots (8 steps, 6 experiments, 30-episode probes); optimizer on the 2 most promising
robots that are not Cleared (candidate ranking order; 30-episode screen, 50-episode confirm, at most 4 objects x 8
moves, 900 episodes per robot); 50-episode confirmation batch per recommended change; 11 worker processes; seed
base 1000. The simulator's pinch handling now costs about twice the time per episode, so the budget dropped from 50
to 40 episodes per batch and from 3 to 2 optimizer robots. Calibration run (cafe, offline mode, same budget):
719.2 s wall (batches 290.5 s, optimizer 351.9 s).

### Reference rooms used

`jobs/reference/rooms.py` (seed 0), regenerated into `sample_sites/` for these runs:

- **Cafe:** each table waypoint `T1`-`T6` is a serving spot beside its table on the aisle side, with the robot's
  centre 0.42 m from the table edge (tray robots park beside the table in the open aisle; the earlier spots were
  0.175 m from the edge, where no tray robot 0.5 m or wider can stop). Measured after regeneration: each spot is
  0.42 m from its own table and 0.52-0.54 m from the table across the aisle. Furniture, walls, the occupancy grid
  and the ground-truth distances are unchanged. The charging dock is on the west wall with about 1 m of open floor
  in front of it. `PICKUP` faces the kitchen pass-through worktop. The single street door carries both `ENTRANCE`
  and `EXIT`.
- **Storeroom:** `ENTRANCE` just inside the loading door, `EXIT` in the door opening.
- **Corridor:** `ENTRANCE` at the south door to the lobby (S1), `EXIT` at the south door to the stairwell (S2).

### Verdicts

Success rates are at normal traffic, conservative / nominal / optimistic geometry bound, 40 episodes per batch.

| site | task | robot | verdict | main reason (code) | success, normal traffic |
|---|---|---|---|---|---|
| ref_cafe | DOCK -> PICKUP -> T1 -> T3 -> T5 -> DOCK | Keenon DINERBOT T8 | Cleared with changes | threshold D1 must be measured manually; cleared after measuring D1 (holds only if it is within the robot's limit) | 100 / 97.5 / 97.5 % |
| ref_cafe | | Bear Servi | Not cleared | threshold D1 must be measured manually | 72.5 / 75 / 95 % |
| ref_cafe | | Keenon W3 | Not cleared | threshold D1 must be measured manually | 57.5 / 70 / 75 % |
| ref_cafe | | Keenon DINERBOT T9 | Not cleared | threshold D1 must be measured manually | 40 / 47.5 / 57.5 % |
| ref_storeroom | DOCK -> PICKUP -> S1 -> S2 -> S3 -> S4 -> DOCK | Pudu T300 | Cleared | meets every rule as-is (6/6 legs) | 97.5 % (all bounds) |
| ref_storeroom | | OTTO 100 | Cleared | meets every rule as-is (6/6 legs) | 97.5 % |
| ref_storeroom | | MiR250 | Not cleared | success 88 % at normal traffic (needs 90 %) | 87.5 % |
| ref_storeroom | | MiR100 | Not cleared | success 88 % at normal traffic (needs 90 %) | 87.5 % |
| ref_corridor | DOCK -> PICKUP -> O4 -> O3 -> O2 -> O1 -> DOCK | Keenon DINERBOT T8 | Cleared | meets every rule as-is (6/6 legs) | 92.5 % (all bounds) |
| ref_corridor | | Bear Servi | Cleared | meets every rule as-is | 95 % |
| ref_corridor | | Pudu KettyBot | Cleared | meets every rule as-is | 92.5 % |
| ref_corridor | | Keenon W3 | Cleared | meets every rule as-is | 100 % |
| ref_corridor | | Keenon DINERBOT T9 | Cleared | meets every rule as-is | 95 % |
| ref_corridor | | Pudu T300 | Cleared | meets every rule as-is | 92.5 % |

What the geometry did:

- **Cafe.** 8 Class A robots were considered; 4 passed the code pre-screen. Bear Servi Plus, Pudu BellaBot, Pudu
  KettyBot and Pudu PuduBot 2 were excluded because every route to the kitchen crosses the 2.0 cm door lip `D1`
  above their limit. For the other four the limit is not published, so `D1` stays pending (3 of 5 legs settled;
  the 2 legs through the kitchen door wait for the on-site measurement). With the serving spots in the aisle, every
  table waypoint is reachable for all four robots, including the 0.50 m DINERBOT T9 that previously had no pose at
  `T1`/`T3`. The DINERBOT T8 meets the success rule (100 % conservative), so it is Cleared with changes once `D1` is
  measured within its limit. The optimizer ran on the DINERBOT T9 (move chair `CH4W` 0.05 m east: 60 % -> 74 %,
  n=50, normal traffic, nominal bound; flagged not significant, so not recommended) and Bear Servi (no better move
  found).
- **Storeroom.** All four Class B robots pass the 0.78 m aisles statically (6/6 legs). Random clutter boxes and
  loading-door traffic cause the failures; the Pudu T300 and OTTO 100 stay above 90 % at normal traffic, both MiR
  robots reach 87.5 %. The optimizer found no layout change for either MiR.
- **Corridor.** The six robots ranked first (budget of 6) all clear the corridor as-is; the other six were left out
  by the budget rule.

### Wall time per Clearance check (live run)

| site | robots simulated | batch episodes | wall time | candidates | feasibility | batches | investigation | optimizer | verdicts | report |
|---|---|---|---|---|---|---|---|---|---|---|
| ref_cafe | 4 | 1440 | 1958.4 s | 38.0 s | 10.0 s | 499.3 s | 96.7 s | 989.9 s | 95.4 s | 228.1 s |
| ref_storeroom | 4 | 1440 | 1484.7 s | 35.5 s | 11.3 s | 1012.3 s | 49.8 s | 118.6 s | 0.1 s | 255.9 s |
| ref_corridor | 6 | 2160 | 4948.6 s | 28.0 s | 11.0 s | 4536.3 s | 9.6 s | 7.8 s | 7.7 s | 332.7 s |

The report stage now takes 228-333 s per site: the narrative sections are requested in parallel (4 at a time),
each Ultra call takes 15-130 s. The single-pass writer of the previous run needed 234-356 s per site for one call.

These checks ran while other compute-heavy jobs at normal priority shared the machine (the checks ran at
below-normal priority so they would not slow the API server of a concurrent phone test). The simulation stages are
therefore 2-20 times slower than in the previous run (corridor batches 4536.3 s here vs 227.5 s there; the first
corridor run of this proof, 494.5 s wall time and 232.4 s of batches, was repeated because its report metrics were
not saved: with no investigation the writer had no analysis id; it now finds it from the saved facts pack). The
wall times above are not a throughput measurement.

### Simulation throughput

Measured over the batch stage (11 worker processes on 12 logical cores, one batch per process), shared CPU:

| site | episodes | batch stage | episodes / min |
|---|---|---|---|
| ref_cafe | 1440 | 499.3 s | 173.0 |
| ref_storeroom | 1440 | 1012.3 s | 85.3 |
| ref_corridor | 2160 | 4536.3 s | 28.6 |

The previous run on a quieter machine measured 263.5, 220.9 and 569.6 episodes per minute for the same batches.

### Fact-check

`agents/factcheck/check.py` over the three published reports (`sample_sites/<site>/analyses/<id>/report.md`):
**3 of 3 published reports pass, 0 errors.** Writer metrics per report are in `report.json` (key `writer`) and
`report_meta.json` beside it.

| report | first-pass errors, single-pass writer (previous run) | first-pass errors, hybrid writer (sum over narrative sections) | of which one runaway draft | regenerations | sentences removed | template fallbacks | published: numbers checked | published: errors | distinct fact tags |
|---|---|---|---|---|---|---|---|---|---|
| ref_cafe | 33 | 2 | none | 2 of 7 sections | 0 | none | 223 | 0 | 173 |
| ref_storeroom | 41 | 171 | 165 (why Pudu T300) | 5 of 6 | 3 | none | 159 | 0 | 135 |
| ref_corridor | 22 | 132 | 125 (trade-offs) | 6 of 8 | 2 | none | 222 | 0 | 191 |

- Without the two runaway drafts the first-pass errors are 2, 6 and 7 per report (33, 41 and 22 before). A runaway
  draft repeats the section text until the output budget ends (9,338 and 8,980 output tokens over the two calls of
  that section); the regeneration with the error list produced a clean section in both cases. The writer now also
  tells the model the allowed length and sends back only the start of such a draft (unit-tested; not yet measured
  in a live run).
- The remaining first-pass errors are `value_mismatch` (a number next to the tag of a neighbouring fact) and a few
  `untagged_number` / `unit_mismatch`. Every section that failed was regenerated once; 5 sentences were removed in
  total, no section fell back to the template text.
- Required blocks are rendered by code and present in all three reports: the robot comparison table (one row per
  verdict, conservative-optimistic success ranges), a rule-check table per robot under its code verdict,
  "How we investigated", the Pinch Map summary, the recommended changes table, payback, hazards and manual checks,
  methodology, scope and sources. The catalog review label reads "human-reviewed" for every robot in these runs.
- The fact checker no longer accepts a bare integer next to a ratio tag ("layout change 1 ... 78% [f]" matched the
  78% fact by rounding); such a number is now an error.

### Investigation loop (live Nemotron Super)

| site | robots investigated | experiments run | probe episodes | findings recorded |
|---|---|---|---|---|
| ref_cafe | 2 (Bear Servi, Keenon DINERBOT T9) | 1 | 30 | 0 |
| ref_storeroom | 2 (MiR100, MiR250) | 0 | 0 | 0 |
| ref_corridor | 0 (every robot met the success rule) | 0 | 0 | 0 |

- Cafe, DINERBOT T9: hypothesis "moving chair `CH4W` away from the top bottleneck widens the critical passage";
  the probe moved `CH4W` (dx 0.5 m, dy 0.5 m): 100 % success (30 episodes) vs 48 % baseline, +52 points vs a probe
  CI half-width of 6 points -> supported.
- The Bear Servi, MiR100 and MiR250 loops used their 8 steps on evidence tools without running a probe; no loop
  recorded a finding before the step limit.

### Live model usage (from `docs/PROOF.md`)

| tier | calls | failed | tokens in | tokens out | median latency |
|---|---:|---:|---:|---:|---:|
| Nemotron Super (candidate ordering, investigation) | 45 | 0 | 152,805 | 22,007 | 2.4 s |
| Nemotron Ultra (report narrative sections) | 57 | 0 | 147,026 | 138,028 | 40.3 s |
| total | 102 | 0 | 299,831 | 160,035 | |

LLM calls by tier and site (attributed by analysis id): cafe super 19, ultra 9; storeroom super 20, ultra 11;
corridor super 3, ultra 14. The other 23 Ultra and 3 Super calls in the usage table carry no analysis id: the first
corridor run of this proof (before the analysis-id fix) and a concurrent Clearance check on the local API server,
which writes to the same usage table. No call failed and no stage fell back to offline mode. Cost: the prices are
not verified, so `docs/PROOF.md` shows "not metered".

## 4. Next measurement step: on site (PLAN 15.3 and the gate's hardware half)

The reference-room numbers above measure the software against known geometry. The next step measures it against a
real room:

1. **On-site scan run**: done with an iPhone 13 (T2_VIO_MONO), GO (section 1b). The next run adds two taped aisle
   widths (protocol in `docs/GATE.md`: 10 or more distances, narrowest aisle passable at its taped width +-5 cm) and
   a LiDAR iPhone for the T1 tier.
2. **Cardboard robot validation**: cut cardboard footprints at the true catalog dimensions of two robots (one large,
   for example MiR100 at 0.89 m x 0.58 m, one small, the DINERBOT T8 at 0.47 m x 0.38 m), push each along the
   planned routes in the scanned room, and record pass/fail at every pinch point that static feasibility flags.
   Target: at least 90 % agreement between predicted and observed pass/fail. Film it.
3. **Thresholds**: measure every door lip on the route with a ruler or feeler gauge and enter it in the twin
   editor; flagged thresholds then resolve in the verdict.
4. **Catalog review**: a human reviews the 12 catalog specs against the cited vendor pages
   (`POST /catalog/robots/{id}/verify`), after which checks run with reviewed specs only (the default).

## How to reproduce

```bash
PYTHONPATH=. python scripts/release_proof.py --fresh-usage --force
```

This archives the usage DB, runs the three checks in live mode, copies compact artifacts into `sample_sites/<site>/analyses/<id>/` (served by
services/api at startup) and writes the web app's offline-mode fixtures (`apps/web/src/offline/fixtures/`).
