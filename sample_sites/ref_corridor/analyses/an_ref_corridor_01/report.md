# Clearance Report

Every number below links to where it came from: an on-site measurement, a cited page, your own inputs, a formula, or a simulation batch you can replay. Simulation results are simulation-based estimates, shown as ranges across scan uncertainty.

## Summary

The clearance check evaluated the Keenon Robotics W3, Bear Robotics Servi, Keenon Robotics DINERBOT T9, Keenon Robotics DINERBOT T8, Pudu Robotics KettyBot and Pudu Robotics T300 against the surveyed site geometry (median absolute error 0.0 cm [f_002], p90 absolute error 0.0 cm [f_003]) and simulation‑based success rates. Keenon Robotics W3 – Cleared [f_365]; Bear Robotics Servi – Cleared [f_187]; Keenon Robotics DINERBOT T9 – Cleared [f_455]; Keenon Robotics DINERBOT T8 – Cleared [f_099]; Pudu Robotics KettyBot – Cleared [f_278]; Pudu Robotics T300 – Cleared [f_545].

All robots meet every rule as‑is, so each receives a Cleared verdict. The most useful next step for the site owner is to request pricing and schedule an on‑site validation.

## 1. Site evidence

- Geometry source: reference site (known ground-truth geometry) [f_005].
- Scan accuracy (reference geometry (known ground truth) [f_001]): median absolute error 0.0 cm [f_002], p90 absolute error 0.0 cm [f_003]. This error sets the conservative and optimistic geometry bounds used in every simulation.
- Floor area inside the scanned bounds: 28.8 m² [f_004].
- Pedestrian traffic (user-estimated [f_006]): quiet 2 [f_007], normal 6 [f_008], peak 14 [f_009] persons per minute.

## 2. Verdicts

Verdicts are decided by code from fixed rules; the text only explains them.

### Keenon Robotics W3: Cleared [f_365]

- Main reason: meets every rule as-is.

Rule checks as the site is today:

| Rule check | Result |
|---|---|
| every task leg feasible on the conservative geometry bound | met (value: 6 [f_367]/6 legs [f_368]) |
| no unresolved flagged_manual thresholds on the route | met (value: none) |
| simulated success at normal traffic (conservative bound) >= 0.90 [f_369] | met (value: 100% [f_370]) |

**Why this verdict**

Keenon Robotics W3 received Cleared [f_365] because all as‑is rule checks passed: every one of the 6 [f_367] task legs is feasible on the conservative geometry bound (6 [f_368] legs total), no unresolved flagged‑manual thresholds exist on the route, and the simulated success rate at normal traffic with conservative geometry is 100% [f_370], exceeding the 90% [f_371] threshold (rule requires ≥0.9 [f_369]). The narrowest gap on the route is 1.15 m [f_300] while the robot needs only 0.46 m [f_301], so geometry is not limiting. A drop of the simulated success rate below 90% [f_371] or a leg becoming infeasible would change the verdict.

### Bear Robotics Servi: Cleared [f_187]

- Main reason: meets every rule as-is.

Rule checks as the site is today:

| Rule check | Result |
|---|---|
| every task leg feasible on the conservative geometry bound | met (value: 6 [f_189]/6 legs [f_190]) |
| no unresolved flagged_manual thresholds on the route | met (value: none) |
| simulated success at normal traffic (conservative bound) >= 0.90 [f_191] | met (value: 95% [f_192]) |

**Why this verdict**

Bear Robotics Servi received a Cleared [f_187] verdict because all rule checks passed as‑is: every task leg is feasible on the conservative geometry bound (6 [f_189] of 6 [f_190] legs), no unresolved flagged_manual thresholds exist on the route, and the simulated success rate at normal traffic under the conservative bound is 95% [f_192], exceeding the 0.9 [f_191] (90% [f_193]) threshold. A lower success rate or a tighter gap would change the outcome.

### Keenon Robotics DINERBOT T9: Cleared [f_455]

- Main reason: meets every rule as-is.

Rule checks as the site is today:

| Rule check | Result |
|---|---|
| every task leg feasible on the conservative geometry bound | met (value: 6 [f_457]/6 legs [f_458]) |
| no unresolved flagged_manual thresholds on the route | met (value: none) |
| simulated success at normal traffic (conservative bound) >= 0.90 [f_459] | met (value: 95% [f_460]) |

**Why this verdict**

