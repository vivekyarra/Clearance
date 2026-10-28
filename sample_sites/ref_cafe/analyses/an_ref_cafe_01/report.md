# Clearance Report

Every number below links to where it came from: an on-site measurement, a cited page, your own inputs, a formula, or a simulation batch you can replay. Simulation results are simulation-based estimates, shown as ranges across scan uncertainty.

## Summary

The assessment evaluated the robots against the surveyed site geometry (scan median absolute error 0.1 cm [f_002], p90 1.4 cm [f_003]). Keenon Robotics DINERBOT T8 is Cleared with changes [f_095] because threshold D1 must be measured manually and, if within limits, the robot meets the required success rates (100% [f_066] conservative, 98% [f_077] optimistic). Bear Robotics Servi, Keenon Robotics W3, and Keenon Robotics DINERBOT T9 are Not cleared [f_185][f_278][f_370] as each requires manual measurement of threshold D1; their conservative success rates are 72% [f_156], 57% [f_243], and 40% [f_341] respectively.

The most useful next step for the site owner is to measure threshold D1 on site for each robot to confirm whether the DINERBOT T8 verdict holds and to determine if the other robots can meet the required performance.

## 1. Site evidence

- Geometry source: reference site (known ground-truth geometry) [f_005].
- Scan accuracy (reference geometry (known ground truth) [f_001]): median absolute error 0.1 cm [f_002], p90 absolute error 1.4 cm [f_003]. This error sets the conservative and optimistic geometry bounds used in every simulation.
- Floor area inside the scanned bounds: 48.0 m² [f_004].
- Threshold `D1`: 2.0 cm [f_010] high.
- Pedestrian traffic (user-estimated [f_006]): quiet 2 [f_007], normal 6 [f_008], peak 14 [f_009] persons per minute.

## 2. Verdicts

Verdicts are decided by code from fixed rules; the text only explains them.

### Keenon Robotics DINERBOT T8: Cleared with changes [f_095]

