# Robot catalog

`catalog/robots/<robot_id>.json` holds one `RobotSpec` (`clearance_core/schemas.py`) per robot. Only robots a person has reviewed (`verified_by_human: true`) get loaded for simulation (`agents.catalog_builder.catalog.load_catalog()`). Every entry below is **awaiting review**.

## How the specs were sourced

1. **Pages.** We read public vendor spec pages and datasheets on 2026-10-27. Where the vendor site blocked automated access or had no spec table, we used a vendor datasheet hosted by a distributor (MiR250 at hartfiel.com, MiR100 at howtorobot.com). We took no reseller prices, no images and no logos.
2. **Text.** Each page was fetched over HTTP and turned into plain text by `agents/catalog_builder/pages.py`. PDFs go through `pypdf`. The full text is cached outside the repo (`~/.cache/pg/catalog_pages/by_url`). The same text is also stored in the Tavily response shape under `agents/catalog_builder/recorded/tavily/`, so offline mode and the tests can re-check every quote.
3. **Quotes.** For each field we picked a verbatim span of the page text that contains the number. The agent chose the quotes for this seed set. The pipeline (`build.py`) gets them from Nemotron Nano in live mode, or from a deterministic rule-based reader in offline mode.
4. **Units and validation by code.** `units.py` converts the number written in the quote to SI: mm/cm/in/ft → m, km/h/mph/ft/s → m/s, lb → kg, min → h, and % grade → degrees via atan. `validate.py::validate_field` then accepts the value only if both of these hold:
   - the quote appears in the page text after whitespace and unicode normalization;
   - a quantity of the right dimension in the quote converts to exactly that value.

   A field that fails is left `null`, never estimated.
5. **Footprint.** It comes from the vendor's L×W (or W×D) dimensions, stored as a rectangle centred on the body, with `footprint_approx: true`. The body height comes from the same quote.
6. **Price.** None of these vendors publish a price. `price.note` is `"price on request"`, with the vendor's own sales or quote wording as the source where the page has one. The report asks the user to enter a quote.
7. **Notes.** `notes` is a JSON object with these keys:
   - `agent_quote_validated`: `true` means every sourced field passed the validator against the stored page text.
   - `review_status`
   - `null_fields`
   - `conflicts`
   - `comments`: which column or variant was used, and which operating condition applies, for example runtime at no load vs full load.

## Robots

Footprint is length × width in metres. Every non-null field has a `fields_sources` entry with url, quote and retrieved_at.

