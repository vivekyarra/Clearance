# Clearance for iOS

The Clearance iPhone app does two jobs:

1. **Scan**. ARKit world tracking, plus LiDAR scene depth on Pro devices, captures keyframes in the site frame defined by the printed floor marker. Faces are blurred on the phone, coverage is checked live, and the capture uploads to the joined site in resumable ZIP parts.
2. **AR replay**. A simulated episode plays back in the real room, anchored to the same marker. Real furniture hides the robot through the twin's occluder mesh, and ARKit people occlusion hides it behind real people. Safety stops, near misses and failures appear where they happened. You can switch between the as-is layout and the layout with changes, and the screen shows the verdict for the robot: **Cleared**, **Cleared with changes** or **Not cleared**.

```
apps/ios/
├── project.yml                 XcodeGen spec (target "Clearance", bundle id dev.tabularis.clearance)
├── Clearance/                  app target (SwiftUI + ARKit + RealityKit + Vision + AVFoundation)
│   ├── App/                    app entry, settings, theme, home screen
│   ├── Join/                   QR join (AVFoundation metadata reader + paste field)
│   ├── Capture/                CaptureEngine (ARSession), FrameWriter (blur/JPEG/PNG), coverage map
│   ├── Upload/                 API client, resumable uploader, captures list
│   ├── Replay/                 loader, ReplayController (RealityKit scene), AR screen
│   └── Resources/tag36h11_id0.png   bundled marker reference image (generated)
├── ClearanceKit/               Swift package with the pure-Swift core and its XCTest suite
│   ├── Sources/ClearanceKit/   SiteFrame, KeyframeSelector, CoverageGrid, Zip, PNG, Episode, ...
│   └── Tests/ClearanceKitTests/
└── tools/make_marker.py        generates the marker PNG with cv2.aruco (DICT_APRILTAG_36h11, id 0)
```

## Build

You need a Mac with Xcode 16 or later (iOS 18 SDK; the deployment target is iOS 17.0) and an ARKit iPhone. ARKit capture and replay need a real device. The simulator builds the app, but ARKit does not run there.

```bash
brew install xcodegen
cd apps/ios
xcodegen                       # writes Clearance.xcodeproj and Clearance/Info.plist
open Clearance.xcodeproj       # Signing & Capabilities: pick your team, then Run on the iPhone
```

From the command line:

```bash
xcodebuild -project Clearance.xcodeproj -scheme Clearance -destination 'generic/platform=iOS' \
  DEVELOPMENT_TEAM=XXXXXXXXXX build
```

Unit tests for the pure-Swift core run on the Mac itself, with no device:

```bash
cd apps/ios/ClearanceKit && swift test
```

To regenerate the marker (it is checked in already), run from the repo root:

```bash
python apps/ios/tools/make_marker.py                          # bundled reference PNG, self-checked
python apps/ios/tools/make_marker.py --printable marker.png --size-m 0.16 --dpi 300
```

Print the tag, measure the outer black square with a ruler, and enter that length in **Settings → Marker** (default 16.0 cm). ARKit uses it as `ARReferenceImage.physicalWidth`. The bundled PNG is cropped exactly to the black square, so its width is the same length that cv2.aruco's `markerLength` refers to in the observer PWA.

## Permissions (Info.plist, generated from project.yml)

| Key | Why |
|---|---|
| `NSCameraUsageDescription` | ARKit scanning, the QR join screen and AR replay |
| `NSLocalNetworkUsageDescription` | uploads to and replays from a Clearance server on the LAN |
| `NSAppTransportSecurity` → `NSAllowsLocalNetworking` only | plain `http://<laptop-ip>:8000` works on the local network during a site visit; every other API host must be https (join links naming a public http host are refused). Before the first upload to a host the app shows it and asks for confirmation |
| `UIRequiredDeviceCapabilities` = `arkit` | the app is only useful with ARKit |
| `UIFileSharingEnabled`, `LSSupportsOpeningDocumentsInPlace` | capture folders can be copied off the phone with Finder or the Files app |
| `CFBundleURLTypes` → `clearance://` | `clearance://join?...` links open the app and join the site |

No microphone or photo library access is requested.

## Coordinate frames (must match `clearance_core.schemas.PoseRecord`)

