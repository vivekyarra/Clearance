# Clearance Report

Every number below links to where it came from: an on-site measurement, a cited page, your own inputs, a formula, or a simulation batch you can replay. Simulation results are simulation-based estimates, shown as ranges across scan uncertainty.

## Summary

The clearance check evaluated the robots against the surveyed site geometry (scan median absolute error 1.0 cm [f_002], p90 3.0 cm [f_003]). Keenon Robotics DINERBOT T8 is Cleared [f_086] because it meets every rule as‑is. Bear Robotics Servi is Not cleared [f_128] because the narrowest gap on its route is 0.52 m [f_130] while the robot needs 0.62 m [f_131]. Bear Robotics Servi Plus is Not cleared [f_208] because the DOCK stop point is only 0.19 m [f_210] from a wall or scanned obstacle. Keenon Robotics DINERBOT T9 is Not cleared [f_168] for the same DOCK clearance of 0.19 m [f_170]. Pudu Robotics BellaBot is Not cleared [f_248] with a DOCK clearance of 0.19 m [f_250].

The most useful next step for the site owner is to verify the DOCK area clearance on site and, if possible, increase the free space to at least the required margin for the affected robots.

## 1. Site evidence

- Geometry source: scanned site [f_005].
- Scan accuracy (tape check on site [f_001]): median absolute error 1.0 cm [f_002], p90 absolute error 3.0 cm [f_003]. This error sets the conservative and optimistic geometry bounds used in every simulation.
- Floor area inside the scanned bounds: 15.2 m² [f_004].
- Pedestrian traffic (user-estimated [f_006]): quiet 2 [f_007], normal 6 [f_008], peak 14 [f_009] persons per minute.

## 2. Verdicts

Verdicts are decided by code from fixed rules; the text only explains them.

### Keenon Robotics DINERBOT T8: Cleared [f_086]

- Main reason: meets every rule as-is.

Rule checks as the site is today:

| Rule check | Result |
|---|---|
| every task leg feasible on the conservative geometry bound | met (value: 3 [f_088]/3 legs [f_089]) |
| no unresolved flagged_manual thresholds on the route | met (value: none) |
| simulated success at normal traffic (conservative bound) >= 0.90 [f_090] | met (value: 100% [f_091]) |

**Why this verdict**

The Keenon Robotics DINERBOT T8 received a Cleared [f_086] verdict because it met all as‑is rule checks: every task leg feasible on the conservative geometry bound (3 [f_088] of 3 [f_089] legs), no unresolved flagged‑manual thresholds, and simulated success at normal traffic under conservative geometry (100% [f_091]) exceeding the 0.9 [f_090] threshold. Outcome would change if success fell below 90% [f_092], a leg became infeasible, or a flagged‑manual threshold appeared.

### Bear Robotics Servi: Not cleared [f_128]

- Main reason: no path DOCK->PICKUP: narrowest gap 0.52 m [f_130] vs 0.62 m [f_131] needed.

Rule checks as the site is today:

| Rule check | Result |
|---|---|
| every task leg feasible on the conservative geometry bound | not met (value: 1 [f_132]/3 legs [f_133]) |
| no unresolved flagged_manual thresholds on the route | met (value: none) |
| simulated success at normal traffic (conservative bound) >= 0.90 [f_134] | pending (measure on site) (value: not simulated) |

**Why this verdict**

Bear Robotics Servi received Not cleared [f_128] because the static geometry check failed: only 1 [f_132] of 3 [f_133] task legs were feasible on the conservative bound, as the narrowest gap on the route is 0.52 m [f_130] while the robot needs 0.62 m [f_131] at that point. The rule requiring simulated success at normal traffic (conservative bound) ≥ 0.9 [f_134] (90% [f_135]) is pending because no conservative‑bound simulation was run. The outcome would change if the gap were widened to at least 0.62 m [f_131] or an alternative route made all legs feasible, and if a conservative‑bound simulation showed a success rate meeting the 90% [f_135] threshold.

### Bear Robotics Servi Plus: Not cleared [f_208]

- Main reason: no path DOCK->PICKUP: stop point DOCK is too close to a wall or scanned obstacle for this robot: 0.19 m [f_210] from it.

Rule checks as the site is today:

| Rule check | Result |
|---|---|
| every task leg feasible on the conservative geometry bound | not met (value: 0 [f_211]/3 legs [f_212]) |
| no unresolved flagged_manual thresholds on the route | met (value: none) |
| simulated success at normal traffic (conservative bound) >= 0.90 [f_213] | pending (measure on site) (value: not simulated) |

**Why this verdict**

