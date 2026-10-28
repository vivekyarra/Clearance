# Clearance Report

Every number below links to where it came from: an on-site measurement, a cited page, your own inputs, a formula, or a simulation batch you can replay. Simulation results are simulation-based estimates, shown as ranges across scan uncertainty.

## Summary

The clearance check evaluated the robots against the site scan (median absolute error 0.0 cm [f_002], p90 0.0 cm [f_003]) and simulation‑based success‑rate thresholds. OTTO Motors OTTO 100 is Cleared [f_281] and Pudu Robotics T300 is Cleared [f_097] because each meets every rule as‑is. MiR MiR100 is Not cleared [f_373] due to a simulation‑based success rate of 88% [f_375] at normal traffic, below the required 90% [f_376]. MiR MiR250 is Not cleared [f_190] for the same reason, with a success rate of 88% [f_192] versus the 90% [f_193] threshold.

The most useful next step is to confirm the narrowest gap dimensions on site and, if they cannot be widened, select a robot that meets the required threshold.

## 1. Site evidence

- Geometry source: reference site (known ground-truth geometry) [f_005].
- Scan accuracy (reference geometry (known ground truth) [f_001]): median absolute error 0.0 cm [f_002], p90 absolute error 0.0 cm [f_003]. This error sets the conservative and optimistic geometry bounds used in every simulation.
- Floor area inside the scanned bounds: 80.0 m² [f_004].
- Pedestrian traffic (user-estimated [f_006]): quiet 2 [f_007], normal 6 [f_008], peak 14 [f_009] persons per minute.

## 2. Verdicts

Verdicts are decided by code from fixed rules; the text only explains them.

### OTTO Motors OTTO 100: Cleared [f_281]

- Main reason: meets every rule as-is.

Rule checks as the site is today:

| Rule check | Result |
|---|---|
| every task leg feasible on the conservative geometry bound | met (value: 6 [f_283]/6 legs [f_284]) |
| no unresolved flagged_manual thresholds on the route | met (value: none) |
| simulated success at normal traffic (conservative bound) >= 0.90 [f_285] | met (value: 98% [f_286]) |

**Why this verdict**

The OTTO Motors OTTO 100 received a Cleared [f_281] verdict because it meets all as‑is rule checks: 6 [f_283] of 6 [f_284] task legs are feasible on the conservative geometry bound, no unresolved flagged‑manual thresholds exist on the route, and the simulated success rate at normal traffic under conservative geometry is 98% [f_286], exceeding the 90% [f_287] threshold. The narrowest gap on the route is 0.78 m [f_214] while the robot requires 0.55 m [f_215], leaving margin. Under normal traffic the median mission time is 127 s [f_259] with 2.15 [f_260] near‑misses per mission. The outcome would change if the success rate fell below 90% [f_287], a task leg became infeasible, or flagged‑manual thresholds appeared.

### Pudu Robotics T300: Cleared [f_097]

- Main reason: meets every rule as-is.

Rule checks as the site is today:

| Rule check | Result |
|---|---|
| every task leg feasible on the conservative geometry bound | met (value: 6 [f_099]/6 legs [f_100]) |
| no unresolved flagged_manual thresholds on the route | met (value: none) |
| simulated success at normal traffic (conservative bound) >= 0.90 [f_101] | met (value: 98% [f_102]) |

**Why this verdict**

The Pudu Robotics T300 received Cleared [f_097] because all rule checks passed: 6 [f_099] of 6 [f_100] task legs are feasible on the conservative geometry bound, no unresolved flagged_manual thresholds exist, and the simulated success rate at normal traffic with conservative geometry is 98% [f_102], exceeding the 0.9 [f_101] threshold. The verdict would change if the success rate fell below 90% [f_103], a task leg became infeasible, or a flagged manual threshold appeared.

### MiR MiR100: Not cleared [f_373]

- Main reason: success 88% [f_375] at normal traffic (needs 90% [f_376]).

Rule checks as the site is today:

