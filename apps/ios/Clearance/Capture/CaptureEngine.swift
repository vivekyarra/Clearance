import ARKit
import ClearanceKit
import Combine
import Foundation
import RealityKit
import UIKit

/// Scanner capture: ARKit world tracking (+ LiDAR scene depth when available),
/// marker anchoring into the site frame, keyframe selection, blur and tracking checks, coverage
/// grid and prompts. Heavy per-keyframe work (face blur, JPEG, PNG) runs on `FrameWriter`'s queue.
@MainActor
final class CaptureEngine: NSObject, ObservableObject, ARSessionDelegate {
    enum Phase: Equatable {
        case preview
        case findingMarker
        case recording
        case finishing
        case finished(URL)
        case failed(String)
    }

    enum PromptLevel { case info, warning, critical }

    struct Prompt: Equatable {
        var text: String
        var level: PromptLevel
    }

    // Published UI state
    @Published private(set) var phase: Phase = .preview
    @Published private(set) var markerVisible = false
    @Published private(set) var markerResidualCM: Double?
    @Published private(set) var trackingText: String?
    @Published private(set) var prompt: Prompt?
    @Published private(set) var keyframes = 0
    @Published private(set) var rejectedBlur = 0
    @Published private(set) var sharpness: Double = 0
    @Published private(set) var coverage = CoverageGrid()
    @Published private(set) var cameraSite: SIMD3<Double>?
    @Published private(set) var headingSite: Double = 0
    @Published private(set) var elapsed: TimeInterval = 0
    @Published private(set) var markerCheck: String?
    @Published private(set) var facesBlurred = 0

    let arView: ARView
    let tier = DeviceInfo.tier

    // Settings (read when a capture starts)
    private var markerSize = SettingsDefaults.markerSizeM
    private var maxLongSide = SettingsDefaults.maxLongSide
    private var blurThreshold = SettingsDefaults.blurThreshold
    private var leveled = SettingsDefaults.leveledAnchoring

    // Capture state
    private var site: SiteJoin?
    private var captureDir: URL?
    private var captureId = ""
    private var writer: FrameWriter?
    private var selector = KeyframeSelector()
    private var motion = MotionMonitor()
    private var lock = MarkerLock()
    private var grid = CoverageGrid()
    private var worldFromSite: Mat4?
    private var siteFromWorld: Mat4?
    private var needsRelock = false
    private var relocks = 0
    private var t0Uptime: TimeInterval?
    private var t0Unix: TimeInterval = 0
    private var startedAt = Date()
    private var nextIndex = 0
    private var pendingWrites = 0
    private var droppedBusy = 0
    private var limitedKeyframes = 0
    private var lastFrameTime: TimeInterval = 0
    private var lastSightingLog: TimeInterval = 0
    private var lastCoveragePublish: TimeInterval = 0
    private var lastCoveragePrompt: (text: String, time: TimeInterval)?
    private var latestCamera: SIMD3<Double>?
    private var latestHeading: Double = 0
    private var transientPrompt: (prompt: Prompt, until: TimeInterval)?
    private var originGizmo: AnchorEntity?

    override init() {
        arView = ARView(frame: .zero, cameraMode: .ar, automaticallyConfigureSession: false)
        super.init()
        arView.renderOptions.insert(.disableMotionBlur)
        arView.session.delegate = self
    }

    // MARK: lifecycle