The Keenon Robotics DINERBOT T9 earned a Cleared [f_455] verdict by meeting all as‑is rule checks: every task leg is feasible on the conservative geometry bound (6 [f_385] of 6 [f_384] legs), no unresolved flagged_manual thresholds exist, and the simulated success rate at normal traffic under conservative geometry meets the 0.9 [f_459] threshold with a value of 95% [f_460] (above the 90% [f_461] requirement). A success rate below 90% [f_461] or any infeasible leg would change the outcome.

### Keenon Robotics DINERBOT T8: Cleared [f_099]

- Main reason: meets every rule as-is.

Rule checks as the site is today:

| Rule check | Result |
|---|---|
| every task leg feasible on the conservative geometry bound | met (value: 6 [f_101]/6 legs [f_102]) |
| no unresolved flagged_manual thresholds on the route | met (value: none) |
| simulated success at normal traffic (conservative bound) >= 0.90 [f_103] | met (value: 92% [f_104]) |

**Why this verdict**

The DINERBOT T8 received Cleared [f_099] because all as‑is rule checks passed: every one of the 6 [f_101] task legs is feasible on the conservative geometry bound (6 [f_102]), there are no unresolved flagged‑manual thresholds, and the simulated success rate at normal traffic under the conservative bound is 92% [f_104], exceeding the 90% [f_105] threshold (rule value 0.9 [f_103]). The narrowest gap on the route is 1.15 m [f_030] while the robot requires 0.38 m [f_031] at that point. Under normal traffic the simulation recorded 3.80 [f_075] near‑misses per mission and 3 [f_078] collisions in 40 [f_071] episodes; a drop of the success rate below 90% [f_105] or an infeasible leg would change the verdict.

### Pudu Robotics KettyBot: Cleared [f_278]

- Main reason: meets every rule as-is.

Rule checks as the site is today:

| Rule check | Result |
|---|---|
| every task leg feasible on the conservative geometry bound | met (value: 6 [f_280]/6 legs [f_281]) |
| no unresolved flagged_manual thresholds on the route | met (value: none) |
| simulated success at normal traffic (conservative bound) >= 0.90 [f_282] | met (value: 92% [f_283]) |

**Why this verdict**

Pudu Robotics KettyBot received Cleared [f_278] because it meets every rule as‑is: all 6 [f_280] of 6 [f_281] task legs are feasible on the conservative geometry bound, no unresolved flagged_manual thresholds exist on the route, and the simulated success rate at normal traffic under the conservative bound is 92% [f_283], exceeding the 0.9 [f_282] (90% [f_284]) threshold. The narrowest gap on the route is 1.15 m [f_210] while the robot requires only 0.45 m [f_211], so clearance is adequate. In 40 [f_251] simulated episodes the robot suffered 3 [f_258] collisions. The verdict would change if the success rate fell below 90% [f_284], a leg became infeasible, or a flagged threshold appeared.

### Pudu Robotics T300: Cleared [f_545]

- Main reason: meets every rule as-is.

Rule checks as the site is today:

| Rule check | Result |
|---|---|
| every task leg feasible on the conservative geometry bound | met (value: 6 [f_547]/6 legs [f_548]) |
| no unresolved flagged_manual thresholds on the route | met (value: none) |
| simulated success at normal traffic (conservative bound) >= 0.90 [f_549] | met (value: 92% [f_550]) |

**Why this verdict**

The Pudu Robotics T300 received a Cleared verdict because it satisfies all as‑is rule checks: every one of the 6 [f_547] task legs is feasible on the conservative geometry bound (6 legs [f_548]), there are no unresolved flagged‑manual thresholds on the route, and the simulated success rate at normal traffic under the conservative bound is 92% [f_550], exceeding the 90% [f_549] threshold. The narrowest gap on the route is 0.90 m [f_477] while the robot requires 0.50 m [f_478]; median mission time is 75 s [f_521] with 4.03 near‑misses per mission [f_522] over 40 simulated episodes [f_518]. A drop of the success rate below 90% [f_551] or an infeasible leg would change the verdict.

## 3. How we investigated

No investigation experiments were recorded for this analysis.

## 4. Why it fails (investigation)

No findings were recorded; the comparison and Pinch Map below show where time is lost.

## 5. Robot comparison (simulation-based estimates)

