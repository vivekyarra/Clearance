# Clearance: submission kit

## Devpost

**Title:** Clearance: Know if a robot fits before you buy it

**Tagline (short description):** Scan a real room with an iPhone, get a code-decided verdict on which delivery robots and AMRs can work there, what to move, and what it pays back, with every number traced to a run, a measurement or a source.

**Track:** Physical AI

### Inspiration
Restaurants, cafés, clinics and small warehouses are being sold service robots that cost as much as a year of staff time. Whether a robot works depends on that exact site: aisle widths, turning space, door lips, clutter and people at peak hours. Today the only answers come from a vendor visit or a paid pilot. We wanted an independent answer an owner can get from a five-minute phone scan.

### What it does
- **Scan:** the Clearance iPhone app records ARKit poses and keyframes anchored to a floor marker; faces are blurred on the phone. Android phones join as fixed observers for foot traffic.
- **Twin:** the server turns the scan into a measured 2.5 cm occupancy twin. On a real room scanned with an iPhone 13 (no LiDAR), object sizes matched a tape measure with a **median error of 1.0 cm** across 8 distances.
- **Clearance check:** code filters a 12-robot catalog built with Tavily, checks static feasibility on three geometry bounds, and runs Monte Carlo navigation batches with pedestrians.
- **Nemotron investigation loop:** Nemotron 3.5 Super VL forms a hypothesis about why a robot fails, picks the next experiment (fewer pedestrians in a zone, a chair moved, a different robot), runs it in the simulator, reads the result and decides whether to dig further. The trail appears in the report as "How we investigated".
- **Verdicts by code:** every robot gets exactly one verdict, Cleared, Cleared with changes, or Not cleared, from a deterministic rule with thresholds in config. Nemotron explains verdicts and never sets them.
- **Clearance Report:** Nemotron 3 Ultra writes the narrative; every number carries a fact tag that a fact-checker verifies against its source.
- **AR replay:** point the iPhone at the marker and watch the robot drive the mission in the real room.

### How we built it
- **NVIDIA Nemotron on Nebius Token Factory:** Nemotron 3.5 Lightning extracts robot specs with a verbatim quote per field; Nemotron 3.5 Super VL runs the orchestrator and the investigation loop with tool calling; Nemotron 3 Ultra writes the report.
- **Cosmos Reason 2** (vLLM on Nebius AI Cloud) for scene hazards from keyframes; it never outputs geometry.
- **Nebius AI Cloud / Serverless Jobs** definitions for GPU reconstruction and parallel CPU simulation shards; **Nebius Object Storage** for captures and twins.
- **Tavily** Search and Extract feed the catalog: 129 spec fields validated against source pages, 60 rejected by the quote validator.
- **Depth Anything V2 Small** (Apache-2.0) aligned per frame to ARKit feature points for non-LiDAR phones; our own footprint-accurate navigation simulator with social-force pedestrians; a layout optimizer; FastAPI, React, three.js, Swift/ARKit/RealityKit.

### Challenges we ran into
- Turning monocular depth into centimetre-level geometry: per-frame scale/shift alignment to ARKit feature points, transient-object removal (people walking through a scan) and honest "unknown" areas.
- Keeping language models away from numbers: a hybrid report writer (code renders tables and verdict rules, Ultra writes the narrative) plus a fact-checker that rejects any untagged or mismatched number.
- Making the simulator deterministic and fair across seeds, shards and machines.

### Accomplishments we're proud of
- A real-room scan from a plain iPhone 13 matching a tape measure to a median 1.0 cm.
- A go/no-go gate we ran first, before building the rest, and passed on reference rooms and on a real room.
- 2,700+ automated tests, a 94-test Swift suite and an iOS build in CI.

### What we learned
Geometry and foot traffic decide whether a wheeled robot works; visuals do not. The most useful thing a language model does here is investigate and explain, not measure.

### What's next
Cardboard-footprint validation at real pinch points, a real café owner pilot, more AMR classes, and a hosted demo for integrators to pre-qualify sites remotely.

### Built With
`nvidia-nemotron` `nebius-token-factory` `nebius-ai-cloud` `cosmos-reason-2` `tavily` `python` `fastapi` `numpy` `numba` `opencv` `pytorch` `depth-anything-v2` `react` `typescript` `vite` `three.js` `swift` `arkit` `realitykit` `sqlite` `github-actions`

---

## Demo video (3:00, real world first)

**Watch:** https://youtu.be/sYL-GOtZIys

| Time | What it shows |
|---|---|
| 0:00 | Robots are for sale now. Will one fit in your space? |
| 0:14 | Clearance: for cafés, hotels and warehouses today, every home tomorrow |
| 0:20 | Join the site with a QR code; the marker phone on the floor |
| 0:26 | **Hardware:** scanning a real room with an iPhone 13 (no LiDAR), faces blurred on the phone |
| 1:20 | The scan becomes a 2.5 cm floor twin |
| 1:30 | Tape measure 1.91 m vs scan 1.95 m; median error 1.0 cm over 8 checks |
| 1:42 | Editor: confirm a clear patch, place the dock, pickup and table, run the check |
| 1:52 | How it works: robot specs from Tavily, Nemotron on Nebius Token Factory investigates and writes the report |
| 2:16 | Code-decided verdicts: DINERBOT T8 Cleared, Bear Servi Not cleared (0.52 m gap, needs 0.62 m) |
| 2:32 | **Hardware:** AR replay, the robot drives the route in the real room |

### Clips recorded on 2026-10-28
- iPhone screen recording of the scan (marker lock, coverage map, upload).
- Side camera footage of the scan.
- Laptop screen recording: console, upload arriving, twin, editor, Clearance check.
- AR replay of the DINERBOT T8 mission on the iPhone, in the scanned room.
- Tape-measure close-up for the 1:15 shot: film it with the next clip session (one take, 10 seconds).

### Real-room result for the narration
DINERBOT T8 **Cleared**: 100% / 99% / 95% success at quiet / normal / peak traffic, narrowest gap 0.43 m vs 0.38 m
needed. Servi, Servi Plus, T9 and BellaBot **Not cleared**, each with the exact pinch point (Servi needs 0.62 m to
turn where the route is 0.52 m; the other three fit the route but not the dock stop, 0.19 m from the wall).
Full report: `docs/onsite_clearance_report.md`.
