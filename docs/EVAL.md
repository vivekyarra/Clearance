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
`python scripts/release_proof.py --require-provider token_factory --fresh-usage --force` on 2026-10-28 (usage DB
archived first, so `docs/PROOF.md` counts only this run; every Nemotron call was served by Nebius Token Factory):
code candidate filters -> Nemotron Super candidate ordering (with live Tavily search) -> static
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
  measured within its limit. The optimizer ran on the Keenon W3 (move chair `CH4W` 0.05 m east: 78 % -> 96 %,
  n=50, normal traffic; flagged not significant, so not recommended) and Bear Servi (no better move found).
- **Storeroom.** All four Class B robots pass the 0.78 m aisles statically (6/6 legs). Random clutter boxes and
  loading-door traffic cause the failures; the Pudu T300 and OTTO 100 stay above 90 % at normal traffic, both MiR
  robots reach 87.5 %. The optimizer found no layout change for either MiR.
- **Corridor.** The six robots ranked first (budget of 6) all clear the corridor as-is; the other six were left out
  by the budget rule.

### Wall time per Clearance check (live run)

| site | robots simulated | batch episodes | wall time | candidates | feasibility | batches | investigation | optimizer | verdicts | report |
|---|---|---|---|---|---|---|---|---|---|---|
| ref_cafe | 4 | 1440 | 3050.0 s | 75.0 s | 28.0 s | 1221.7 s | 202.5 s | 1257.7 s | 227.7 s | 27.6 s |
| ref_storeroom | 4 | 1440 | 1409.2 s | 64.5 s | 32.4 s | 911.8 s | 151.6 s | 219.9 s | 0.3 s | 27.5 s |
| ref_corridor | 6 | 2160 | 538.7 s | 9.9 s | 63.7 s | 406.1 s | 0.1 s | 0.6 s | 0.2 s | 56.7 s |

On Token Factory the report stage takes 28-57 s per site: the narrative sections are requested in parallel (4 at a
time) and the median Nemotron Ultra call takes 6.9 s.

Other compute-heavy jobs shared the machine during these checks, so the simulation stages vary from run to run;
the wall times above are not a throughput measurement. The simulation results themselves use fixed seeds and match the
previous run episode for episode.

### Simulation throughput

Measured over the batch stage (11 worker processes on 12 logical cores, one batch per process), shared CPU:

| site | episodes | batch stage | episodes / min |
|---|---|---|---|
| ref_cafe | 1440 | 1221.7 s | 70.7 |
| ref_storeroom | 1440 | 911.8 s | 94.8 |
| ref_corridor | 2160 | 406.1 s | 319.1 |

An earlier run on a quieter machine measured 263.5, 220.9 and 569.6 episodes per minute for the same batches.

### Fact-check

`agents/factcheck/check.py` over the three published reports (`sample_sites/<site>/analyses/<id>/report.md`):
**3 of 3 published reports pass, 0 errors.** Writer metrics per report are in `report.json` (key `writer`) and
`report_meta.json` beside it.

| report | first-pass errors, single-pass writer (previous run) | first-pass errors, hybrid writer (sum over narrative sections) | of which one runaway draft | regenerations | sentences removed | template fallbacks | published: numbers checked | published: errors | distinct fact tags |
|---|---|---|---|---|---|---|---|---|---|
| ref_cafe | 33 | 3 | none | 3 of 7 sections | 0 | none | 254 | 0 | 192 |
| ref_storeroom | 41 | 5 | none | 3 of 7 | 2 | none | 190 | 0 | 147 |
| ref_corridor | 22 | 254 | 246 (trade-offs, 2,210 words) | 7 of 8 | 1 | none | 231 | 0 | 189 |

- Without the runaway draft the first-pass errors are 3, 5 and 8 per report (33, 41 and 22 with the single-pass
  writer). The writer told the model the allowed length and sent back only the start of the runaway draft; the
  regeneration produced a clean trade-offs section.