| Robot | Verdict | Legs feasible | Narrowest gap (needed) | Success, quiet traffic | Success, normal traffic | Success, peak traffic | Median mission | Near-misses per mission | Catalog specs |
|---|---|---|---|---|---|---|---|---|---|
| Keenon Robotics W3 | Cleared | 6 [f_298] of 6 [f_297] | 1.15 m [f_300] (0.46 m [f_301]) | 100% [f_326][f_336] | 100% [f_338][f_348] | 100% [f_350][f_360] | 79 s [f_344] | 4.70 [f_345] | human-reviewed [f_296] |
| Bear Robotics Servi | Cleared | 6 [f_117] of 6 [f_116] | 1.15 m [f_119] (0.45 m [f_120]) | 100% [f_145][f_155] | 95% [f_157][f_168] | 92% [f_170][f_182] | 66 s [f_163] | 3.45 [f_164] | human-reviewed [f_115] |
| Keenon Robotics DINERBOT T9 | Cleared | 6 [f_385] of 6 [f_384] | 1.15 m [f_387] (0.50 m [f_388]) | 100% [f_413][f_423] | 95% [f_425][f_436] | 92% [f_438][f_450] | 75 s [f_431] | 4.22 [f_432] | human-reviewed [f_383] |
| Keenon Robotics DINERBOT T8 | Cleared | 6 [f_028] of 6 [f_027] | 1.15 m [f_030] (0.38 m [f_031]) | 100% [f_056][f_066] | 92% [f_068][f_079] | 92% [f_081][f_093] | 66 s [f_074] | 3.80 [f_075] | human-reviewed [f_026] |
| Pudu Robotics KettyBot | Cleared | 6 [f_208] of 6 [f_207] | 1.15 m [f_210] (0.45 m [f_211]) | 100% [f_236][f_246] | 92% [f_248][f_259] | 90% [f_261][f_273] | 68 s [f_254] | 3.45 [f_255] | human-reviewed [f_206] |
| Pudu Robotics T300 | Cleared | 6 [f_475] of 6 [f_474] | 0.90 m [f_477] (0.50 m [f_478]) | 100% [f_503][f_513] | 92% [f_515][f_526] | 90% [f_528][f_540] | 75 s [f_521] | 4.03 [f_522] | human-reviewed [f_473] |

Success ranges run from the conservative to the optimistic geometry bound. Mission time and near-misses are at the reference traffic level on the nominal geometry.

- Keenon Robotics W3, normal traffic, nominal geometry: 100% [f_340] (CI95 91–100% [f_342][f_343]) over 40 [f_341] episodes.
- Bear Robotics Servi, normal traffic, nominal geometry: 95% [f_159] (CI95 83–99% [f_161][f_162]) over 40 [f_160] episodes.
- Keenon Robotics DINERBOT T9, normal traffic, nominal geometry: 95% [f_427] (CI95 83–99% [f_429][f_430]) over 40 [f_428] episodes.
- Keenon Robotics DINERBOT T8, normal traffic, nominal geometry: 92% [f_070] (CI95 80–97% [f_072][f_073]) over 40 [f_071] episodes.
- Pudu Robotics KettyBot, normal traffic, nominal geometry: 92% [f_250] (CI95 80–97% [f_252][f_253]) over 40 [f_251] episodes.
- Pudu Robotics T300, normal traffic, nominal geometry: 92% [f_517] (CI95 80–97% [f_519][f_520]) over 40 [f_518] episodes.

## 6. Pinch Map

The Pinch Map in the Clearance console shows where robots stop, wait or pass close to people. The tightest points and the main reasons for unsuccessful episodes:

- Bear Robotics Servi, normal traffic: collision in 2 [f_167] of 40 [f_160] episodes.
- Bear Robotics Servi: success falls from 100% [f_147] at quiet traffic to 92% [f_172] at peak traffic (nominal geometry).
- Keenon Robotics DINERBOT T9, normal traffic: collision in 2 [f_435] of 40 [f_428] episodes.
- Keenon Robotics DINERBOT T9: success falls from 100% [f_415] at quiet traffic to 92% [f_440] at peak traffic (nominal geometry).
- Keenon Robotics DINERBOT T8, normal traffic: collision in 3 [f_078] of 40 [f_071] episodes.
- Keenon Robotics DINERBOT T8: success falls from 100% [f_058] at quiet traffic to 92% [f_083] at peak traffic (nominal geometry).
- Pudu Robotics KettyBot, normal traffic: collision in 3 [f_258] of 40 [f_251] episodes.
- Pudu Robotics KettyBot: success falls from 100% [f_238] at quiet traffic to 90% [f_263] at peak traffic (nominal geometry).
- Pudu Robotics T300, normal traffic: collision in 3 [f_525] of 40 [f_518] episodes.
- Pudu Robotics T300: success falls from 100% [f_505] at quiet traffic to 90% [f_530] at peak traffic (nominal geometry).