- Main reason: as-is: threshold D1 must be measured manually; cleared after measuring threshold D1 (holds only if it is within the robot's limit).
- Required: measure threshold `D1` on site.

Rule checks as the site is today:

| Rule check | Result |
|---|---|
| every task leg feasible on the conservative geometry bound | pending (measure on site) (value: 3 [f_097]/5 legs [f_098]) |
| no unresolved flagged_manual thresholds on the route | pending (measure on site) (value: D1) |
| simulated success at normal traffic (conservative bound) >= 0.90 [f_099] | met (value: 100% [f_100]) |

Rule checks with the recommended changes:

| Rule check | Result |
|---|---|
| every task leg feasible on the conservative geometry bound | met (value: 3 [f_102]/5 legs [f_103]) |
| no unresolved flagged_manual thresholds on the route | met (value: D1) |
| simulated success at normal traffic (conservative bound) >= 0.90 [f_104] | met (value: 100% [f_105]) |

**Why this verdict**

The Keenon Robotics DINERBOT T8 received Cleared with changes [f_095] because as-is the rule checks for task leg feasibility on the conservative geometry bound (3 [f_029] of 5 [f_028] legs) and threshold D1 were pending manual measurement on site. The simulated success rate at normal traffic with conservative geometry was 100% [f_100], meeting the 90% [f_101] threshold. After measuring D1 and confirming it within the robot's limit, all checks pass: 3 [f_102] of 5 [f_103] legs feasible, D1 resolved, and success rate 100% [f_105] still meets the 90% [f_106] threshold. The narrowest gap on route is 0.65 m [f_032] while the robot needs 0.38 m [f_033] at that point.

### Bear Robotics Servi: Not cleared [f_185]

- Main reason: threshold D1 must be measured manually.

Rule checks as the site is today:

| Rule check | Result |
|---|---|
| every task leg feasible on the conservative geometry bound | pending (measure on site) (value: 3 [f_187]/5 legs [f_188]) |
| no unresolved flagged_manual thresholds on the route | pending (measure on site) (value: D1) |
| simulated success at normal traffic (conservative bound) >= 0.90 [f_189] | not met (value: 72% [f_190]) |

**Why this verdict**

Bear Robotics Servi received Not cleared [f_185] because three rule checks failed: only 3 [f_187] of 5 [f_188] task legs are feasible on the conservative geometry bound (pending measure on site), an unresolved flagged_manual threshold D1 remains on the route (pending measure on site), and the simulated success rate at normal traffic under conservative geometry is 72% [f_190], below the required 90% [f_191] (rule threshold 0.9 [f_189]). Resolving the manual measurements and raising the success rate above the threshold would change the outcome.

### Keenon Robotics W3: Not cleared [f_278]

- Main reason: threshold D1 must be measured manually.

Rule checks as the site is today:

| Rule check | Result |
|---|---|
| every task leg feasible on the conservative geometry bound | pending (measure on site) (value: 3 [f_280]/5 legs [f_281]) |
| no unresolved flagged_manual thresholds on the route | pending (measure on site) (value: D1) |
| simulated success at normal traffic (conservative bound) >= 0.90 [f_282] | not met (value: 57% [f_283]) |

Rule checks with the recommended changes:

| Rule check | Result |
|---|---|
| every task leg feasible on the conservative geometry bound | pending (measure on site) (value: 3 [f_285]/5 legs [f_286]) |
| no unresolved flagged_manual thresholds on the route | pending (measure on site) (value: D1) |
| simulated success at normal traffic (conservative bound) >= 0.90 [f_287] | not met (value: 88% [f_288]) |

**Why this verdict**

Keenon Robotics W3 received Not cleared [f_278] because the rule checks failed: only 3 [f_280] of 5 [f_281] task legs are feasible on the conservative geometry bound (pending measure on site), threshold D1 remains unresolved (pending measure on site), and simulated success at normal traffic under conservative geometry is 57% [f_283], below the 90% [f_284] threshold. With the recommended layout change moving `CH4W` 5.0 cm [f_270] east, success rises to 88% [f_288] but still misses the 90% [f_289] threshold, and the pending checks remain. The chair CH4W encroaches on the top bottleneck; moving it raises success but not enough to clear.

### Keenon Robotics DINERBOT T9: Not cleared [f_370]

- Main reason: threshold D1 must be measured manually.

Rule checks as the site is today:

| Rule check | Result |
|---|---|
| every task leg feasible on the conservative geometry bound | pending (measure on site) (value: 3 [f_372]/5 legs [f_373]) |
| no unresolved flagged_manual thresholds on the route | pending (measure on site) (value: D1) |
| simulated success at normal traffic (conservative bound) >= 0.90 [f_374] | not met (value: 40% [f_375]) |

**Why this verdict**

Keenon Robotics DINERBOT T9 received Not cleared [f_370] because three as‑is rule checks failed: only 3 [f_372] of 5 [f_373] task legs are feasible on the conservative geometry bound (pending measure on site), the flagged manual threshold `D1` remains unresolved (pending measure on site), and the simulated success rate at normal traffic under conservative geometry is 40% [f_375], below the 90% [f_376] requirement. The narrowest gap on the route is 0.65 m [f_306] while the robot needs 0.50 m [f_307], so clearance is adequate. Clearing would require confirming all task legs, resolving `D1`, and achieving a success rate ≥90% [f_376] (e.g., via layout changes or reduced traffic).

## 3. How we investigated

For Bear Robotics Servi, the hypothesis was that moving chair CH4W away from the top bottleneck would widen the passage and raise the success rate. The experiment moved CH4W by 0.50 m [f_377] dx, 0.50 m [f_378] dy, 0° [f_379] dyaw. The success rate increased from 75% [f_i79f1d_031] to 100% [f_i79f1d_027] over 30 episodes [f_i79f1d_030], a 25% [f_i79f1d_032] gain exceeding the 6% [f_i79f1d_033] CI half-width. The pinch map showed 44% [f_i79f1d_011] of stop/near-miss time at the bottleneck, where the free gap was 0.66 m [f_i79f1d_017] against a 0.45 m [f_i79f1d_018] robot width. The hypothesis was supported.

For Keenon Robotics W3, the hypothesis was similar: moving CH4W would widen a narrow gap of 0.58 m [f_i7dc17_015] versus the 0.46 m [f_i7dc17_016] robot width. The experiment moved CH4W by 0.60 m [f_380] dx, 0.50 m [f_381] dy, 0° [f_382] dyaw. Success rose from 70% [f_i7dc17_027] to 100% [f_i7dc17_023] over 30 episodes [f_i7dc17_026], a 30% [f_i7dc17_028] gain exceeding the 6% [f_i7dc17_029] CI half-width. The pinch map indicated 39% [f_i7dc17_010] of stop/near-miss time at the bottleneck. The hypothesis was supported.

### Bear Robotics Servi

1. **Hypothesis:** Moving chair CH4W away from the top-ranked bottleneck cell in the seating zone will widen the narrow passage, reducing stops and near-misses and increasing success rate.
   - **Experiment:** moved `CH4W` (dx 0.50 m [f_377], dy 0.50 m [f_378], dyaw 0° [f_379]) (probe batch `sb_338d3604eb3e`).
   - **Result:** before: value: 75% [f_i79f1d_031]; after: value: 100% [f_i79f1d_027]; value: 89% [f_i79f1d_028]; value: 100% [f_i79f1d_029]; value: 30 [f_i79f1d_030]; value: 25% [f_i79f1d_032]; value: 6% [f_i79f1d_033].
   - **Conclusion:** recorded in the trail. Outcome: supported; concluded.

### Keenon Robotics W3

1. **Hypothesis:** Moving chair CH4W away from the top bottleneck widens the narrow gap (gap 0.5831m [f_i7dc17_015] vs robot width 0.459m [f_i7dc17_016]), reducing stops/near-misses and raising success rate (currently 0.75 [f_i7dc17_001]).
   - **Experiment:** moved `CH4W` (dx 0.60 m [f_380], dy 0.50 m [f_381], dyaw 0° [f_382]) (probe batch `sb_2fe17c062b68`).
   - **Result:** before: value: 70% [f_i7dc17_027]; after: value: 100% [f_i7dc17_023]; value: 89% [f_i7dc17_024]; value: 100% [f_i7dc17_025]; value: 30 [f_i7dc17_026]; value: 30% [f_i7dc17_028]; value: 6% [f_i7dc17_029].
   - **Conclusion:** Chair CH4W encroaches on the top bottleneck (39.1% of stop/near-miss time [f_i7dc17_010]), where the gap is only 0.5831m [f_i7dc17_015] vs the robot's 0.459m width [f_i7dc17_016]. Moving CH4W away from this point raised success from 0.7 [f_i7dc17_027] to 1.0 [f_i7dc17_023], an gain of 0.3 [f_i7dc17_028] exceeding the CI half-width of 0.0568 [f_i7dc17_029]. Outcome: supported; concluded.

## 4. Why it fails (investigation)

- Keenon Robotics W3: Chair CH4W encroaches on the top bottleneck (39.1% of stop/near-miss time [f_i7dc17_010]), where the gap is only 0.5831m [f_i7dc17_015] vs the robot's 0.459m width [f_i7dc17_016]. Moving CH4W away from this point raised success from 0.7 [f_i7dc17_027] to 1.0 [f_i7dc17_023], an gain of 0.3 [f_i7dc17_028] exceeding the CI half-width of 0.0568 [f_i7dc17_029]. Status: confirmed by a probe; objects involved: `CH4W`.

## 5. Robot comparison (simulation-based estimates)

| Robot | Verdict | Legs feasible | Narrowest gap (needed) | Success, quiet traffic | Success, normal traffic | Success, peak traffic | Median mission | Near-misses per mission | Catalog specs |
|---|---|---|---|---|---|---|---|---|---|
| Keenon Robotics DINERBOT T8 | Cleared with changes | 3 [f_029] of 5 [f_028] | 0.65 m [f_032] (0.38 m [f_033]) | 100% [f_054][f_064] | 98–100% [f_077][f_066] | 100% [f_079][f_089] | 104 s [f_072] | 1.90 [f_073] | human-reviewed [f_027] |
| Bear Robotics Servi | Not cleared | 3 [f_118] of 5 [f_117] | 0.65 m [f_121] (0.45 m [f_122]) | 75–95% [f_143][f_154] | 72–95% [f_156][f_167] | 72–88% [f_169][f_180] | 100 s [f_162] | 0.95 [f_163] | human-reviewed [f_116] |
| Keenon Robotics W3 | Not cleared | 3 [f_205] of 5 [f_204] | 0.65 m [f_208] (0.46 m [f_209]) | 57–75% [f_230][f_241] | 57–75% [f_243][f_254] | 57–75% [f_256][f_267] | 111 s [f_249] | 1.10 [f_250] | human-reviewed [f_203] |
| Keenon Robotics DINERBOT T9 | Not cleared | 3 [f_303] of 5 [f_302] | 0.65 m [f_306] (0.50 m [f_307]) | 40–57% [f_328][f_339] | 40–57% [f_341][f_352] | 40–57% [f_354][f_365] | 138 s [f_347] | 0.65 [f_348] | human-reviewed [f_301] |

Success ranges run from the conservative to the optimistic geometry bound. Mission time and near-misses are at the reference traffic level on the nominal geometry.

- Keenon Robotics DINERBOT T8, normal traffic, nominal geometry: 98% [f_068] (CI95 87–100% [f_070][f_071]) over 40 [f_069] episodes.
- Bear Robotics Servi, normal traffic, nominal geometry: 75% [f_158] (CI95 60–86% [f_160][f_161]) over 40 [f_159] episodes.
- Keenon Robotics W3, normal traffic, nominal geometry: 70% [f_245] (CI95 55–82% [f_247][f_248]) over 40 [f_246] episodes.
- Keenon Robotics DINERBOT T9, normal traffic, nominal geometry: 48% [f_343] (CI95 33–63% [f_345][f_346]) over 40 [f_344] episodes.

## 6. Pinch Map

The Pinch Map in the Clearance console shows where robots stop, wait or pass close to people. The tightest points and the main reasons for unsuccessful episodes:

- Keenon Robotics DINERBOT T8, leg DOCK to PICKUP: measure on site [f_034]; narrowest gap 0.65 m [f_035] vs 0.38 m [f_036] needed.
- Keenon Robotics DINERBOT T8, leg PICKUP to T1: measure on site [f_038]; narrowest gap 0.88 m [f_039] vs 0.38 m [f_040] needed.
- Keenon Robotics DINERBOT T8, normal traffic: collision in 1 [f_076] of 40 [f_069] episodes.
- Bear Robotics Servi, leg DOCK to PICKUP: measure on site [f_123]; narrowest gap 0.65 m [f_124] vs 0.45 m [f_125] needed.
- Bear Robotics Servi, leg PICKUP to T1: measure on site [f_127]; narrowest gap 0.88 m [f_128] vs 0.45 m [f_129] needed.
- Bear Robotics Servi, normal traffic: no path static in 10 [f_166] of 40 [f_159] episodes.
- Keenon Robotics W3, leg DOCK to PICKUP: measure on site [f_210]; narrowest gap 0.65 m [f_211] vs 0.46 m [f_212] needed.
- Keenon Robotics W3, leg PICKUP to T1: measure on site [f_214]; narrowest gap 0.88 m [f_215] vs 0.46 m [f_216] needed.
- Keenon Robotics W3, normal traffic: no path static in 12 [f_253] of 40 [f_246] episodes.
- Keenon Robotics DINERBOT T9, leg DOCK to PICKUP: measure on site [f_308]; narrowest gap 0.65 m [f_309] vs 0.50 m [f_310] needed.
- Keenon Robotics DINERBOT T9, leg PICKUP to T1: measure on site [f_312]; narrowest gap 0.88 m [f_313] vs 0.50 m [f_314] needed.
- Keenon Robotics DINERBOT T9, normal traffic: no path static in 21 [f_351] of 40 [f_344] episodes.

## 7. Recommended changes

| Robot | Change | Success before → after | Episodes |
|---|---|---|---|
| Keenon Robotics DINERBOT T8 | measure threshold `D1` on site before deciding | — | — |
| Keenon Robotics W3 | move `CH4W` 5.0 cm [f_270] east | 78% [f_272] → 96% [f_273] | 50 [f_274] |

Success before and after each change is at normal traffic on the nominal geometry.

## 8. Payback

Your inputs: staff cost $16/h [f_012]; staff hours per day on the task 5 [f_013]; operating days per week 6 [f_014]; operating days per month 26 [f_094]; maintenance per year as a share of price 10% [f_015].

| Robot | Price | Automatable share | Monthly saving | Running costs | Net monthly | Payback |
|---|---|---|---|---|---|---|
| Keenon Robotics DINERBOT T8 | price on request [f_026] | 58% [f_091] | $1,217/month [f_092] | — | — | enter a quote [f_093] |
| Bear Robotics Servi | price on request [f_115] | 45% [f_182] | $936/month [f_183] | — | — | enter a quote [f_184] |
| Keenon Robotics W3 | price on request [f_202] | 42% [f_275] | $874/month [f_276] | — | — | enter a quote [f_277] |
| Keenon Robotics DINERBOT T9 | price on request [f_300] | 28% [f_367] | $593/month [f_368] | — | — | enter a quote [f_369] |

Formula: `automatable share = success rate (normal traffic, nominal geometry) x feasible leg fraction`; `monthly saving = staff cost x task hours x automatable share x days per month`; `payback = price / (monthly saving - monthly running costs)`.

## 9. Trade-offs between the robots

- Keenon DINERBOT T8: 100% [f_066] conservative, 98% [f_077] optimistic success, median mission 104 s [f_072], near-misses 1.90 [f_073], verdict Cleared with changes [f_095], price on request [f_026], payback enter a quote [f_093], narrowest gap 0.65 m [f_032].
- Bear Servi: 72% [f_156] conservative, 95% [f_167] optimistic success, median mission 100 s [f_162], near-misses 0.95 [f_163], verdict Not cleared [f_185], price on request [f_115], payback enter a quote [f_184], narrowest gap 0.65 m [f_121].
- Keenon W3: 57% [f_243] conservative, 75% [f_254] optimistic success, median mission 111 s [f_249], near-misses 1.10 [f_250], verdict Not cleared [f_278]; a layout change raises success from 78% [f_272] to 96% [f_273], price on request [f_202], payback enter a quote [f_277], narrowest gap 0.65 m [f_208].
- Keenon DINERBOT T9: 40% [f_341] conservative, 57% [f_352] optimistic success, median mission 138 s [f_347], near-misses 0.65 [f_348], verdict Not cleared [f_370], price on request [f_300], payback enter a quote [f_369], narrowest gap 0.65 m [f_306].

## 10. Hazards and manual checks

- Keenon Robotics DINERBOT T8 price: price on request [f_026]; enter a quote to see payback.
- Bear Robotics Servi price: price on request [f_115]; enter a quote to see payback.
- Keenon Robotics W3 price: price on request [f_202]; enter a quote to see payback.
- Keenon Robotics DINERBOT T9 price: price on request [f_300]; enter a quote to see payback.
- Traffic is user-estimated [f_006]; a fixed observer phone at peak time would replace the estimate.

## 11. Methodology

- The site was scanned with phones and turned into a measured twin; robot footprints come from cited vendor specs.
- Every robot ran a footprint-accurate 2D navigation simulation on the nominal, conservative (obstacles grown by the scan error) and optimistic (obstacles shrunk by it) geometry bounds, using the p90 scan error of 1.4 cm [f_003].
- Pedestrians follow the site traffic model at quiet, normal and peak levels; robots stop for people inside their safety distance and replan when blocked.
- Verdicts come from code rules: Cleared means every leg is feasible on the conservative bound, no threshold is pending, and conservative success at normal traffic meets the rule; Cleared with changes means the same rule holds after the recommended changes or on-site measurements; everything else is Not cleared.
- Tables, verdicts and rule checks are written by code from the facts pack. Nemotron runs the investigation experiments and writes the summary, the explanations and the trade-off discussion; each of those sections is fact-checked on its own against the facts pack before the report is shown.

## 12. Scope

- Results are simulation-based estimates for wheeled service robots and indoor AMRs on flat floors; real deployments also depend on staff routines, Wi-Fi and vendor setup.
- Stairs, elevators, outdoor routes and manipulation tasks are outside this report.
- Thresholds close to the scan noise are marked for on-site measurement rather than guessed.
- Vendor specs are used as published and cited; prices appear only when public or entered by you.

## 13. Sources

- [Optimized Compact Design](https://www.keenon.com/en/product/T8), retrieved 2026-10-27; used for Keenon Robotics DINERBOT T8: footprint_polygon_m, Keenon Robotics DINERBOT T8: height_m, Keenon Robotics DINERBOT T8: max_slope_deg, Keenon Robotics DINERBOT T8: max_speed_mps, Keenon Robotics DINERBOT T8: min_aisle_width_vendor_m, Keenon Robotics DINERBOT T8: payload_kg
- [https://www.bearrobotics.ai/servi](https://www.bearrobotics.ai/servi), retrieved 2026-10-27; used for Bear Robotics Servi: footprint_polygon_m, Bear Robotics Servi: height_m, Bear Robotics Servi: max_speed_mps, Bear Robotics Servi: payload_kg, Bear Robotics Servi: runtime_h
- [https://www.bearrobotics.ai/faq](https://www.bearrobotics.ai/faq), retrieved 2026-10-27; used for Bear Robotics Servi: price
- [https://www.keenon.com/en/product/W3](https://www.keenon.com/en/product/W3), retrieved 2026-10-27; used for Keenon Robotics W3: footprint_polygon_m, Keenon Robotics W3: height_m, Keenon Robotics W3: max_slope_deg, Keenon Robotics W3: max_speed_mps, Keenon Robotics W3: min_aisle_width_vendor_m, Keenon Robotics W3: payload_kg
- [Keenon W3 Delivery Robot — Price, Specs & Demo · RobotLAB](https://www.robotlab.com/store/keenon-w3), retrieved 2026-10-27; used for Keenon Robotics W3: runtime_h, market check: Keenon W3 threshold climbing capability mm specification
- [https://www.keenon.com/en/product/T9](https://www.keenon.com/en/product/T9), retrieved 2026-10-27; used for Keenon Robotics DINERBOT T9: footprint_polygon_m, Keenon Robotics DINERBOT T9: height_m, Keenon Robotics DINERBOT T9: max_slope_deg, Keenon Robotics DINERBOT T9: max_speed_mps, Keenon Robotics DINERBOT T9: min_aisle_width_vendor_m, Keenon Robotics DINERBOT T9: payload_kg
- [Keenon Dinerbot T8 - Keenon Robots](https://sica-robotics.nl/featured_item/keenon-dinerbot-t8), retrieved 2026-10-28; used for market check: Keenon Dinerbot T8 threshold climbing capability mm specification
- [KEENON DINERBOT T8 Review (2026): Specs, Pros & Cons](https://antonrobots.com/keenon-dinerbot-t8-review), retrieved 2026-10-28; used for market check: Keenon Dinerbot T8 threshold climbing capability mm specification
- [Keenon T8 Delivery Robot — Specs & Alternatives · RobotLAB](https://www.robotlab.com/store/keenon-dinerbot-t8), retrieved 2026-10-28; used for market check: Keenon Dinerbot T8 threshold climbing capability mm specification
- [KEENON Dinerbot T8 – service robots in OKMbot lease or buy](https://okmbot.de/en/products/keenon-dinerbot-t8), retrieved 2026-10-28; used for market check: Keenon Dinerbot T8 threshold climbing capability mm specification
- [Bear Robotics Servi Plus Review 2026: Price & Specs](https://antonrobots.com/bear-robotics-servi-plus-review), retrieved 2026-10-28; used for market check: Bear Robotics Servi threshold climbing capability mm specification
- [Servi Plus: Bear Robotics' Flagship AI Service Robot for Hospitality and Beyond — Bear Robotics](https://www.bearrobotics.ai/servi-plus), retrieved 2026-10-28; used for market check: Bear Robotics Servi threshold climbing capability mm specification
- [Servi Q: The Compact Service Robot That Goes Where Others Can’t — Bear Robotics](https://www.bearrobotics.ai/blog/bear-robotics-introduces-servi-q), retrieved 2026-10-28; used for market check: Bear Robotics Servi threshold climbing capability mm specification
- [Bear Robotics Robots — Prices, Specs & Service · RobotLAB](https://www.robotlab.com/manufacturers/bear-robotics), retrieved 2026-10-28; used for market check: Bear Robotics Servi threshold climbing capability mm specification
- [User Manual](https://static1.squarespace.com/static/652cbb3fb1f91809d4610dc0/t/6683574fd99cec6481bda2c9/1719883618543/Servi+Plus_User+Manual_EN.pdf), retrieved 2026-10-28; used for market check: Bear Robotics Servi threshold climbing capability mm specification
- [KEENON BUTLERBOT W3 Review (2026): Specs, Price & Uses](https://antonrobots.com/keenon-butlerbot-w3-review), retrieved 2026-10-28; used for market check: Keenon W3 threshold climbing capability mm specification
- [Keenon Butlerbot W3 room service robot | SomeBots](https://somebots.com/en/robots/room-service/keenon-W3), retrieved 2026-10-28; used for market check: Keenon W3 threshold climbing capability mm specification
- [KEENON Butlerbot W3 – Contactless delivery robots OKMbot](https://okmbot.de/en/products/keenon-butlerbot-w3-silber), retrieved 2026-10-28; used for market check: Keenon W3 threshold climbing capability mm specification
- [Cross-floor Delivery Robot W3 Product Introduction](https://www.youtube.com/watch?v=5DbdbLRaYbA), retrieved 2026-10-28; used for market check: Keenon W3 threshold climbing capability mm specification
- [KEENON Dinerbot T9 – delivery to rent a robot for OKMbot or buy](https://okmbot.de/en/products/keenon-dinerbot-t9), retrieved 2026-10-28; used for market check: Keenon Dinerbot T9 threshold climbing capability mm specification
- [DINERBOT T9｜Deliveries with High Load Capacity](https://www.youtube.com/watch?v=5TP6MokvFnQ), retrieved 2026-10-28; used for market check: Keenon Dinerbot T9 threshold climbing capability mm specification
- [Keenon DinerBot T9 Delivery Robot | Knoxlabs](https://www.knoxlabs.com/products/keenon-dinerbot-t9-delivery-robot), retrieved 2026-10-28; used for market check: Keenon Dinerbot T9 threshold climbing capability mm specification
- [KEENON Robot Price Guide 2026: The Same T10 Is Listed at $13,500 and $20,580 | GrabaRobot](https://www.grabarobot.com/blog/keenon-robotics-price-guide-2026), retrieved 2026-10-28; used for market check: Keenon Dinerbot T9 threshold climbing capability mm specification