Bear Robotics Servi Plus received Not cleared [f_208] because the static feasibility check failed: 0 [f_189] of the 3 [f_188] task legs were feasible on the conservative geometry bound, and the narrowest gap on the route is 0.52 m [f_191] while the robot requires 0.54 m [f_192]. The primary blocker is that the DOCK stop point is only 0.19 m [f_210] from a wall, preventing a path to PICKUP. The traffic simulation rule requiring 90% [f_214] success at the conservative bound 0.9 [f_213] remains pending (measure on site). Clearing would require widening the gap or relocating the dock so the robot meets the geometry and traffic thresholds.

### Keenon Robotics DINERBOT T9: Not cleared [f_168]

- Main reason: no path DOCK->PICKUP: stop point DOCK is too close to a wall or scanned obstacle for this robot: 0.19 m [f_170] from it.

Rule checks as the site is today:

| Rule check | Result |
|---|---|
| every task leg feasible on the conservative geometry bound | not met (value: 0 [f_171]/3 legs [f_172]) |
| no unresolved flagged_manual thresholds on the route | met (value: none) |
| simulated success at normal traffic (conservative bound) >= 0.90 [f_173] | pending (measure on site) (value: not simulated) |

**Why this verdict**

The DINERBOT T9 received Not cleared [f_168] because the conservative geometry check failed: 0 [f_171] of 3 [f_148] task legs were feasible, primarily because the DOCK stop point is only 0.19 m [f_170] from a wall or scanned obstacle, leaving insufficient clearance. The narrowest gap on the route is 0.52 m [f_151] while the robot requires 0.50 m [f_152], but the geometry rule is not satisfied. The traffic simulation rule requiring ≥90% [f_174] success (threshold 0.9 [f_173]) remains pending (measure on site). Resolving the DOCK clearance or adjusting the layout would be needed to change the outcome.

### Pudu Robotics BellaBot: Not cleared [f_248]

- Main reason: no path DOCK->PICKUP: stop point DOCK is too close to a wall or scanned obstacle for this robot: 0.19 m [f_250] from it.

Rule checks as the site is today:

| Rule check | Result |
|---|---|
| every task leg feasible on the conservative geometry bound | not met (value: 0 [f_251]/3 legs [f_252]) |
| no unresolved flagged_manual thresholds on the route | met (value: none) |
| simulated success at normal traffic (conservative bound) >= 0.90 [f_253] | pending (measure on site) (value: not simulated) |

**Why this verdict**

Pudu Robotics BellaBot received Not cleared [f_248] because the conservative geometry check failed: 0 [f_251] of 3 [f_252] task legs were feasible, the narrowest gap on the route is 0.52 m [f_231] while the robot requires 0.54 m [f_232], and the dock stop point is only 0.19 m [f_250] from a wall, violating the clearance rule. The traffic simulation threshold of 90% [f_254] (rule 0.9 [f_253]) remains pending as it was not simulated.

## 3. How we investigated

The investigation loop runs simulator experiments on robots that can drive the route but fall short of the success rule. Keenon Robotics DINERBOT T8 already met every rule, so there was nothing to investigate. Bear Robotics Servi, Bear Robotics Servi Plus, Keenon Robotics DINERBOT T9, Pudu Robotics BellaBot cannot complete every task leg on the measured geometry; the reasons are in the Pinch Map below, and the recommended changes show what would open the route.

## 4. Why it fails (investigation)

No findings were recorded; the comparison and Pinch Map below show where time is lost.

## 5. Robot comparison (simulation-based estimates)

| Robot | Verdict | Legs feasible | Narrowest gap (needed) | Success, quiet traffic | Success, normal traffic | Success, peak traffic | Median mission | Near-misses per mission | Catalog specs |
|---|---|---|---|---|---|---|---|---|---|
| Keenon Robotics DINERBOT T8 | Cleared | 3 [f_028] of 3 [f_027] | 0.43 m [f_030] (0.38 m [f_031]) | 100% [f_044][f_054] | 100% [f_056][f_067] | 97–98% [f_080][f_069] | 44 s [f_062] | 0.45 [f_063] | human-reviewed [f_026] |
| Bear Robotics Servi | Not cleared | 1 [f_104] of 3 [f_103] | 0.52 m [f_106] (0.62 m [f_107]) | 100% [f_122] | 98% [f_124] | 95% [f_126] | — | — | human-reviewed [f_102] |
| Bear Robotics Servi Plus | Not cleared | 0 [f_189] of 3 [f_188] | 0.52 m [f_191] (0.54 m [f_192]) | — | — | — | — | — | human-reviewed [f_187] |
| Keenon Robotics DINERBOT T9 | Not cleared | 0 [f_149] of 3 [f_148] | 0.52 m [f_151] (0.50 m [f_152]) | — | — | — | — | — | human-reviewed [f_147] |
| Pudu Robotics BellaBot | Not cleared | 0 [f_229] of 3 [f_228] | 0.52 m [f_231] (0.54 m [f_232]) | — | — | — | — | — | human-reviewed [f_227] |