| Rule check | Result |
|---|---|
| every task leg feasible on the conservative geometry bound | met (value: 6 [f_377]/6 legs [f_378]) |
| no unresolved flagged_manual thresholds on the route | met (value: none) |
| simulated success at normal traffic (conservative bound) >= 0.90 [f_379] | not met (value: 88% [f_380]) |

**Why this verdict**

MiR MiR100 received Not cleared [f_373] because the simulated success rate at normal traffic under conservative geometry was 88% [f_380], below the 90% [f_381] threshold required by the rule check simulated success at normal traffic (conservative bound) >= 0.9 [f_379]. The other rule checks passed: all 6 [f_377] of 6 [f_378] task legs are feasible on the conservative geometry, and no unresolved flagged manual thresholds exist. The narrowest gap on route is 0.78 m [f_304] while the robot needs 0.58 m [f_305], so geometry is not limiting. However, simulation over 40 [f_346] episodes showed 1.75 [f_350] near‑misses per mission and 5 [f_353] episodes with no static path, contributing to the shortfall. The verdict would change if the success rate reaches at least 90% [f_381].

### MiR MiR250: Not cleared [f_190]

- Main reason: success 88% [f_192] at normal traffic (needs 90% [f_193]).

Rule checks as the site is today:

| Rule check | Result |
|---|---|
| every task leg feasible on the conservative geometry bound | met (value: 6 [f_194]/6 legs [f_195]) |
| no unresolved flagged_manual thresholds on the route | met (value: none) |
| simulated success at normal traffic (conservative bound) >= 0.90 [f_196] | not met (value: 88% [f_197]) |

**Why this verdict**

MiR MiR250 received Not cleared [f_190] because the simulated success rate at normal traffic under conservative geometry is 88% [f_192], below the required 90% [f_193] (rule check: simulated success at normal traffic (conservative bound) >= 0.9 [f_196] not met, value 88% [f_197] vs threshold 90% [f_198]). The other rule checks passed: all 6 [f_194] task legs are feasible on the conservative geometry bound (6 [f_195] legs total) and no unresolved flagged_manual thresholds exist. The narrowest gap on the route is 0.78 m [f_120] while the robot needs 0.58 m [f_121]. Median mission time is 125 s [f_165] with 2.05 [f_166] near‑misses per mission.

## 3. How we investigated

No investigation experiments were recorded for this analysis.

## 4. Why it fails (investigation)

No findings were recorded; the comparison and Pinch Map below show where time is lost.

## 5. Robot comparison (simulation-based estimates)

| Robot | Verdict | Legs feasible | Narrowest gap (needed) | Success, quiet traffic | Success, normal traffic | Success, peak traffic | Median mission | Near-misses per mission | Catalog specs |
|---|---|---|---|---|---|---|---|---|---|
| OTTO Motors OTTO 100 | Cleared | 6 [f_212] of 6 [f_211] | 0.78 m [f_214] (0.55 m [f_215]) | 98% [f_240][f_251] | 98% [f_253][f_264] | 100% [f_266][f_276] | 127 s [f_259] | 2.15 [f_260] | human-reviewed [f_210] |
| Pudu Robotics T300 | Cleared | 6 [f_028] of 6 [f_027] | 0.78 m [f_030] (0.50 m [f_031]) | 100% [f_056][f_066] | 98% [f_068][f_079] | 100% [f_081][f_091] | 115 s [f_074] | 1.77 [f_075] | human-reviewed [f_026] |
| MiR MiR100 | Not cleared | 6 [f_302] of 6 [f_301] | 0.78 m [f_304] (0.58 m [f_305]) | 88% [f_330][f_341] | 88% [f_343][f_354] | 82% [f_356][f_368] | 152 s [f_349] | 1.75 [f_350] | human-reviewed [f_300] |
| MiR MiR250 | Not cleared | 6 [f_118] of 6 [f_117] | 0.78 m [f_120] (0.58 m [f_121]) | 90% [f_146][f_157] | 88% [f_159][f_171] | 88% [f_173][f_185] | 125 s [f_165] | 2.05 [f_166] | human-reviewed [f_116] |