* **ARKit world** is right-handed with +Y up (against gravity), in metres.
* **Marker anchor** (`ARImageAnchor.transform`) has its origin at the tag centre and the tag lying in the anchor's X-Z plane: +X points to the tag's right edge, +Z to its bottom edge, and +Y is the tag normal (up, for a tag lying on the floor).
* **Site frame**: origin at the tag centre, **+X** toward the tag's right edge, **+Y** toward its top edge (the top of the PNG), and **+Z up**. This is the cv2.aruco marker frame, so the Android observers agree with it.

The exact conversion is `SiteFrame.anchorFromSite`. The site axes, written in anchor coordinates, are:

```
site X = anchor +X      site Y = anchor -Z      site Z = anchor +Y
T_world_site = T_world_anchor · R_AS,   R_AS = [[1,0,0],[0,0,1],[0,-1,0]] (rows), translation 0
```

**Leveled mode** is the default (Settings → "Level the site frame with gravity"). It keeps the same origin and takes site +Z exactly from ARKit gravity (world +Y). Site +X is the tag's X axis projected onto the horizontal plane. When the tag lies flat, both modes agree. The method is recorded in `capture.json → anchoring.method`.

**Camera poses**: `T_world_cam` in poses.jsonl is `T_site_world · ARCamera.transform`, written as 16 row-major values. The camera axes keep the ARKit convention (x right, y up, z backward; the camera looks along -Z) relative to the **sensor-oriented image**. Saved JPEGs are not rotated, so `K` and `T_world_cam` describe the stored pixels directly.

**Intrinsics**: `K = [fx, fy, cx, cy]` comes from `ARCamera.intrinsics` (column-major: fx=m[0][0], fy=m[1][1], cx=m[2][0], cy=m[2][1]). It is scaled by `w_saved/w_sensor` and `h_saved/h_sensor` when the saved image is smaller. The default saved long side is 1440 px, set in Settings.

## Capture folder (`Documents/captures/<capture_id>/`)

| File | Content |
|---|---|
| `capture.json` | `Capture` schema: `capture_id`, `site_id`, `device{model, os, tier}`, `role: "scanner"`, `marker{family, id, size_m}`, `files`, `privacy{faces_blurred_on_device: true, …}`, `started_at`, `duration_s`. It also carries `anchoring{method, T_arkitworld_site, relocks}`, `clock{t0_uptime_s, t0_unix_s}`, `app` and `stats`; `clearance_core.schemas.Capture` keeps them as optional fields. `files.relative_depth` (optional) names a folder of precomputed relative-depth maps for T2. |
| `poses.jsonl` | one `PoseRecord` per keyframe: `t` (s since the marker lock), `frame`, `T_world_cam` (site frame), `K`, `w`, `h`, `tracking` (`normal` or `limited`) |
| `frames/000000.jpg …` | JPEG at quality 0.85, faces blurred (Vision `VNDetectFaceRectanglesRequest` + `CIGaussianBlur` on the enlarged boxes) |
| `depth/000000.png …` | T1 only: uint16 millimetres from `sceneDepth.depthMap` (256x192); 0 = invalid |
| `confidence/000000.png …` | T1 only: uint8 ARKit confidence 0/1/2 |
| `feature_points.jsonl` | `{"frame", "t", "points_world": [[x,y,z], …]}`: `ARFrame.rawFeaturePoints` in the site frame, mm precision |
| `marker_sightings.jsonl` | about 2 Hz: `t`, `T_arkitworld_marker`, `residual_m` (marker drift against the locked frame), `relocks` |
| `arkit_planes.json` | horizontal ARKit plane anchors in the site frame (for the floor cross-check in J2) |
| `upload_state.json` | local upload progress; not uploaded |

The device tier is **T1_LIDAR** when `ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth)` returns true, and **T2_VIO_MONO** otherwise.

### On-device checks during the scan