Success ranges run from the conservative to the optimistic geometry bound. Mission time and near-misses are at the reference traffic level on the nominal geometry.

- Keenon Robotics DINERBOT T8, normal traffic, nominal geometry: 99% [f_058] (CI95 95–100% [f_060][f_061]) over 100 [f_059] episodes.

## 6. Pinch Map

The Pinch Map in the Clearance console shows where robots stop, wait or pass close to people. The tightest points and the main reasons for unsuccessful episodes:

- Keenon Robotics DINERBOT T8, normal traffic: collision in 1 [f_066] of 100 [f_059] episodes.
- Keenon Robotics DINERBOT T8: success falls from 100% [f_046] at quiet traffic to 95% [f_071] at peak traffic (nominal geometry).
- Bear Robotics Servi, leg DOCK to PICKUP: blocked [f_108]; narrowest gap 0.52 m [f_109] vs 0.62 m [f_110] needed.
- Bear Robotics Servi, leg PICKUP to TBL1: blocked [f_113]; narrowest gap 0.52 m [f_114] vs 0.62 m [f_115] needed.
- Bear Robotics Servi Plus, leg DOCK to PICKUP: blocked [f_193]; narrowest gap 0.55 m [f_194] vs 0.54 m [f_195] needed; the route is wide enough, but stop point DOCK is too close to a wall or scanned obstacle for this robot: 0.19 m [f_197] from it.
- Bear Robotics Servi Plus, leg PICKUP to TBL1: blocked [f_198]; narrowest gap 0.52 m [f_199] vs 0.54 m [f_200] needed.
- Bear Robotics Servi Plus, leg TBL1 to DOCK: blocked [f_203]; narrowest gap 0.55 m [f_204] vs 0.54 m [f_205] needed; the route is wide enough, but stop point DOCK is too close to a wall or scanned obstacle for this robot: 0.19 m [f_207] from it.
- Keenon Robotics DINERBOT T9, leg DOCK to PICKUP: blocked [f_153]; narrowest gap 0.55 m [f_154] vs 0.50 m [f_155] needed; the route is wide enough, but stop point DOCK is too close to a wall or scanned obstacle for this robot: 0.19 m [f_157] from it.
- Keenon Robotics DINERBOT T9, leg PICKUP to TBL1: blocked [f_158]; narrowest gap 0.52 m [f_159] vs 0.50 m [f_160] needed; the route is wide enough, but stop point PICKUP is too close to a wall or scanned obstacle for this robot: 0.21 m [f_162] from it.
- Keenon Robotics DINERBOT T9, leg TBL1 to DOCK: blocked [f_163]; narrowest gap 0.55 m [f_164] vs 0.50 m [f_165] needed; the route is wide enough, but stop point DOCK is too close to a wall or scanned obstacle for this robot: 0.19 m [f_167] from it.
- Pudu Robotics BellaBot, leg DOCK to PICKUP: blocked [f_233]; narrowest gap 0.55 m [f_234] vs 0.54 m [f_235] needed; the route is wide enough, but stop point DOCK is too close to a wall or scanned obstacle for this robot: 0.19 m [f_237] from it.
- Pudu Robotics BellaBot, leg PICKUP to TBL1: blocked [f_238]; narrowest gap 0.52 m [f_239] vs 0.54 m [f_240] needed.
- Pudu Robotics BellaBot, leg TBL1 to DOCK: blocked [f_243]; narrowest gap 0.55 m [f_244] vs 0.54 m [f_245] needed; the route is wide enough, but stop point DOCK is too close to a wall or scanned obstacle for this robot: 0.19 m [f_247] from it.

## 7. Recommended changes

No layout changes were needed or found.

## 8. Payback

Your inputs: staff cost $150/h [f_011]; staff hours per day on the task 2 [f_012]; operating days per week 6 [f_013]; operating days per month 26 [f_085]; maintenance per year as a share of price 10% [f_014].

| Robot | Price | Automatable share | Monthly saving | Running costs | Net monthly | Payback |
|---|---|---|---|---|---|---|
| Keenon Robotics DINERBOT T8 | price on request [f_025] | 99% [f_082] | $7,722/month [f_083] | — | — | enter a quote [f_084] |