## 7. Recommended changes

No layout changes were needed or found.

## 8. Payback

Your inputs: staff cost $20/h [f_011]; staff hours per day on the task 2 [f_012]; operating days per week 5 [f_013]; operating days per month 21.666667 [f_098]; maintenance per year as a share of price 10% [f_014].

| Robot | Price | Automatable share | Monthly saving | Running costs | Net monthly | Payback |
|---|---|---|---|---|---|---|
| Keenon Robotics W3 | price on request [f_295] | 100% [f_362] | $867/month [f_363] | — | — | enter a quote [f_364] |
| Bear Robotics Servi | price on request [f_114] | 95% [f_184] | $823/month [f_185] | — | — | enter a quote [f_186] |
| Keenon Robotics DINERBOT T9 | price on request [f_382] | 95% [f_452] | $823/month [f_453] | — | — | enter a quote [f_454] |
| Keenon Robotics DINERBOT T8 | price on request [f_025] | 92% [f_095] | $802/month [f_096] | — | — | enter a quote [f_097] |
| Pudu Robotics KettyBot | price on request [f_205] | 92% [f_275] | $802/month [f_276] | — | — | enter a quote [f_277] |
| Pudu Robotics T300 | price on request [f_472] | 92% [f_542] | $802/month [f_543] | — | — | enter a quote [f_544] |

Formula: `automatable share = success rate (normal traffic, nominal geometry) x feasible leg fraction`; `monthly saving = staff cost x task hours x automatable share x days per month`; `payback = price / (monthly saving - monthly running costs)`.

## 9. Trade-offs between the robots

Keenon Robotics W3 achieves 100% [f_338] success with a median mission time of 79 s [f_344] and 4.70 [f_345] near‑misses; Bear Robotics Servi shows 95% [f_157] success, 66 s [f_163] median time, and 3.45 [f_164] near‑misses; Keenon Robotics DINERBOT T9 matches 95% [f_425] success, 75 s [f_431] median time, and 4.22 [f_432] near‑misses; Keenon Robotics DINERBOT T8 offers 92% [f_068] success, 66 s [f_074] median time, and 3.80 [f_075] near‑misses; Pudu Robotics KettyBot reaches 92% [f_248] success, 68 s [f_254] median time, and 3.45 [f_255] near‑misses; Pudu Robotics T300 provides 92% [f_515] success, 75 s [f_521] median time, and 4.03 [f_522] near‑misses. The narrowest gap required is 1.15 m [f_300] for W3, 1.15 m [f_119] for Servi, 1.15 m [f_387] for T9, 1.15 m [f_030] for T8, 1.15 m [f_210] for KettyBot, and 0.90 m [f_477] for T300. All units are priced on request and payback status is enter a quote.

## 10. Hazards and manual checks

- Keenon Robotics W3 price: price on request [f_295]; enter a quote to see payback.
- Bear Robotics Servi price: price on request [f_114]; enter a quote to see payback.
- Keenon Robotics DINERBOT T9 price: price on request [f_382]; enter a quote to see payback.
- Keenon Robotics DINERBOT T8 price: price on request [f_025]; enter a quote to see payback.
- Pudu Robotics KettyBot price: price on request [f_205]; enter a quote to see payback.
- Pudu Robotics T300 price: price on request [f_472]; enter a quote to see payback.
- Traffic is user-estimated [f_006]; a fixed observer phone at peak time would replace the estimate.

## 11. Methodology

