# Changelog

All notable changes to Clearance are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-10-28

First public release.

### Added
- **Shared contracts:** frozen data shapes for captures, twins, robots, simulation batches, facts, investigations
  and verdicts, exported as 10 JSON Schemas, plus code-decided verdicts (Cleared, Cleared with changes, Not cleared)
  with thresholds in `config/sim_defaults.yaml`.
- **Scan to twin:** ARKit captures fused into a 2.5 cm occupancy twin. LiDAR tier, and a camera-only tier with
  Depth Anything V2 Small aligned to ARKit feature points. Transient-object removal, door thresholds and
  operator-confirmed clear floor.
- **Scan-to-occupancy gate:** reference rooms at a median error of 1.2 cm (LiDAR) and 0.9 cm (camera only); a real
  room scanned with an iPhone 13 at a median of 1.0 cm against a tape measure across 8 distances.
- **Navigation simulator:** footprint-accurate 2D planning at 36 headings, Monte Carlo batches with social-force
  pedestrians, three geometry bounds from the scan error, Wilson intervals, Pinch Map, repeatable runs,
  and a 20 Hz live session.
- **Layout optimizer:** furniture moves that open a route, checked against walls and egress.
- **Scene understanding and traffic:** Cosmos Reason 2 hazard detection from keyframes, observer phones for foot
  traffic with on-device face blur, Gaussian splat export.
- **Robot catalog:** 12 commercial robots built with Tavily Search and Extract and Nemotron extraction; every numeric
  spec carries a verbatim quote from its source page (129 fields validated, 60 rejected by the quote validator).
- **Nemotron investigation loop:** Nemotron Super forms a hypothesis, runs an experiment in the simulator, reads
  the result and digs further or concludes, up to 6 experiments per robot.
- **Clearance Report:** code renders tables and verdict rules, Nemotron Ultra writes the narrative, and a fact
  checker verifies every number against the facts pack. Payback from the owner's business inputs.
- **Clearance API:** sites, captures, twins, checks, replays and live sessions, with operator, site, device and
  session tokens.
- **Web console:** site setup and join QR, twin editor, animated Clearance check view, report with fact chips,
  replay, observer page and joystick.
- **iPhone app:** marker-anchored capture with on-device face blur, upload, and AR replay of a robot mission in the
  real room; `ClearanceKit` tested in CI, unsigned IPA built on every change.
- **Proof run:** `scripts/release_proof.py` re-runs the three reference checks live and rebuilds `docs/PROOF.md`.

[Unreleased]: https://github.com/vivekyarra/Clearance/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/vivekyarra/Clearance/releases/tag/v0.1.0