Formula: `automatable share = success rate (normal traffic, nominal geometry) x feasible leg fraction`; `monthly saving = staff cost x task hours x automatable share x days per month`; `payback = price / (monthly saving - monthly running costs)`.

## 9. Trade-offs between the robots

- Keenon Robotics DINERBOT T8: success rate 100% [f_056] (conservative) and 100% [f_067] (optimistic), median mission time 44 s [f_062], near‑misses 0.45 [f_063] per mission, verdict Cleared [f_086].
- Bear Robotics Servi verdict Not cleared [f_128]; Bear Robotics Servi Plus verdict Not cleared [f_208]; Keenon Robotics DINERBOT T9 verdict Not cleared [f_168]; Pudu Robotics BellaBot verdict Not cleared [f_248]; no published simulation success rates or mission times for these robots.
- Narrowest route gap: Keenon T8 0.43 m [f_030]; Bear Servi 0.52 m [f_106]; Bear Servi Plus 0.52 m [f_191]; Keenon T9 0.52 m [f_151]; Pudu BellaBot 0.52 m [f_231].
- Price: Keenon T8 price on request [f_025]; Bear Servi price on request [f_101]; Bear Servi Plus price on request [f_186]; Keenon T9 price on request [f_146]; Pudu BellaBot price on request [f_226]. Payback for Keenon T8 enter a quote [f_084]; no payback data for the others.
- Trade‑off: the cleared robot delivers higher simulated reliability and faster cycles but requires tighter clearance, while the not‑cleared robots need wider gaps and have unknown performance and payback.

## 10. Hazards and manual checks

- Keenon Robotics DINERBOT T8 price: price on request [f_025]; enter a quote to see payback.
- Bear Robotics Servi price: price on request [f_101]; enter a quote to see payback.
- Bear Robotics Servi Plus price: price on request [f_186]; enter a quote to see payback.
- Keenon Robotics DINERBOT T9 price: price on request [f_146]; enter a quote to see payback.
- Pudu Robotics BellaBot price: price on request [f_226]; enter a quote to see payback.
- Traffic is user-estimated [f_006]; a fixed observer phone at peak time would replace the estimate.

## 11. Methodology

- The site was scanned with phones and turned into a measured twin; robot footprints come from cited vendor specs.
- Every robot ran a footprint-accurate 2D navigation simulation on the nominal, conservative (obstacles grown by the scan error) and optimistic (obstacles shrunk by it) geometry bounds, using the p90 scan error of 3.0 cm [f_003].
- Operator confirmed 1 [f_255] unseen floor patch clear (0.6 m² [f_256], by operator [f_257]); unseen cells inside them count as free floor in every simulation, measured obstacles are unchanged.
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
- [https://www.keenon.com/en/product/T9](https://www.keenon.com/en/product/T9), retrieved 2026-10-27; used for Keenon Robotics DINERBOT T9: footprint_polygon_m, Keenon Robotics DINERBOT T9: height_m, Keenon Robotics DINERBOT T9: max_slope_deg, Keenon Robotics DINERBOT T9: max_speed_mps, Keenon Robotics DINERBOT T9: min_aisle_width_vendor_m, Keenon Robotics DINERBOT T9: payload_kg
- [https://www.bearrobotics.ai/servi-plus](https://www.bearrobotics.ai/servi-plus), retrieved 2026-10-27; used for Bear Robotics Servi Plus: footprint_polygon_m, Bear Robotics Servi Plus: height_m, Bear Robotics Servi Plus: max_speed_mps, Bear Robotics Servi Plus: max_threshold_m, Bear Robotics Servi Plus: min_aisle_width_vendor_m, Bear Robotics Servi Plus: payload_kg
- [https://antonrobots.com/bear-robotics-servi-plus-review](https://antonrobots.com/bear-robotics-servi-plus-review), retrieved 2026-10-27; used for Bear Robotics Servi Plus: max_slope_deg
- [https://academy.pudutech.com/en/docs/chan-pin-jie-shao](https://academy.pudutech.com/en/docs/chan-pin-jie-shao), retrieved 2026-10-27; used for Pudu Robotics BellaBot: footprint_polygon_m, Pudu Robotics BellaBot: height_m, Pudu Robotics BellaBot: max_slope_deg, Pudu Robotics BellaBot: max_threshold_m, Pudu Robotics BellaBot: min_aisle_width_vendor_m
- [https://www.pudurobotics.com/en/products/bellabot](https://www.pudurobotics.com/en/products/bellabot), retrieved 2026-10-27; used for Pudu Robotics BellaBot: max_speed_mps, Pudu Robotics BellaBot: payload_kg, Pudu Robotics BellaBot: runtime_h