| robot_id | class | robot | footprint (m) | max speed (m/s) | payload (kg) | null fields | sources |
|---|---|---|---|---|---|---|---|
| `bear_servi` | A_service | Bear Robotics Servi | 0.430 x 0.445 | 1.2 | 30 | nominal_speed, max_threshold, max_slope, min_aisle_width, safety_stop_distance | bearrobotics.ai |
| `bear_servi_plus` | A_service | Bear Robotics Servi Plus | 0.590 x 0.535 | 1.2 | 40 | nominal_speed, safety_stop_distance | bearrobotics.ai; max_slope from antonrobots.com (live) |
| `keenon_dinerbot_t8` | A_service | Keenon Robotics DINERBOT T8 | 0.468 x 0.384 | 1.0 | 20 | nominal_speed, max_threshold, safety_stop_distance | keenon.com |
| `keenon_dinerbot_t9` | A_service | Keenon Robotics DINERBOT T9 | 0.527 x 0.500 | 1.0 | 40 | nominal_speed, max_threshold, safety_stop_distance | keenon.com |
| `keenon_w3` | A_service | Keenon Robotics W3 (BUTLERBOT W3) | 0.549 x 0.459 | 0.8 | 20 | nominal_speed, max_threshold, safety_stop_distance | keenon.com, robotlab.com (live) |
| `pudu_bellabot` | A_service | Pudu Robotics BellaBot | 0.565 x 0.537 | 1.2 | 40 | nominal_speed, safety_stop_distance | pudurobotics.com, academy.pudutech.com |
| `pudu_kettybot` | A_service | Pudu Robotics KettyBot | 0.435 x 0.450 | 1.2 | 30 | nominal_speed, safety_stop_distance | KettyBot user manual (Pudu document, distributor-hosted), robotsourced.com (live) |
| `pudu_pudubot_2` | A_service | Pudu Robotics PuduBot 2 | 0.580 x 0.535 | 1.2 | 40 | nominal_speed, safety_stop_distance | pudurobotics.com; max_threshold, max_slope from antonrobots.com (live) |
| `mir_mir100` | B_amr | MiR MiR100 | 0.890 x 0.580 | 1.5 | 100 | nominal_speed, safety_stop_distance | howtorobot.com (MiR datasheet) |
| `mir_mir250` | B_amr | MiR MiR250 | 0.800 x 0.580 | 2.0 | 250 | nominal_speed, safety_stop_distance | hartfiel.com (MiR spec doc 2023-06-28), mobile-industrial-robots.com |
| `otto_100` | B_amr | OTTO Motors OTTO 100 | 0.740 x 0.550 | 2.0 | 150 | nominal_speed, max_slope, safety_stop_distance | OTTO spec sheet OTTO-DS001F-EN-MAR2026 (cdn.sanity.io) |
| `pudu_t300` | B_amr | Pudu Robotics T300 (Standard) | 0.835 x 0.500 | 1.2 | 300 | nominal_speed, max_slope, safety_stop_distance | pudurobotics.com |

"(live)" marks values the live pipeline added on 2026-10-27; every one passed the validator. Fields taken from third-party review or reseller pages carry a comment asking the reviewer to confirm them with the vendor.

No vendor publishes a separate typical or cruise speed, so `nominal_speed_mps` is `null` everywhere. The simulator should treat it as unknown, not as `max_speed_mps`. `safety_stop_distance_m` is also `null` everywhere. The only braking figure we found belongs to an older KEENBOT T8 laser variant, so we did not use it for the current DINERBOT T8.

Each robot's `notes.comments` lists the readings a reviewer should check:

- The OTTO 100 threshold is the sheet's "Floor Obstacle" value.
- The MiR250 aisle width uses the default footprint.
- Runtimes are given at the condition the vendor states.

## Live pipeline run (2026-10-27)

We ran the full live pipeline (`build_spec`: Tavily Search → Tavily Extract → Nemotron Nano extraction → unit conversion and verbatim validator) for every catalog robot and for five candidate robots, with `CLEARANCE_TAVILY_MODE=live` and `CLEARANCE_LLM_MODE=live`. Tavily supplied every page the model read; no direct HTTP fetches were used. Each robot's `notes.live_check` records the pages read, the fields that agree, the fields added, the values that differ, and the number of validator rejections.

### Usage

| measure | value |
|---|---|
| robots processed | 15 (10 catalog robots + 5 candidates) |
| pipeline runs | 22 (15 first runs; 7 re-runs after the ranking fix below, which replayed the recorded searches) |
| Tavily Search calls | 30 (28 pipeline + 2 targeted look-ups), 30 credits |
| Tavily Extract calls | 20, 65 URLs, about 27 credits (advanced extract; one call during an interrupted run is counted from its recorded pages) |
| Tavily credits in total | about 57, from the client's per-call `usage.credits` log |
| Nemotron Nano calls | 87 (83 in completed runs, up to 4 in the interrupted run), 0 errors |
| validated fields filled by the model | 129 across the 21 completed runs (footprint and numeric fields) |
| model values rejected by the validator | 60 across the same runs (for example: quote not on the page, wrong unit, value from another product, figures read in the wrong order) |

### What changed in the catalog