* **Marker lock**: recording starts only after 8 consecutive tracked sightings agree within 1 cm and 1.5°. After a camera interruption the scan pauses until the marker is seen again. Each re-lock is counted in `relocks`.
* **Keyframes**: a frame is saved when the camera moved more than 10 cm or turned more than 10° since the last keyframe, at most 15 fps. At most two frames are in flight to the writer at once.
* **Sharpness**: Laplacian variance of the luma plane, box-downsampled to about 480 px wide. Frames below the threshold (default 35, adjustable in Settings; the scan screen shows the live value) are skipped with a "Hold steady" prompt.
* **Tracking**: limited-tracking reasons become prompts. Keyframes taken while tracking is limited are saved with `tracking: "limited"` (J1 checks the share).
* **Speed**: smoothed motion above 1 m/s or 90°/s triggers "Slow down".
* **Coverage**: a 0.5 m top-down grid in the site frame, fed by feature points and LiDAR depth samples. A cell turns green once it has been seen from two directions at least 30° apart. The mini-map shows amber (one direction) and green cells and the phone's position, and the prompt points to the nearest one-sided area ("Area 1.8 m to your left was seen from one side only…").

## Joining a site (QR)

The console's site QR code (`GET /sites/{id}/qr.png`) encodes one URL (docs/CONTRACTS.md, "Site QR code"):

```
<web base>/join/<site_id>?api=<url-encoded API base>&name=<url-encoded site name>
```

Any phone camera opens it as the console's `/join` page. The join screen reads it with AVFoundation, or you can paste it. `SiteJoinParser` also accepts:

* App link: `clearance://join?api=<url-encoded API base>&site=site_x&name=…` (the `/join` page's "Open in the Clearance app" button).
* The API's JSON site payload (`qr` in `GET /sites/{id}`): `{"v": 1, "kind": "clearance-site", "site_id", "name", "api_base", "join_url", "marker"}`.
* A console URL without `api=`: the API base defaults to `https://host/api`, the console's proxy.

A `token`, if present, is sent as `Authorization: Bearer <token>` to the API host only.

## Coach screen

During a scan the app streams coverage and prompts to the console's coach screen (`/coach/<site_id>`). `POST /live {"site_id", "mode": "coach"}` returns the site's open coach session (or opens one), and the app connects to `WS /live/{session}?role=scanner`. Each message has the fixed form `{"type": "coach", "site_id", "coverage": {"cell_m", "origin", "width", "height", "cells"} | null, "prompt", "t", "status"?}` (`ClearanceKit/CoachMessage.swift`). Cells are row-major with row 0 = max y; 0 = unseen, 1 = one direction, 2 = two or more. Updates go out on every keyframe (at most 1 Hz) and whenever the prompt changes. The link is best effort and never blocks the scan.

## Upload protocol

The capture folder is split into **independent ZIP parts**, each a complete archive with paths relative to the capture root and about 16 MB by default. Each frame's JPEG, depth and confidence files stay in the same part, and `capture.json` is the first entry of part 0. The ZIP writer is the app's own (`ClearanceKit/Zip.swift`). JPEG and PNG files are stored uncompressed, and JSON lines are deflated with Apple's Compression framework (raw DEFLATE).

1. `POST /sites/{site_id}/captures` (`RegisterCaptureRequest.body`):

   ```json
   {"device": {"model", "os", "tier"}, "role": "scanner", "marker": {"family", "id", "size_m"},
    "n_chunks": 7, "chunking": "independent", "started_at": "…", "duration_s": 95.5,
    "privacy": {…}, "capture_files": {…capture.json "files"…}}
   ```

   The response (`201`) carries the server's `capture_id`, `complete_url` and `upload.chunks: [{"part", "url", "method", "headers", "key", "expires_in_s"}]`. The app rewrites `capture.json` with the server's `capture_id` before it builds part 0; the server also stamps `capture_id` and `site_id` into `capture.json` when it assembles the parts.
2. `PUT` each part to its `url` with the given `headers` (`Content-Type: application/zip` when none are given). The app records the SHA-256 and `ETag` of every part in `upload_state.json`. After a restart, only missing parts are sent. Network errors and 5xx responses retry with backoff. A 400 or 403 response (expired URL), or a restart, asks `POST /captures/{capture_id}/upload-urls {"parts": [pending indexes]}` for fresh URLs of the same capture; only a 404 there registers the capture again.
3. `POST /captures/{capture_id}/complete` (`complete_url`), answered with `202`. The body carries the receipts `{"capture_id", "site_id", "n_parts", "content_type", "parts": [{"index", "name", "size", "sha256", "etag"?}]}` for the server log; the server checks completeness against its own storage.

Uploads start when a scan ends (Settings → "Upload when the scan ends"). They can be paused and resumed from **Captures and uploads**. The console lists every capture of a site with its upload and processing state (`GET /sites/{site_id}/captures`).

## AR replay

* **Inputs**: an analysis id and/or batch ids, the robot from `GET /catalog/robots`, and an episode index. With an analysis id the app reads `GET /analyses/{id}`. The as-is batch is `batches[0]`, the with-changes batch is `optimizer[0].batch_ids[-1]`, the layout change is `optimizer[0].change`, and the badge is the entry in `verdicts` for the chosen robot.
* **Episodes**: `GET /sim/batches/{batch_id}/episodes/{n}`. The app accepts JSONL text, a JSON array of lines, or an object holding the array under `frames`/`lines`/`trajectory`/`episode`/`steps`. Lines follow CONTRACTS (`t`, `robot: [x, y, yaw, v]`, `peds`, `events`). Playback runs in real time by default (0.5x to 4x), with interpolation between the 10 Hz samples.
* **Anchoring**: `ARWorldTrackingConfiguration` with the marker in `detectionImages` and `maximumNumberOfTrackedImages = 1`, so the marker keeps refining the alignment while it is in view. Updates are smoothed, and "Hold alignment" freezes them. `.personSegmentationWithDepth` is enabled when supported (A12 and later).
* **Occluder**: `Twin.assets["occluder_usdz"]`, fetched from its absolute URL or, for a storage key, from `GET /sites/{id}/twin/assets/occluder.usdz?version=N`. Every material is replaced with `OcclusionMaterial`. The mesh is expected in site-frame metres with Z up; turn off Settings → "Occluder mesh is Z-up" for a Y-up export. Menu → "Show room mesh" draws it translucent so you can check the alignment.
* **Robot**: a parametric body built from `footprint_polygon_m` and `height_m` with `MeshResource.generate(from: [MeshDescriptor])`, in a neutral grey with no branding. Class A robots get a base, a slim column and three tray tiers. A dark heading marker shows +x, a soft shadow sits underneath, and a faint ring marks `safety_stop_distance_m` when the spec has it. Specs without a footprint or height use a 0.5 m × 0.5 m × 1.2 m body, and the screen says the shape is approximate.
* **Events**:
  * stop: an amber halo pulses around the robot while it is stopped.
  * near_miss: a red ring pulses out from the event point.
  * fail / collision: a red pole and ball with a floating label (`Mission stopped · blocked timeout`) stays until the episode restarts.
  * success: a green ring and a "Mission complete" label.

  Events are also marked on the timeline and announced in a short banner.
* **With changes**: switching mode loads the second episode at the same relative time. Moved furniture shows its old footprint in red, its new position as a translucent teal box, an arrow between them and a "Move <label> <id> 0.18 m" label. Removed items are shown in red and added boxes in teal.

## Device test plan

Use an iPhone 13 (T2) and, if available, an iPhone 12 Pro or later (T1). Print the marker, measure it, and tape it flat on the floor near the entrance.

1. **Join**: in the console, create a site and scan the QR. The home screen shows the site name and API host. Also paste a `clearance://join?...` link into Safari, which should open the app.
2. **Marker lock**: start a scan 1–2 m from the marker. The pill goes from "Find the marker" to "Marker locked". An RGB axes gizmo appears on the tag: red X to the tag's right, green Y to its top, blue Z up.
3. **Keyframes**: walk 3 m slowly. Keyframes increase by about 1 per 10 cm. Standing still and turning 90° adds about 9.
4. **Sharpness**: shake the phone and check that "Hold steady" appears and "Skipped (blur)" grows. Adjust the threshold in Settings until normal walking keeps nearly every frame.
5. **Tracking**: cover the lens for 2 s and check the tracking prompt. Then press the home button mid-scan and return. The scan waits for the marker ("Point the camera at the marker to continue") and continues after the re-lock.
6. **Coverage**: walk one side of a table only. Its cells stay amber, and the prompt points to them. Walk around it and the cells turn green.
7. **Face blur**: have a consenting person stand in view. Pull the folder off the phone with Finder (Files → Clearance → captures) and check that every frame with the face is blurred. `capture.json → privacy.face_boxes_blurred` should be above 0.
8. **Data checks on the laptop**: load with `clearance_core.schemas`. `Capture.model_validate_json(capture.json)` passes, and each poses.jsonl line validates as `PoseRecord`. Project a few `feature_points.jsonl` points with `K` and `T_world_cam`, and they should land on visible corners within a few pixels. A camera held 1.5 m above the marker reads about z = 1.5 in `T_world_cam`.
9. **Scale check**: place a second object 2.00 m from the marker along the tag's top direction, tape-measured. Its feature points should read y ≈ 2.00.
10. **T1**: on a LiDAR phone, `depth/` and `confidence/` exist with the same names as the frames. A depth PNG opened in Python (`cv2.IMREAD_UNCHANGED`) gives uint16 millimetre values that match a tape measure to a nearby wall within a few centimetres.
11. **Upload**: with auto-upload on, the progress bar advances part by part. Switch on airplane mode during part 2, then off, and press Resume. Only the remaining parts upload, and the console shows the capture complete. `upload_state.json` lists every part with its sha256.
12. **AR replay**: open a finished analysis.
    * Point at the marker. The robot appears on its path, and the trail and footprint line up with the furniture ("Show room mesh" helps here).
    * Walk behind a real table: the robot is hidden by it. Have a person walk in front: the robot is hidden by the person.
    * Check that stop, near-miss and failure effects appear at their timeline marks.
    * Toggle As is / With changes and check the moved-furniture overlay and the verdict badge.
    * Leave the marker's view for 30 s and check the drift, then use "Find the marker again".

## On-device verification checklist

CI (`.github/workflows/ios.yml`) builds the app as an unsigned IPA and runs the ClearanceKit tests (`swift test`) on every change to `apps/ios`. The items below need a real device and a real room:

1. **Build and unit tests (CI)**: CI builds the whole app and runs `swift test` for ClearanceKit. The XCTest suite covers the site-frame conversion, keyframe selection, coverage grid, ZIP writer and reader (plus a `/usr/bin/unzip -t` cross-check on macOS), PNG writer (decoded through ImageIO), checksums, the episode parser and clock, the mesh builder, QR parsing, the upload plan and the API payloads (`APIContractTests`).
2. **Swift concurrency**: the project uses Swift 5 language mode with `SWIFT_STRICT_CONCURRENCY=minimal`. The ARKit, AVFoundation and RealityKit callbacks are `nonisolated` and hop with `MainActor.assumeIsolated`, because they run on the main queue. Building with Xcode 15 (iOS 17 SDK) may show isolation warnings around `@StateObject` initializers. Xcode 16 does not.
3. **Image anchor axes**: confirm on device that ARKit's anchor axes match the convention above. The RGB gizmo in step 2 of the test plan shows this directly.
4. **Vision boxes**: `VNDetectFaceRectanglesRequest` runs on an image that has already been made upright (`CIImage.oriented`), then the blurred result is rotated back. Check step 7 in portrait, both landscape directions and upside down.
5. **Blur threshold**: the default of 35 needs calibrating for real rooms and light.
6. **Occluder up axis**: RealityKit's handling of the USD `upAxis` and `metersPerUnit` metadata in `occluder.usdz` from J2. Use "Show room mesh" and the Z-up setting.
7. **Rendering**: RealityKit materials and blending (`UnlitMaterial.blending = .transparent(opacity:)`), text meshes (`MeshResource.generateText`) and the label billboard orientation. Every generated mesh is double-sided, so triangle winding cannot hide faces.
8. **Session options**: people occlusion together with marker tracking on iPhone 13, and `environmentTexturing = .automatic`.
9. **API routes**: the register / upload-urls / complete payloads, `twin/assets/occluder.usdz` and the coach session are covered by the API's integration tests (`tests/integration/api/test_api_contract_routes.py`); check them once against a running API from the phone.
10. **Background time**: uploads use `beginBackgroundTask`, which gives about 30 s in the background. Long uploads continue when the app is in the foreground, and resume from the saved state otherwise.