- The site was scanned with phones and turned into a measured twin; robot footprints come from cited vendor specs.
- Every robot ran a footprint-accurate 2D navigation simulation on the nominal, conservative (obstacles grown by the scan error) and optimistic (obstacles shrunk by it) geometry bounds, using the p90 scan error of 0.0 cm [f_003].
- Pedestrians follow the site traffic model at quiet, normal and peak levels; robots stop for people inside their safety distance and replan when blocked.
- Verdicts come from code rules: Cleared means every leg is feasible on the conservative bound, no threshold is pending, and conservative success at normal traffic meets the rule; Cleared with changes means the same rule holds after the recommended changes or on-site measurements; everything else is Not cleared.
- Tables, verdicts and rule checks are written by code from the facts pack. Nemotron runs the investigation experiments and writes the summary, the explanations and the trade-off discussion; each of those sections is fact-checked on its own against the facts pack before the report is shown.

## 12. Scope

- Results are simulation-based estimates for wheeled service robots and indoor AMRs on flat floors; real deployments also depend on staff routines, Wi-Fi and vendor setup.
- Stairs, elevators, outdoor routes and manipulation tasks are outside this report.
- Thresholds close to the scan noise are marked for on-site measurement rather than guessed.
- Vendor specs are used as published and cited; prices appear only when public or entered by you.

## 13. Sources

- [https://www.keenon.com/en/product/T8](https://www.keenon.com/en/product/T8), retrieved 2026-10-27; used for Keenon Robotics DINERBOT T8: footprint_polygon_m, Keenon Robotics DINERBOT T8: height_m, Keenon Robotics DINERBOT T8: max_slope_deg, Keenon Robotics DINERBOT T8: max_speed_mps, Keenon Robotics DINERBOT T8: min_aisle_width_vendor_m, Keenon Robotics DINERBOT T8: payload_kg
- [https://www.bearrobotics.ai/servi](https://www.bearrobotics.ai/servi), retrieved 2026-10-27; used for Bear Robotics Servi: footprint_polygon_m, Bear Robotics Servi: height_m, Bear Robotics Servi: max_speed_mps, Bear Robotics Servi: payload_kg, Bear Robotics Servi: runtime_h
- [https://www.bearrobotics.ai/faq](https://www.bearrobotics.ai/faq), retrieved 2026-10-27; used for Bear Robotics Servi: price
- [https://6083570.fs1.hubspotusercontent-na1.net/hubfs/6083570/2022%20Robotics/Kettybot%20User%20Manual.pdf](https://6083570.fs1.hubspotusercontent-na1.net/hubfs/6083570/2022%20Robotics/Kettybot%20User%20Manual.pdf), retrieved 2026-10-27; used for Pudu Robotics KettyBot: footprint_polygon_m, Pudu Robotics KettyBot: height_m, Pudu Robotics KettyBot: max_slope_deg, Pudu Robotics KettyBot: max_speed_mps, Pudu Robotics KettyBot: max_threshold_m, Pudu Robotics KettyBot: min_aisle_width_vendor_m
- [https://robotsourced.com/robots/service/pudu-kettybot](https://robotsourced.com/robots/service/pudu-kettybot), retrieved 2026-10-27; used for Pudu Robotics KettyBot: payload_kg
- [https://www.keenon.com/en/product/W3](https://www.keenon.com/en/product/W3), retrieved 2026-10-27; used for Keenon Robotics W3: footprint_polygon_m, Keenon Robotics W3: height_m, Keenon Robotics W3: max_slope_deg, Keenon Robotics W3: max_speed_mps, Keenon Robotics W3: min_aisle_width_vendor_m, Keenon Robotics W3: payload_kg
- [https://www.robotlab.com/store/keenon-w3](https://www.robotlab.com/store/keenon-w3), retrieved 2026-10-27; used for Keenon Robotics W3: runtime_h
- [https://www.keenon.com/en/product/T9](https://www.keenon.com/en/product/T9), retrieved 2026-10-27; used for Keenon Robotics DINERBOT T9: footprint_polygon_m, Keenon Robotics DINERBOT T9: height_m, Keenon Robotics DINERBOT T9: max_slope_deg, Keenon Robotics DINERBOT T9: max_speed_mps, Keenon Robotics DINERBOT T9: min_aisle_width_vendor_m, Keenon Robotics DINERBOT T9: payload_kg
- [https://www.pudurobotics.com/en/products/pudut300](https://www.pudurobotics.com/en/products/pudut300), retrieved 2026-10-27; used for Pudu Robotics T300: footprint_polygon_m, Pudu Robotics T300: height_m, Pudu Robotics T300: max_speed_mps, Pudu Robotics T300: max_threshold_m, Pudu Robotics T300: min_aisle_width_vendor_m, Pudu Robotics T300: payload_kg