- Fields added (were null, the live value passed the validator): `bear_servi_plus.max_slope_deg` = 7°, `pudu_pudubot_2.max_threshold_m` = 0.01 m and `max_slope_deg` = 5°. All three come from a third-party review site (antonrobots.com) and are flagged for the reviewer.
- Validated but held for review: `keenon_dinerbot_t8.max_threshold_m` = 0.01 m. It comes from a reseller page (somebots.com) and is worded as operating advice ("avoid steps over 1 cm"), not as a vendor capability. With this value the simulator treats the thresholds in the reference cafe as impassable for the T8. It is stored in `notes.candidate_fields` until a reviewer confirms the vendor figure.
- Robots added: `keenon_w3` and `pudu_kettybot`. Footprint, width and max speed all validated for both.
- Candidates not added: Locus Origin (footprint and payload validated, but Locus publishes no maximum speed on the pages found, even after a targeted search) and Zebra FlexShelf (the search returned no spec page for this model; no field validated).
- Every other live value either matched the catalog (within 1 %, or the same footprint read in the other orientation) or is listed below. No catalog value was replaced.

### For the reviewer: live values that differ (catalog value kept)

- `mir_mir100.runtime_h`: the catalog has 10 h (MiR datasheet, active operation time at maximum payload). A later MiR100 spec document (version 2.57, third-party host) states "Up to 6 h 53 min (24V Standard battery)". The two documents describe different versions or batteries. Please confirm which applies.
- `mir_mir100.min_aisle_width_vendor_m`: the catalog has 1.0 m (default footprint). The live value of 1.1 m is for the minimized footprint, SICK safety configuration and no payload.
- `mir_mir250.min_aisle_width_vendor_m`: the catalog has 1.45 m (default footprint), which two live pages also state. The live value of 1.0 m is for the dynamic footprint.
- `keenon_dinerbot_t9.max_speed_mps`: a reseller page states 0.8 m/s. The catalog keeps the vendor's 1.0 m/s.
- Runtimes on reseller or review pages differ from the vendor figures for `bear_servi` and `bear_servi_plus` (8 h vs 10 h), `pudu_bellabot` (10 h and 12 h vs 13 h) and `pudu_t300` (6 h in a distributor-hosted operation guide vs 8 h). The vendor figures are kept.
- `pudu_kettybot`: the user manual's spec table lists cruise speed "0.5~0.9m/s", and its speed setting range goes up to 1.2 m/s. We keep 1.2 m/s from the setting range. For runtime we keep the manual's ">8h" (8 h) over a reseller's 10 h. Payload (30 kg) appears only on reseller pages.

### Pipeline improvements from this run

- **Search ranking now matches the exact model.** The first live run ranked sibling-model pages, such as "Servi Plus" for Servi and "BellaBot Pro" for BellaBot, above the model's own page. Their values were not used. `build.search_urls` now ranks pages in this order: pages that name the exact model; then vendor-hosted pages, where a vendor word matches the host, e.g. Keenon Robotics → keenon.com; then the Tavily score. Pages that name only a sibling model come last. With the new ranking, the re-runs agree with the catalog.
- **Units in brackets.** The number reader now reads a unit in brackets after the numbers, e.g. "435*450*1120 (mm)", as that unit.
- **Malformed model output.** Malformed model output (non-object fields, a missing unit) is rejected with a reason and no longer stops the build.

## Commands

```bash
# build one robot (live: TAVILY_API_KEY + NEBIUS_API_KEY; offline: recorded responses + rule-based reader)
python -m agents.catalog_builder.build --vendor "Pudu Robotics" --model BellaBot --class A_service --out /tmp/bellabot.json
# review: show, edit with a source, approve
python -m agents.catalog_builder.review pudu_bellabot
python -m agents.catalog_builder.review pudu_bellabot --set max_slope_deg=5 --url URL --quote "..."
python -m agents.catalog_builder.review pudu_bellabot --approve --reviewer NAME
# re-run the validator over all catalog files against the cached page text
python -m agents.catalog_builder.revalidate
```

Modes: `CLEARANCE_TAVILY_MODE=offline|live` and `CLEARANCE_LLM_MODE=offline|live`. If unset, each one defaults to live when its key is present.