- The remaining first-pass errors are `value_mismatch` (a number next to the tag of a neighbouring fact) and a few
  `untagged_number` / `unit_mismatch`. Every section that failed was regenerated once; 3 sentences were removed in
  total, no section fell back to the template text.
- In the first corridor report of this run, one regenerated section answered with the model's working notes
  instead of section prose; the fact check removed every sentence with a number, but sentences without numbers
  remained. The writer now uses the template text for any section answer more than twice its word budget or one
  the fact check had to cut by more than 3 sentences (unit-tested), and the corridor report above was written
  again with that rule, on Token Factory, from the same facts pack.
- Required blocks are rendered by code and present in all three reports: the robot comparison table (one row per
  verdict, conservative-optimistic success ranges), a rule-check table per robot under its code verdict,
  "How we investigated", the Pinch Map summary, the recommended changes table, payback, hazards and manual checks,
  methodology, scope and sources. The catalog review label reads "human-reviewed" for every robot in these runs.
- The fact checker no longer accepts a bare integer next to a ratio tag ("layout change 1 ... 78% [f]" matched the
  78% fact by rounding); such a number is now an error.

### Investigation loop (live Nemotron Super)

| site | robots investigated | experiments run | probe episodes | findings recorded |
|---|---|---|---|---|
| ref_cafe | 2 (Bear Servi, Keenon W3) | 2 | 60 | 2 (both confirmed by probe) |
| ref_storeroom | 2 (MiR100, MiR250) | 1 | 30 | 0 |
| ref_corridor | 0 (every robot met the success rule) | 0 | 0 | 0 |

- Cafe, Bear Servi: hypothesis "moving chair `CH4W` away from the top-ranked bottleneck widens the narrow passage";
  the probe moved `CH4W` (dx 0.5 m, dy 0.5 m): success 75 % -> 100 %, +25 points vs a probe CI half-width of
  5.7 points -> supported, finding recorded.
- Cafe, Keenon W3: same chair, moved dx 0.6 m, dy 0.5 m: success 70 % -> 100 %, +30 points vs 5.7 -> supported,
  finding recorded.
- Storeroom, MiR250: hypothesis "moving `PALLET1` away from the top pinch point opens the static no-path legs";
  the probe moved `PALLET1` (dx 0.5 m, dy -0.5 m): inconclusive. The MiR100 loop used its 8 steps on evidence tools
  without running a probe.

### Live model usage (from `docs/PROOF.md`)

All on Nebius Token Factory:

| tier | model | calls | failed | tokens in | tokens out | median latency | cost (list-price estimate) |
|---|---|---:|---:|---:|---:|---:|---:|
| Nemotron Super (candidate ordering, investigation) | `nvidia/Nemotron-3.5-Super-VL` | 39 | 0 | 140,729 | 17,633 | 2.5 s | 0.06 USD |
| Nemotron Ultra (report narrative sections) | `nvidia/Nemotron-3-Ultra-550b-a55b` | 51 | 0 | 131,392 | 129,173 | 6.9 s | 0.52 USD |
| total | | 90 | 0 | 272,121 | 146,806 | | 0.58 USD |

LLM calls by tier and site (every call carries its analysis id): cafe super 20, ultra 10; storeroom super 17,
ultra 10; corridor super 2, ultra 31 (two report passes, see Fact-check). No call failed and no stage fell back to
offline mode. Cost uses the per-token prices in `config/models.yaml`; the Token Factory billing page is the
authoritative figure. Three full reference checks cost well under 1 USD.

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
PYTHONPATH=. python scripts/release_proof.py --require-provider token_factory --fresh-usage --force
```

This archives the usage DB, runs the three checks in live mode, copies compact artifacts into `sample_sites/<site>/analyses/<id>/` (served by
services/api at startup) and writes the web app's offline-mode fixtures (`apps/web/src/offline/fixtures/`).
