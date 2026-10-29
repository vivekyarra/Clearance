# Privacy and consent

Clearance films real places: cafés, storerooms, offices. Real places have people in them. This page says what we capture, what never leaves the phone, what we publish, and how we get permission. It implements the privacy terms of the hackathon's Official Rules (docs/VERIFIED.md 6.9 and 6.10).

## Principles

1. **Ask first.** We scan or record a site only with the owner's written permission.
2. **Faces stay on the phone.** Faces are blurred on the device before a frame is saved or uploaded. Unblurred frames are never uploaded.
3. **Collect only what the analysis needs.** The product needs geometry and how people move through the space. It does not need who they are.
4. **Publish aggregates only.** Published sites contain twins, blurred imagery and aggregate traffic heatmaps.

## Consent

Before any capture at a site we collect a signed permission from the site owner or manager that covers:

- scanning the space and recording traffic on the agreed date and time window;
- processing the capture on Nebius AI Cloud and Token Factory;
- publishing the processed twin, blurred imagery, aggregate heatmaps and the Clearance Report in the public repository, the hosted app and the demo video;
- whether the site's name may be shown (if not, the site is published under a neutral label such as "Café A").

The signed forms are stored by the team outside the repository. They are never committed.

People who appear identifiably in the demo video (for example a site owner on camera) sign a separate appearance release. Everyone else is blurred.

## Signage during traffic recording

While observer phones record, a printed sign is placed at each entrance and next to each observer phone:

> Camera recording for a research project. Faces are blurred on the device. Ask us if you prefer not to be recorded.

Anyone who asks not to be recorded is respected: we pause the recording while they are present, or end the session.

## What each device captures

| Device | Captures | Blurred on device | Leaves the phone |
|---|---|---|---|
| iPhone scanner (iOS app) | Keyframes (JPEG), camera poses and intrinsics, ARKit feature points, LiDAR depth on Pro models | Yes: Apple Vision face detection, Gaussian blur on face boxes before a keyframe is written | Blurred keyframes, poses, depth |
| Android fixed observer (web page) | Low-frame-rate video of the space from a fixed stand, 10–20 stills | Yes: MediaPipe Tasks Face Detector in the browser, blur drawn on a canvas, only the canvas is recorded | Blurred video and stills |
| Coach phone | Nothing | n/a | Nothing |

Scanner operators avoid people where possible: we scan before opening hours or in quiet periods.

If face detection misses a face (profile views, motion blur, partial occlusion), the frame can still contain an identifiable person. Mitigations: observers record at low resolution from above head height; the published imagery is reviewed by a team member before release, and any frame with a visible unblurred face is removed or blurred by hand.

## What the cloud receives and keeps

- **Object Storage (Nebius, S3-compatible):** blurred captures, twins, simulation results and reports, in a private bucket. Uploads use short-lived presigned URLs; there are no public object ACLs.
- **Traffic modeling (J5):** person detection runs on blurred video and produces anonymous floor-plane tracks (positions over time). No identity, appearance or face features are extracted or stored. Tracks are reduced to heatmaps and origin-destination counts for the simulator.
- **Language and vision models:** Nemotron models on Token Factory receive text only: extracted web pages, site measurements and simulation facts. The scene model (Cosmos Reason 2 on our own Nebius endpoint, or Nemotron 3.5 Super VL on Token Factory) receives a small set of blurred keyframes to describe hazards such as cables or glass doors.
- **Tavily:** receives search queries about robot models and vendors only. No site data or imagery is sent.

## What we publish

`sample_sites/` and the hosted app contain only:

- the twin (occupancy grid, primitives, waypoints, measured gaps), mesh and splat built from blurred frames;
- blurred keyframes selected for the viewer;
- aggregate traffic heatmaps and origin-destination counts (no individual tracks);
- the Clearance Report and its facts.

Raw captures, individual tracks, observer video and consent forms are never published or committed. `.gitignore` excludes `data/`.

## Retention and deletion

- Raw uploads for a site are deleted from the bucket after the twin and the published artifacts are produced and reviewed, and at the latest 30 days after the hackathon judging period ends.
- A site owner can ask us to remove their site at any time; we delete the bucket prefix and remove the published sample within 7 days.

## Rules compliance

The Official Rules require that submissions do not violate anyone's privacy or publicity rights and that entrants have permission for third-party content (docs/VERIFIED.md 6.9). The consent forms, signage, on-device blurring and aggregate-only publishing above are how Clearance meets that requirement. The rules also allow the organizers to approve edits that remove personally identifiable information after submission; we would use that path if a missed face is found.

## Scope

- This policy covers the demo sites captured by the team during the hackathon. A production deployment would add a data processing agreement with each customer and region-specific terms.
- On-device face blurring depends on the detector's recall; the human review step above covers the cases it misses.