    func startPreview() {
        let d = UserDefaults.standard
        markerSize = d.double(forKey: SettingsKey.markerSizeM)
        maxLongSide = d.integer(forKey: SettingsKey.maxLongSide)
        blurThreshold = d.double(forKey: SettingsKey.blurThreshold)
        leveled = d.bool(forKey: SettingsKey.leveledAnchoring)
        if markerSize < 0.02 { markerSize = SettingsDefaults.markerSizeM }

        let config = ARWorldTrackingConfiguration()
        config.planeDetection = [.horizontal]
        if let ref = MarkerReference.make(physicalWidth: markerSize) {
            config.detectionImages = [ref]
            config.maximumNumberOfTrackedImages = 1
            MarkerReference.validate(ref) { [weak self] text in self?.markerCheck = text }
        } else {
            markerCheck = "Marker image missing from the app bundle."
        }
        if ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) {
            config.frameSemantics.insert(.sceneDepth)
        }
        arView.session.run(config, options: [.resetTracking, .removeExistingAnchors])
        UIApplication.shared.isIdleTimerDisabled = true
    }

    func pause() {
        arView.session.pause()
        UIApplication.shared.isIdleTimerDisabled = false
    }

    /// Creates the capture folder and waits for a stable marker sighting to lock the site frame.
    func begin(site: SiteJoin, store: CaptureStore) {
        guard phase == .preview || isTerminal else { return }
        do {
            captureId = CaptureManifest.newCaptureId()
            let dir = try store.newCaptureDirectory(captureId: captureId)
            writer = try FrameWriter(dir: dir)
            captureDir = dir
        } catch {
            phase = .failed("Could not create the capture folder: \(error.localizedDescription)")
            return
        }
        self.site = site
        selector = KeyframeSelector()
        motion = MotionMonitor()
        lock = MarkerLock()
        grid = CoverageGrid()
        coverage = grid
        worldFromSite = nil
        siteFromWorld = nil
        needsRelock = false
        relocks = 0
        t0Uptime = nil
        nextIndex = 0
        pendingWrites = 0
        droppedBusy = 0
        limitedKeyframes = 0
        keyframes = 0
        rejectedBlur = 0
        facesBlurred = 0
        elapsed = 0
        startedAt = Date()
        originGizmo?.removeFromParent()
        originGizmo = nil
        phase = .findingMarker
    }

    func stop() {
        switch phase {
        case .findingMarker:
            // Nothing recorded yet: discard the empty folder.
            if let dir = captureDir { try? FileManager.default.removeItem(at: dir) }
            writer = nil
            phase = .preview
        case .recording:
            phase = .finishing
            finalizeWhenIdle()
        default:
            break
        }
    }

    func reset() {
        if isTerminal { phase = .preview }
    }

    private var isTerminal: Bool {
        switch phase {
        case .finished, .failed: return true
        default: return false
        }
    }

    private func finalizeWhenIdle() {
        guard pendingWrites == 0 else {
            DispatchQueue.main.asyncAfter(deadline: .now() + 0.1) { [weak self] in
                MainActor.assumeIsolated { self?.finalizeWhenIdle() }
            }
            return
        }
        guard let writer, let dir = captureDir, let site else { return }
        let planes = planeRecords()
        writer.finish { [weak self] summary in
            guard let self else { return }
            do {
                try self.writeManifest(dir: dir, site: site, summary: summary, planes: planes)
                self.phase = .finished(dir)
            } catch {
                self.phase = .failed("Could not write capture.json: \(error.localizedDescription)")
            }
            self.writer = nil
        }
    }

    private func writeManifest(dir: URL, site: SiteJoin, summary: WriterSummary, planes: Data?) throws {
        if let planes { try planes.write(to: dir.appendingPathComponent("arkit_planes.json"), options: .atomic) }
        var stats = CaptureStats()
        stats.keyframes = summary.keyframes
        stats.rejectedBlur = rejectedBlur
        stats.limitedTrackingFrames = limitedKeyframes
        stats.coveredCells = grid.coveredCount
        stats.seenCells = grid.seenCount
        var files = CaptureFiles()
        if tier == .lidar {
            files.depth = "depth"
            files.confidence = "confidence"
        } else {
            try? FileManager.default.removeItem(at: dir.appendingPathComponent("depth"))
            try? FileManager.default.removeItem(at: dir.appendingPathComponent("confidence"))
        }
        let iso = ISO8601DateFormatter()
        iso.formatOptions = [.withInternetDateTime, .withFractionalSeconds]
        let manifest = CaptureManifest(
            captureId: captureId, siteId: site.siteId,
            device: CaptureDevice(model: DeviceInfo.modelIdentifier, os: DeviceInfo.osVersion, tier: tier),
            role: "scanner",
            marker: CaptureMarker(family: "tag36h11", id: 0, sizeM: markerSize),
            files: files,
            privacy: CapturePrivacy(facesBlurredOnDevice: true, faceDetector: "VNDetectFaceRectanglesRequest",
                                    faceBoxesBlurred: summary.faceBoxes),
            startedAt: iso.string(from: startedAt),
            durationS: t0Uptime.map { max(0, lastFrameTime - $0) } ?? elapsed,
            anchoring: worldFromSite.map {
                CaptureAnchoring(method: leveled ? "arkit_image_leveled" : "arkit_image",
                                 tWorldSite: SiteFrame.rowMajor($0), relocks: relocks)
            },
            clock: t0Uptime.map { CaptureClock(t0Uptime: $0, t0Unix: t0Unix) },
            app: ["name": "Clearance", "version": DeviceInfo.appVersion,
                  "writer_messages": summary.errors.prefix(5).joined(separator: " | ")],
            stats: stats)
        try CaptureStore.writeManifest(manifest, to: dir)
    }

    /// Horizontal ARKit plane anchors in the site frame (recon cross-checks the floor with them).
    private func planeRecords() -> Data? {
        guard let sfw = siteFromWorld, let anchors = arView.session.currentFrame?.anchors else { return nil }
        var out: [[String: Any]] = []
        for case let p as ARPlaneAnchor in anchors where p.alignment == .horizontal {
            let t = sfw * SiteFrame.double(p.transform)
            let c = SiteFrame.transform(t, SIMD3<Double>(p.center))
            out.append(["id": p.identifier.uuidString,
                        "T_site_plane": SiteFrame.rowMajor(t),
                        "center_site": [c.x, c.y, c.z],
                        "extent_m": [Double(p.planeExtent.width), Double(p.planeExtent.height)],
                        "classification": "\(p.classification)"])
        }
        return try? JSONSerialization.data(withJSONObject: ["planes": out], options: [.prettyPrinted, .sortedKeys])
    }

    // MARK: ARSessionDelegate (delivered on the main queue)

    nonisolated func session(_ session: ARSession, didUpdate frame: ARFrame) {
        MainActor.assumeIsolated { self.handle(frame: frame) }
    }

    nonisolated func session(_ session: ARSession, didAdd anchors: [ARAnchor]) {
        MainActor.assumeIsolated { self.handle(anchors: anchors) }
    }

    nonisolated func session(_ session: ARSession, didUpdate anchors: [ARAnchor]) {
        MainActor.assumeIsolated { self.handle(anchors: anchors) }
    }

    nonisolated func session(_ session: ARSession, cameraDidChangeTrackingState camera: ARCamera) {
        let state = camera.trackingState
        MainActor.assumeIsolated { self.updateTracking(state) }
    }

    nonisolated func sessionWasInterrupted(_ session: ARSession) {
        MainActor.assumeIsolated {
            self.flash("Camera interrupted. The scan resumes when the marker is seen again.", .critical, seconds: 4)
        }
    }

    nonisolated func sessionInterruptionEnded(_ session: ARSession) {
        MainActor.assumeIsolated {
            if self.phase == .recording {
                self.needsRelock = true
                self.lock.reset()
            }
        }
    }

    nonisolated func sessionShouldAttemptRelocalization(_ session: ARSession) -> Bool { true }

    nonisolated func session(_ session: ARSession, didFailWithError error: Error) {
        let text = error.localizedDescription
        MainActor.assumeIsolated { self.phase = .failed("AR session stopped: \(text)") }
    }

    // MARK: per-frame logic

    private func updateTracking(_ state: ARCamera.TrackingState) {
        switch state {
        case .normal: trackingText = nil
        case .notAvailable: trackingText = "Tracking unavailable"
        case .limited(let reason):
            switch reason {
            case .initializing: trackingText = "Starting tracking: move the phone slowly"
            case .excessiveMotion: trackingText = "Slow down: the phone is moving too fast"
            case .insufficientFeatures: trackingText = "Low detail: aim at textured surfaces or add light"
            case .relocalizing: trackingText = "Relocalizing: return to an area you already scanned"
            @unknown default: trackingText = "Tracking limited"
            }
        @unknown default: trackingText = "Tracking limited"
        }
    }

    private func handle(anchors: [ARAnchor]) {
        for case let img as ARImageAnchor in anchors where img.referenceImage.name == MarkerReference.name {
            if markerVisible != img.isTracked { markerVisible = img.isTracked }
            guard img.isTracked else { continue }
            let wa = SiteFrame.double(img.transform)
            let ws = SiteFrame.worldFromSite(worldFromAnchor: wa, leveled: leveled)
            switch phase {
            case .findingMarker:
                if let locked = lock.add(ws) { lockSite(locked, relock: false) }
            case .recording:
                if needsRelock {
                    if let locked = lock.add(ws) { lockSite(locked, relock: true) }
                } else if let cur = worldFromSite {
                    let r = simd_distance(SiteFrame.translation(ws), SiteFrame.translation(cur)) * 100
                    if abs(r - (markerResidualCM ?? -1)) > 0.2 { markerResidualCM = r }
                }
                logSighting(worldFromAnchor: wa)
            default:
                break
            }
        }
    }

    private func lockSite(_ ws: Mat4, relock: Bool) {
        worldFromSite = ws
        siteFromWorld = SiteFrame.rigidInverse(ws)
        if !relock {
            t0Uptime = arView.session.currentFrame?.timestamp ?? lastFrameTime
            t0Unix = Date().timeIntervalSince1970
            startedAt = Date()
            phase = .recording
        } else {
            relocks += 1
            needsRelock = false
            // Keyframe spacing restarts from the new frame.
            selector.reset()
        }
        markerResidualCM = 0
        showOriginGizmo(ws)
        UINotificationFeedbackGenerator().notificationOccurred(.success)
        flash(relock ? "Marker found again. Scan continues." : "Marker locked. Walk the space slowly.", .info, seconds: 3)
    }

    private func logSighting(worldFromAnchor wa: Mat4) {
        guard let writer, let t0 = t0Uptime else { return }
        let t = lastFrameTime - t0
        guard t - lastSightingLog > 0.5 else { return }
        lastSightingLog = t
        let obj: [String: Any] = ["t": t, "T_arkitworld_marker": SiteFrame.rowMajor(wa),
                                  "residual_m": (markerResidualCM ?? 0) / 100, "relocks": relocks]
        if var d = try? JSONSerialization.data(withJSONObject: obj, options: [.sortedKeys]) {
            d.append(0x0A)
            writer.appendSighting(d)
        }
    }

    private func handle(frame: ARFrame) {
        lastFrameTime = frame.timestamp
        let worldFromCam = SiteFrame.double(frame.camera.transform)
        if let t0 = t0Uptime, phase == .recording, frame.timestamp - t0 - elapsed >= 0.5 { elapsed = frame.timestamp - t0 }
        defer { refreshPrompt(now: frame.timestamp) }

        guard phase == .recording, let sfw = siteFromWorld, let writer, let t0 = t0Uptime else { return }
        let siteCam = sfw * worldFromCam
        let t = frame.timestamp - t0
        latestCamera = SiteFrame.translation(siteCam)
        latestHeading = SiteFrame.viewingYaw(siteFromCamera: siteCam)
        if frame.timestamp - lastCoveragePublish > 0.25 {
            lastCoveragePublish = frame.timestamp
            cameraSite = latestCamera
            headingSite = latestHeading
            coverage = grid
        }

        if motion.update(time: t, pose: siteCam) {
            flash("Slow down: walk and turn more slowly", .warning, seconds: 1.2)
        }
        let trackingName: String
        switch frame.camera.trackingState {
        case .normal: trackingName = "normal"
        case .limited: trackingName = "limited"
        case .notAvailable: return
        @unknown default: trackingName = "limited"
        }
        guard !needsRelock else { return }
        guard selector.isCandidate(time: t, pose: siteCam) else { return }
        guard pendingWrites < 2 else { droppedBusy += 1; return }

        let sharp = PixelBufferTools.sharpness(frame.capturedImage)
        sharpness = sharp
        if sharp < blurThreshold {
            rejectedBlur += 1
            flash("Hold steady: a blurry frame was skipped", .warning, seconds: 1.0)
            return
        }
        selector.commit(time: t, pose: siteCam)

        let cam = frame.camera
        let sensorW = Int(cam.imageResolution.width), sensorH = Int(cam.imageResolution.height)
        let kSensor = Intrinsics(columnMajor: cam.intrinsics, width: sensorW, height: sensorH)
        let saved = Intrinsics.savedSize(sensorWidth: sensorW, sensorHeight: sensorH, maxLongSide: maxLongSide)
        let kSaved = kSensor.scaled(toWidth: saved.width, height: saved.height)
        let orientation = UprightOrientation.from(worldUpInCamera: SiteFrame.worldUpInCamera(worldFromCamera: worldFromCam))

        let featureSite = (frame.rawFeaturePoints?.points ?? []).map { SiteFrame.transform(sfw, SIMD3<Double>($0)) }

        var depth: (values: [Float], width: Int, height: Int)?
        var conf: (values: [UInt8], width: Int, height: Int)?
        var depthSite: [SIMD3<Double>] = []
        if tier == .lidar, let sd = frame.sceneDepth {
            depth = PixelBufferTools.copy(sd.depthMap, as: Float.self)
            if let cm = sd.confidenceMap { conf = PixelBufferTools.copy(cm, as: UInt8.self) }
            if let d = depth {
                let local = CameraGeometry.depthSamples(depth: d.values, width: d.width, height: d.height,
                                                        confidence: conf?.values, k: kSensor, step: 16)
                depthSite = local.map { SiteFrame.transform(siteCam, $0) }
            }
        }
        grid.observe(cameraPosition: SiteFrame.translation(siteCam), points: featureSite + depthSite)

        let index = nextIndex
        nextIndex += 1
        if trackingName == "limited" { limitedKeyframes += 1 }
        let name = CaptureNaming.frameName(index)
        let pose = PoseLine(t: t, frame: name, tWorldCam: SiteFrame.rowMajor(siteCam), k: kSaved.array,
                            w: saved.width, h: saved.height, tracking: trackingName)
        let job = KeyframeJob(index: index, pixelBuffer: frame.capturedImage, orientation: orientation,
                              outWidth: saved.width, outHeight: saved.height, pose: pose,
                              featureLine: JSONLines.featurePointsLine(frame: name, t: t, points: featureSite),
                              depth: depth, confidence: conf)
        pendingWrites += 1
        writer.enqueue(job) { [weak self] result in
            guard let self else { return }
            self.pendingWrites -= 1
            switch result {
            case .success(let r):
                self.keyframes += 1
                self.facesBlurred += r.faces
            case .failure(let e):
                self.flash("Could not save a frame: \(e.localizedDescription)", .critical, seconds: 3)
            }
        }
    }

    // MARK: prompts and visuals

    private func flash(_ text: String, _ level: PromptLevel, seconds: TimeInterval) {
        let p = Prompt(text: text, level: level)
        transientPrompt = (p, lastFrameTime + seconds)
        setPrompt(p)
    }

    /// Publishes only real changes (this runs every AR frame).
    private func setPrompt(_ p: Prompt?) {
        if prompt != p { prompt = p }
    }

    private func refreshPrompt(now: TimeInterval) {
        if let tp = transientPrompt, now < tp.until {
            setPrompt(tp.prompt)
            return
        }
        transientPrompt = nil
        switch phase {
        case .findingMarker:
            setPrompt(Prompt(text: markerVisible ? "Hold still on the marker…" : "Point the camera at the marker on the floor",
                             level: .info))
            return
        case .recording:
            break
        default:
            setPrompt(nil)
            return
        }
        if needsRelock {
            setPrompt(Prompt(text: "Point the camera at the marker to continue", level: .critical))
            return
        }
        if let text = trackingText {
            setPrompt(Prompt(text: text, level: .warning))
            return
        }
        if let last = lastCoveragePrompt, now - last.time < 4 {
            setPrompt(Prompt(text: last.text, level: .info))
            return
        }
        if let cam = latestCamera, let p = grid.prompt(cameraPosition: cam, heading: latestHeading) {
            lastCoveragePrompt = (p.text, now)
            setPrompt(Prompt(text: p.text, level: .info))
        } else {
            setPrompt(nil)
        }
    }

    /// Small axes gizmo at the site origin so the user sees the lock (red X, green Y, blue Z).
    private func showOriginGizmo(_ ws: Mat4) {
        originGizmo?.removeFromParent()
        let anchor = AnchorEntity(world: SiteFrame.float(ws))
        let s = markerSize
        let plate = MeshFactory.entity(MeshBuilder.prism(PolygonMath.rectangle(center: .zero, length: s, width: s, yaw: 0),
                                                         z0: 0.001, z1: 0.004).doubleSided(),
                                       material: MeshFactory.unlit(.clearanceGreen, opacity: 0.45), name: "plate")
        anchor.addChild(plate)
        let len = 0.25, th = 0.008
        let x = MeshBuilder.prism(PolygonMath.rectangle(center: SIMD2(len / 2, 0), length: len, width: th, yaw: 0), z0: 0.005, z1: 0.005 + th)
        let y = MeshBuilder.prism(PolygonMath.rectangle(center: SIMD2(0, len / 2), length: th, width: len, yaw: 0), z0: 0.005, z1: 0.005 + th)
        let z = MeshBuilder.prism(PolygonMath.rectangle(center: .zero, length: th, width: th, yaw: 0), z0: 0.005, z1: len)
        anchor.addChild(MeshFactory.entity(x.doubleSided(), material: MeshFactory.unlit(.systemRed), name: "x"))
        anchor.addChild(MeshFactory.entity(y.doubleSided(), material: MeshFactory.unlit(.systemGreen), name: "y"))
        anchor.addChild(MeshFactory.entity(z.doubleSided(), material: MeshFactory.unlit(.systemBlue), name: "z"))
        arView.scene.addAnchor(anchor)
        originGizmo = anchor
    }
}