Success ranges run from the conservative to the optimistic geometry bound. Mission time and near-misses are at the reference traffic level on the nominal geometry.

- OTTO Motors OTTO 100, normal traffic, nominal geometry: 98% [f_255] (CI95 87–100% [f_257][f_258]) over 40 [f_256] episodes.
- Pudu Robotics T300, normal traffic, nominal geometry: 98% [f_070] (CI95 87–100% [f_072][f_073]) over 40 [f_071] episodes.
- MiR MiR100, normal traffic, nominal geometry: 88% [f_345] (CI95 74–95% [f_347][f_348]) over 40 [f_346] episodes.
- MiR MiR250, normal traffic, nominal geometry: 88% [f_161] (CI95 74–95% [f_163][f_164]) over 40 [f_162] episodes.

## 6. Pinch Map

The Pinch Map in the Clearance console shows where robots stop, wait or pass close to people. The tightest points and the main reasons for unsuccessful episodes:

- OTTO Motors OTTO 100, normal traffic: collision in 1 [f_263] of 40 [f_256] episodes.
- Pudu Robotics T300, normal traffic: collision in 1 [f_078] of 40 [f_071] episodes.
- MiR MiR100, normal traffic: no path static in 5 [f_353] of 40 [f_346] episodes.
- MiR MiR100: success falls from 88% [f_332] at quiet traffic to 82% [f_358] at peak traffic (nominal geometry).
- MiR MiR250, normal traffic: no path static in 4 [f_170] of 40 [f_162] episodes.
- MiR MiR250, normal traffic: collision in 1 [f_169] of 40 [f_162] episodes.
- MiR MiR250: success falls from 90% [f_148] at quiet traffic to 88% [f_175] at peak traffic (nominal geometry).

## 7. Recommended changes

No layout changes were needed or found.

## 8. Payback

Your inputs: staff cost $22/h [f_011]; staff hours per day on the task 4 [f_012]; operating days per week 5 [f_013]; operating days per month 21.666667 [f_096]; maintenance per year as a share of price 10% [f_014].

| Robot | Price | Automatable share | Monthly saving | Running costs | Net monthly | Payback |
|---|---|---|---|---|---|---|
| OTTO Motors OTTO 100 | price on request [f_209] | 98% [f_278] | $1,859/month [f_279] | — | — | enter a quote [f_280] |
| Pudu Robotics T300 | price on request [f_025] | 98% [f_093] | $1,859/month [f_094] | — | — | enter a quote [f_095] |
| MiR MiR100 | price on request [f_299] | 88% [f_370] | $1,668/month [f_371] | — | — | enter a quote [f_372] |
| MiR MiR250 | price on request [f_115] | 88% [f_187] | $1,668/month [f_188] | — | — | enter a quote [f_189] |

Formula: `automatable share = success rate (normal traffic, nominal geometry) x feasible leg fraction`; `monthly saving = staff cost x task hours x automatable share x days per month`; `payback = price / (monthly saving - monthly running costs)`.

## 9. Trade-offs between the robots

Both Cleared robots, OTTO Motors OTTO 100 and Pudu Robotics T300, achieve 98% [f_253] success rate (conservative geometry) with median mission times of 127 s [f_259] and 115 s [f_074] respectively, and near-misses of 2.15 [f_260] and 1.77 [f_075]. The Not cleared MiR MiR100 and MiR MiR250 show 88% [f_343] success rate, with mission times of 152 s [f_349] and 125 s [f_165] and near-misses of 1.75 [f_350] and 2.05 [f_166]. All robots face the same narrowest gap of 0.78 m [f_214] on route, implying similar layout constraints. Pricing for all units is price on request [f_209] with payback status enter a quote [f_280].

## 10. Hazards and manual checks

- OTTO Motors OTTO 100 price: price on request [f_209]; enter a quote to see payback.
- Pudu Robotics T300 price: price on request [f_025]; enter a quote to see payback.
- MiR MiR100 price: price on request [f_299]; enter a quote to see payback.
- MiR MiR250 price: price on request [f_115]; enter a quote to see payback.
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

- [PUDU T300 AMR | Intelligent Autonomous Mobile Robot for Industrial Material Delivery](https://www.pudurobotics.com/en/products/pudut300), retrieved 2026-10-27; used for Pudu Robotics T300: footprint_polygon_m, Pudu Robotics T300: height_m, Pudu Robotics T300: max_speed_mps, Pudu Robotics T300: max_threshold_m, Pudu Robotics T300: min_aisle_width_vendor_m, Pudu Robotics T300: payload_kg
- [https://www.hartfiel.com/wp-content/uploads/2024/04/MiR250-Specs.pdf](https://www.hartfiel.com/wp-content/uploads/2024/04/MiR250-Specs.pdf), retrieved 2026-10-27; used for MiR MiR250: footprint_polygon_m, MiR MiR250: height_m, MiR MiR250: max_slope_deg, MiR MiR250: max_speed_mps, MiR MiR250: max_threshold_m, MiR MiR250: min_aisle_width_vendor_m
- [https://mobile-industrial-robots.com/products/robots/mir250](https://mobile-industrial-robots.com/products/robots/mir250), retrieved 2026-10-27; used for MiR MiR250: price
- [https://cdn.sanity.io/files/armc7p5y/production/ec1dac3528cb906c23589c86c036b565f27e36cf.pdf?dl=](https://cdn.sanity.io/files/armc7p5y/production/ec1dac3528cb906c23589c86c036b565f27e36cf.pdf?dl=), retrieved 2026-10-27; used for OTTO Motors OTTO 100: footprint_polygon_m, OTTO Motors OTTO 100: height_m, OTTO Motors OTTO 100: in_place_rotation, OTTO Motors OTTO 100: max_speed_mps, OTTO Motors OTTO 100: max_threshold_m, OTTO Motors OTTO 100: min_aisle_width_vendor_m
- [https://howtorobot.com/sites/default/files/2021-02/MiR100%E2%84%A2%20_%20Mobile%20Industrial%20Robots_0.pdf](https://howtorobot.com/sites/default/files/2021-02/MiR100%E2%84%A2%20_%20Mobile%20Industrial%20Robots_0.pdf), retrieved 2026-10-27; used for MiR MiR100: footprint_polygon_m, MiR MiR100: height_m, MiR MiR100: max_slope_deg, MiR MiR100: max_speed_mps, MiR MiR100: max_threshold_m, MiR MiR100: min_aisle_width_vendor_m
- [PUDU T300 - autonomous transport robot with a payload of up to 300 kg](https://log-robot.com/en/amr-products/pudu-robotics/pudu-t300), retrieved 2026-10-27; used for market check: Pudu T300 AMR payload capacity specifications
- [Pudu T300 Robot — Price, Specs & Demo · RobotLAB](https://www.robotlab.com/store/pudu-t300), retrieved 2026-10-27; used for market check: Pudu T300 AMR payload capacity specifications
- [OTTO 100 | Quality top modules for OTTO Motors AMRs](https://www.nord-modules.com/otto-100), retrieved 2026-10-27; used for market check: OTTO 100 AMR payload capacity specifications
- [OTTO 100 Autonomous Mobile Robot | Autonomous Material Handling | OTTO by Rockwell Automation](https://ottomotors.com/100), retrieved 2026-10-27; used for market check: OTTO 100 AMR payload capacity specifications
- [OTTO AMR Comparison: 100 vs. 600 vs. 1200 vs. 1500 vs. Lifter](https://blog.hesconet.com/otto-amr-comparison-100-600-1200-1500-lifter), retrieved 2026-10-27; used for market check: OTTO 100 AMR payload capacity specifications
