import ARKit
import ClearanceKit
import Combine
import Foundation
import RealityKit
import UIKit

/// AR replay of a simulated episode in the real room.
///
/// Scene graph (site frame = marker frame, +Z up, metres):
///   AnchorEntity(world: identity)
///     siteRoot            transform = T_world_site from the tracked marker (smoothed)
///       occluder          twin occluder.usdz with OcclusionMaterial (real furniture hides the robot)
///       trail             floor ribbon along the episode path
///       layout            with-changes overlay: moved/added/removed furniture
///       robot             parametric body from RobotSpec, shadow, safety ring, stop halo
///       pedestrians       simulated people as translucent columns
///       effects           near-miss pulses, failure markers, labels
@MainActor
final class ReplayController: NSObject, ObservableObject, ARSessionDelegate {
    enum Mode: String, CaseIterable, Identifiable {
        case asIs = "As is"
        case withChanges = "With changes"
        var id: String { rawValue }
    }

    struct EventMark: Identifiable, Equatable {
        let id: Int
        let progress: Double
        let kind: EpisodeEventKind
    }

    @Published var mode: Mode = .asIs { didSet { if mode != oldValue { applyMode(keepProgress: true) } } }
    @Published var isPlaying = true { didSet { clock.isPlaying = isPlaying } }
    @Published var rate: Double = 1 { didSet { clock.rate = rate } }
    @Published var showPeople = true { didSet { pedRoot.isEnabled = showPeople } }
    @Published var showTrail = true { didSet { trailRoot.isEnabled = showTrail } }
    @Published var showOccluderMesh = false { didSet { styleOccluder() } }
    @Published var alignmentLocked = false
    @Published private(set) var aligned = false
    @Published private(set) var markerTracked = false
    @Published private(set) var time: Double = 0
    @Published private(set) var duration: Double = 0
    @Published private(set) var progress: Double = 0
    @Published private(set) var lastEvent: EpisodeEvent?
    @Published private(set) var eventMarks: [EventMark] = []
    @Published private(set) var occluderLoaded = false
    @Published private(set) var sessionMessage: String?

    let arView: ARView
    let bundle: ReplayBundle
    var hasWithChanges: Bool { bundle.withChanges != nil }

    private let worldAnchor: AnchorEntity
    private let siteRoot: Entity
    private let trailRoot: Entity
    private let layoutRoot: Entity
    private let pedRoot: Entity
    private let effectsRoot: Entity
    private let robotRoot: Entity
    private var halo: ModelEntity
    private var occluder: Entity?
    private var occluderModels: [(entity: Entity, count: Int)] = []
    private var pedPool: [ModelEntity] = []
    private var effects: [Effect] = []
    private var labels: [Entity] = []
    private var smoother = PoseSmoother(alpha: 0.2)
    private var clock: EpisodeClock
    private var episode: Episode
    private var lastEventTime: Double
    private var stopActive = false
    private var wallTime: Double = 0
    private var lastPublish: Double = 0
    private var updateSub: Cancellable?
    private let robotRadius: Double
    private let markerSize: Double
    private let leveled: Bool
    private let occluderZUp: Bool

    private final class Effect {
        let entity: Entity
        let model: ModelEntity?
        let born: Double
        let lifetime: Double?          // nil = stays until the episode restarts
        let color: UIColor
        let grow: (from: Float, to: Float)?
        init(entity: Entity, model: ModelEntity?, born: Double, lifetime: Double?, color: UIColor, grow: (from: Float, to: Float)?) {
            self.entity = entity; self.model = model; self.born = born; self.lifetime = lifetime
            self.color = color; self.grow = grow
        }
    }

    init(bundle: ReplayBundle) {
        self.bundle = bundle
        episode = bundle.asIs
        clock = EpisodeClock(start: bundle.asIs.startTime, end: bundle.asIs.endTime)
        lastEventTime = bundle.asIs.startTime - 1e-6
        robotRadius = bundle.robot.footprint.map { simd_length($0) }.max() ?? 0.35
        let d = UserDefaults.standard
        markerSize = max(0.02, d.double(forKey: SettingsKey.markerSizeM))
        leveled = d.bool(forKey: SettingsKey.leveledAnchoring)
        occluderZUp = d.bool(forKey: SettingsKey.occluderZUp)
        arView = ARView(frame: .zero, cameraMode: .ar, automaticallyConfigureSession: false)
        worldAnchor = AnchorEntity(world: SIMD3<Float>(0, 0, 0))
        siteRoot = Entity()
        trailRoot = Entity()
        layoutRoot = Entity()
        pedRoot = Entity()
        effectsRoot = Entity()
        robotRoot = Entity()
        halo = ModelEntity()
        super.init()
        arView.session.delegate = self
        arView.renderOptions.insert(.disableMotionBlur)
        buildScene()
    }

    // MARK: session

    func start() {
        let config = ARWorldTrackingConfiguration()
        if let ref = MarkerReference.make(physicalWidth: markerSize) {
            config.detectionImages = [ref]
            config.maximumNumberOfTrackedImages = 1
        }
        if ARWorldTrackingConfiguration.supportsFrameSemantics(.personSegmentationWithDepth) {
            config.frameSemantics.insert(.personSegmentationWithDepth)
        }
        config.environmentTexturing = .automatic
        arView.session.run(config, options: [.resetTracking, .removeExistingAnchors])
        clock.isPlaying = isPlaying
        updateSub = arView.scene.subscribe(to: SceneEvents.Update.self) { [weak self] event in
            let dt = event.deltaTime
            MainActor.assumeIsolated { self?.tick(dt: dt) }
        }
        UIApplication.shared.isIdleTimerDisabled = true
    }

    func stop() {
        updateSub?.cancel()
        updateSub = nil
        arView.session.pause()
        UIApplication.shared.isIdleTimerDisabled = false
    }

    func restartAlignment() {
        smoother.reset()
        aligned = false
        alignmentLocked = false
        siteRoot.isEnabled = false
    }

    nonisolated func session(_ session: ARSession, didAdd anchors: [ARAnchor]) {
        MainActor.assumeIsolated { self.handle(anchors) }
    }

    nonisolated func session(_ session: ARSession, didUpdate anchors: [ARAnchor]) {
        MainActor.assumeIsolated { self.handle(anchors) }
    }

    nonisolated func session(_ session: ARSession, didFailWithError error: Error) {
        let text = error.localizedDescription
        MainActor.assumeIsolated { self.sessionMessage = "AR session stopped: \(text)" }
    }

    nonisolated func sessionWasInterrupted(_ session: ARSession) {
        MainActor.assumeIsolated { self.sessionMessage = "Camera interrupted" }
    }

    nonisolated func sessionInterruptionEnded(_ session: ARSession) {
        MainActor.assumeIsolated {
            self.sessionMessage = nil
            self.restartAlignment()
        }
    }

    private func handle(_ anchors: [ARAnchor]) {
        for case let img as ARImageAnchor in anchors where img.referenceImage.name == MarkerReference.name {
            if markerTracked != img.isTracked { markerTracked = img.isTracked }
            guard img.isTracked, !alignmentLocked else { continue }
            let ws = SiteFrame.worldFromSite(worldFromAnchor: SiteFrame.double(img.transform), leveled: leveled)
            let m = smoother.update(ws)
            siteRoot.transform = Transform(matrix: SiteFrame.float(m))
            if !aligned {
                aligned = true
                siteRoot.isEnabled = true
                UINotificationFeedbackGenerator().notificationOccurred(.success)
            }
        }
    }

    // MARK: scene construction

    private func buildScene() {
        arView.scene.addAnchor(worldAnchor)
        worldAnchor.addChild(siteRoot)
        siteRoot.isEnabled = false
        for e in [trailRoot, layoutRoot, pedRoot, effectsRoot, robotRoot] { siteRoot.addChild(e) }
        loadOccluder()
        buildRobot()
        buildLayoutOverlay()
        applyMode(keepProgress: false)
    }

    private func loadOccluder() {
        guard let file = bundle.occluderFile else { return }
        do {
            let e = try Entity.load(contentsOf: file)
            if !occluderZUp {
                // Y-up export: rotate +90 degrees about X so its +Y becomes site +Z.
                e.orientation = simd_quatf(angle: .pi / 2, axis: SIMD3<Float>(1, 0, 0))
            }
            occluderModels = []
            collectModels(e)
            occluder = e
            siteRoot.addChild(e)
            occluderLoaded = true
            styleOccluder()
        } catch {
            sessionMessage = "Occluder mesh could not be opened: \(error.localizedDescription)"
        }
    }

    private func collectModels(_ e: Entity) {
        if let m = e.components[ModelComponent.self] {
            occluderModels.append((e, max(1, m.materials.count)))
        }
        for c in e.children { collectModels(c) }
    }

    /// Occlusion by default; optionally a translucent view of the mesh to check the alignment.
    private func styleOccluder() {
        for (e, n) in occluderModels {
            guard var m = e.components[ModelComponent.self] else { continue }
            if showOccluderMesh {
                m.materials = Array(repeating: MeshFactory.unlit(.clearanceTeal, opacity: 0.28), count: n)
            } else {
                m.materials = Array(repeating: OcclusionMaterial(), count: n)
            }
            e.components.set(m)
        }
    }

    private func buildRobot() {
        let shape = bundle.robot
        let meshes = MeshBuilder.robot(shape)
        robotRoot.addChild(MeshFactory.entity(meshes.body, material: MeshFactory.solid(.clearanceRobotBody, roughness: 0.45), name: "robot.body"))
        robotRoot.addChild(MeshFactory.entity(meshes.accent, material: MeshFactory.solid(.clearanceRobotAccent, roughness: 0.3), name: "robot.heading"))
        let r = robotRadius
        robotRoot.addChild(MeshFactory.entity(MeshBuilder.disc(radius: r * 1.08, z: 0.003),
                                              material: MeshFactory.unlit(.black, opacity: 0.28), name: "robot.shadow"))
        if let stop = shape.safetyStopDistance, stop > 0 {
            robotRoot.addChild(MeshFactory.entity(MeshBuilder.ring(inner: r + stop - 0.015, outer: r + stop, z: 0.004),
                                                  material: MeshFactory.unlit(.white, opacity: 0.3), name: "robot.stopzone"))
        }
        halo = MeshFactory.entity(MeshBuilder.ring(inner: r + 0.06, outer: r + 0.16, z: 0.006),
                                  material: MeshFactory.unlit(.clearanceAmber, opacity: 0.85), name: "robot.halo")
        halo.isEnabled = false
        robotRoot.addChild(halo)
    }

    private func buildLayoutOverlay() {
        guard let change = bundle.change, !change.isEmpty else { return }
        let byId = Dictionary(bundle.primitives.map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
        let after = Dictionary(change.apply(to: bundle.primitives).map { ($0.id, $0) }, uniquingKeysWith: { a, _ in a })
        func footprint(_ p: PrimitiveShape, color: UIColor, opacity: Float) -> ModelEntity {
            var flat = p
            flat.center.z = 0.01
            flat.size.z = 0.02
            return MeshFactory.entity(MeshBuilder.box(flat), material: MeshFactory.unlit(color, opacity: opacity), name: "layout.\(p.id)")
        }
        for (id, delta) in change.moves {
            guard let from = byId[id], let to = after[id] else { continue }
            layoutRoot.addChild(footprint(from, color: .clearanceRed, opacity: 0.35))
            layoutRoot.addChild(MeshFactory.entity(MeshBuilder.box(to), material: MeshFactory.unlit(.clearanceTeal, opacity: 0.42), name: "layout.to.\(id)"))
            let path = [SIMD2(from.center.x, from.center.y), SIMD2(to.center.x, to.center.y)]
            layoutRoot.addChild(MeshFactory.entity(MeshBuilder.ribbon(path, width: 0.03, z: 0.03),
                                                   material: MeshFactory.unlit(.white, opacity: 0.85), name: "layout.arrow.\(id)"))
            let dist = simd_length(SIMD2(delta.x, delta.y))
            let name = from.label.isEmpty ? id : "\(from.label) \(id)"
            let text = "Move \(name) \(String(format: "%.2f", dist)) m"
            addLabel(text, color: .clearanceTeal, at: SIMD3(to.center.x, to.center.y, to.size.z + 0.25), parent: layoutRoot)
        }
        for id in change.removed {
            guard let p = byId[id] else { continue }
            layoutRoot.addChild(footprint(p, color: .clearanceRed, opacity: 0.45))
            addLabel("Remove \(p.label.isEmpty ? id : p.label)", color: .clearanceRed,
                     at: SIMD3(p.center.x, p.center.y, p.size.z + 0.25), parent: layoutRoot)
        }
        for p in change.added {
            layoutRoot.addChild(MeshFactory.entity(MeshBuilder.box(p), material: MeshFactory.unlit(.clearanceTeal, opacity: 0.42), name: "layout.add.\(p.id)"))
        }
    }

    private func rebuildTrail() {
        for c in Array(trailRoot.children) { c.removeFromParent() }
        let path = episode.path(minStep: 0.05)
        let color: UIColor = mode == .withChanges ? .clearanceTeal : .white
        trailRoot.addChild(MeshFactory.entity(MeshBuilder.ribbon(path, width: 0.05, z: 0.002),
                                              material: MeshFactory.unlit(color, opacity: 0.45), name: "trail"))
        if let first = path.first {
            trailRoot.addChild(MeshFactory.entity(MeshBuilder.disc(radius: 0.08, z: 0.003).translated(SIMD3(Float(first.x), Float(first.y), 0)),
                                                  material: MeshFactory.unlit(color, opacity: 0.8), name: "trail.start"))
        }
    }

    // MARK: modes and playback

    func seek(toProgress p: Double) {
        let t = clock.start + p * (clock.end - clock.start)
        clock.seek(t)
        resetEffects(upTo: clock.time)
        updateActors()
        publish(force: true)
    }

    private func applyMode(keepProgress: Bool) {
        let p = keepProgress ? clock.progress : 0
        episode = (mode == .withChanges ? bundle.withChanges : nil) ?? bundle.asIs
        var c = EpisodeClock(start: episode.startTime, end: episode.endTime)
        c.isPlaying = isPlaying
        c.rate = rate
        c.seek(c.start + p * (c.end - c.start))
        clock = c
        layoutRoot.isEnabled = mode == .withChanges
        duration = episode.duration
        eventMarks = episode.events.enumerated().compactMap { i, e in
            guard [.stop, .nearMiss, .collision, .fail, .success].contains(e.kind), episode.duration > 0 else { return nil }
            return EventMark(id: i, progress: (e.t - episode.startTime) / episode.duration, kind: e.kind)
        }
        rebuildTrail()
        resetEffects(upTo: clock.time)
        updateActors()
        publish(force: true)
    }

    private func resetEffects(upTo t: Double) {
        for e in effects { e.entity.removeFromParent() }
        effects.removeAll()
        labels.removeAll { $0.scene == nil }
        stopActive = false
        halo.isEnabled = false
        lastEventTime = t
        // Persistent markers for outcomes that already happened.
        for e in episode.events where e.t <= t && (e.kind == .fail || e.kind == .collision) {
            spawn(e, animate: false)
        }
    }

    private func tick(dt: Double) {
        wallTime += dt
        clock.advance(dt)
        if clock.time + 1e-9 < lastEventTime {
            // Looped back to the start.
            resetEffects(upTo: clock.start - 1e-6)
        }
        for e in episode.events(after: lastEventTime, upTo: clock.time) { spawn(e, animate: true) }
        lastEventTime = clock.time
        if clock.isPlaying != isPlaying { isPlaying = clock.isPlaying }
        updateActors()
        animateEffects()
        billboardLabels()
        publish(force: false)
    }

    private func updateActors() {
        let r = episode.robot(at: clock.time)
        robotRoot.transform = Transform(scale: SIMD3<Float>(repeating: 1),
                                        rotation: simd_quatf(angle: Float(r.yaw), axis: SIMD3<Float>(0, 0, 1)),
                                        translation: SIMD3<Float>(Float(r.x), Float(r.y), 0))
        if stopActive && r.v > 0.1 && clock.time - (lastStopTime ?? -1) > 1.0 { stopActive = false }
        halo.isEnabled = stopActive
        if stopActive {
            let s = Float(1 + 0.06 * sin(wallTime * 6))
            halo.scale = SIMD3<Float>(s, s, 1)
        }

        let peds = showPeople ? episode.pedestrians(at: clock.time) : []
        while pedPool.count < peds.count {
            let e = MeshFactory.entity(MeshBuilder.cylinder(radius: 0.2, z0: 0.0, z1: 1.7, segments: 20).doubleSided(),
                                       material: MeshFactory.unlit(UIColor(white: 0.95, alpha: 1), opacity: 0.32), name: "ped")
            pedRoot.addChild(e)
            pedPool.append(e)
        }
        for (i, e) in pedPool.enumerated() {
            if i < peds.count {
                e.isEnabled = true
                e.position = SIMD3<Float>(Float(peds[i].x), Float(peds[i].y), 0)
            } else {
                e.isEnabled = false
            }
        }
    }

    private var lastStopTime: Double?

    private func spawn(_ e: EpisodeEvent, animate: Bool) {
        let xy = e.xy ?? SIMD2(episode.robot(at: e.t).x, episode.robot(at: e.t).y)
        switch e.kind {
        case .stop:
            stopActive = true
            lastStopTime = e.t
        case .nearMiss:
            let ring = MeshFactory.entity(MeshBuilder.ring(inner: 0.85, outer: 1.0, z: 0.01),
                                          material: MeshFactory.unlit(.clearanceRed, opacity: 0.9), name: "fx.nearmiss")
            ring.position = SIMD3<Float>(Float(xy.x), Float(xy.y), 0)
            effectsRoot.addChild(ring)
            effects.append(Effect(entity: ring, model: ring, born: wallTime, lifetime: 1.3, color: .clearanceRed, grow: (0.25, 0.9)))
        case .collision, .fail:
            let marker = Entity()
            marker.position = SIMD3<Float>(Float(xy.x), Float(xy.y), 0)
            marker.addChild(MeshFactory.entity(MeshBuilder.disc(radius: 0.22, z: 0.008), material: MeshFactory.unlit(.clearanceRed, opacity: 0.55), name: "fx.fail.disc"))
            marker.addChild(MeshFactory.entity(MeshBuilder.cylinder(radius: 0.025, z0: 0, z1: 1.2, segments: 16).doubleSided(),
                                               material: MeshFactory.solid(.clearanceRed, roughness: 0.4), name: "fx.fail.pole"))
            let ball = ModelEntity(mesh: .generateSphere(radius: 0.07), materials: [MeshFactory.solid(.clearanceRed, roughness: 0.3)])
            ball.position = SIMD3<Float>(0, 0, 1.27)
            marker.addChild(ball)
            effectsRoot.addChild(marker)
            effects.append(Effect(entity: marker, model: nil, born: wallTime, lifetime: nil, color: .clearanceRed, grow: nil))
            let detail = e.detail.map { " · " + $0.replacingOccurrences(of: "_", with: " ") } ?? ""
            let label = addLabel(e.title + detail, color: .clearanceRed, at: SIMD3(xy.x, xy.y, 1.5), parent: marker, localToParent: false)
            effects.append(Effect(entity: label, model: nil, born: wallTime, lifetime: nil, color: .clearanceRed, grow: nil))
        case .success:
            guard animate else { return }
            let ring = MeshFactory.entity(MeshBuilder.ring(inner: 0.85, outer: 1.0, z: 0.01),
                                          material: MeshFactory.unlit(.clearanceGreen, opacity: 0.9), name: "fx.success")
            ring.position = SIMD3<Float>(Float(xy.x), Float(xy.y), 0)
            effectsRoot.addChild(ring)
            effects.append(Effect(entity: ring, model: ring, born: wallTime, lifetime: 1.6, color: .clearanceGreen, grow: (0.3, 1.2)))
            let label = addLabel(e.title, color: .clearanceGreen, at: SIMD3(xy.x, xy.y, bundle.robot.height + 0.35), parent: effectsRoot)
            effects.append(Effect(entity: label, model: nil, born: wallTime, lifetime: 3, color: .clearanceGreen, grow: nil))
        case .replan:
            guard animate else { return }
            let ring = MeshFactory.entity(MeshBuilder.ring(inner: 0.9, outer: 1.0, z: 0.01),
                                          material: MeshFactory.unlit(.clearanceTeal, opacity: 0.7), name: "fx.replan")
            ring.position = SIMD3<Float>(Float(xy.x), Float(xy.y), 0)
            effectsRoot.addChild(ring)
            effects.append(Effect(entity: ring, model: ring, born: wallTime, lifetime: 0.8, color: .clearanceTeal, grow: (0.2, 0.6)))
        case .other:
            break
        }
        if animate && e.kind != .other && e.kind != .replan { lastEvent = e }
    }

    private func animateEffects() {
        var keep: [Effect] = []
        for fx in effects {
            let age = wallTime - fx.born
            if let life = fx.lifetime, age > life {
                fx.entity.removeFromParent()
                continue
            }
            if let life = fx.lifetime, let model = fx.model, let grow = fx.grow {
                let u = Float(min(1, age / life))
                let s = grow.from + (grow.to - grow.from) * (1 - (1 - u) * (1 - u))
                model.scale = SIMD3<Float>(s, s, 1)
                model.model?.materials = [MeshFactory.unlit(fx.color, opacity: max(0.02, 0.9 * (1 - u)))]
            }
            keep.append(fx)
        }
        effects = keep
    }

    // MARK: labels

    /// Floating text plate; `at` is in the site frame (or in the parent's frame when localToParent).
    @discardableResult
    private func addLabel(_ text: String, color: UIColor, at p: SIMD3<Double>, parent: Entity, localToParent: Bool = true) -> Entity {
        let root = Entity()
        let mesh = MeshResource.generateText(text, extrusionDepth: 0.001,
                                             font: .systemFont(ofSize: 0.06, weight: .semibold),
                                             containerFrame: .zero, alignment: .center, lineBreakMode: .byTruncatingTail)
        let b = mesh.bounds
        let textEntity = ModelEntity(mesh: mesh, materials: [UnlitMaterial(color: .white)])
        textEntity.position = SIMD3<Float>(-b.center.x, -b.center.y, 0.004)
        let w = b.extents.x + 0.08, h = b.extents.y + 0.05
        let plate = ModelEntity(mesh: .generatePlane(width: w, height: h, cornerRadius: min(w, h) * 0.35),
                                materials: [MeshFactory.unlit(color.withAlphaComponent(1), opacity: 0.88)])
        root.addChild(plate)
        root.addChild(textEntity)
        parent.addChild(root)
        if localToParent {
            root.position = SIMD3<Float>(Float(p.x), Float(p.y), Float(p.z))
        } else {
            // `p` is in site coordinates: convert into the parent's frame.
            root.setPosition(SIMD3<Float>(Float(p.x), Float(p.y), Float(p.z)), relativeTo: siteRoot)
        }
        labels.append(root)
        return root
    }

    /// Keeps every label upright and turned toward the camera.
    private func billboardLabels() {
        labels.removeAll { $0.scene == nil }
        guard aligned else { return }
        let cam = arView.cameraTransform.translation
        for l in labels where l.isEnabledInHierarchy {
            let p = l.position(relativeTo: nil)
            var z = cam - p
            z.y = 0
            let len = simd_length(z)
            guard len > 1e-4 else { continue }
            z /= len
            let y = SIMD3<Float>(0, 1, 0)
            let x = simd_cross(y, z)
            let m = simd_float4x4(columns: (SIMD4<Float>(x, 0), SIMD4<Float>(y, 0), SIMD4<Float>(z, 0), SIMD4<Float>(p, 1)))
            l.setTransformMatrix(m, relativeTo: nil)
        }
    }

    private func publish(force: Bool) {
        guard force || wallTime - lastPublish > 0.1 else { return }
        lastPublish = wallTime
        time = clock.time - clock.start
        progress = clock.progress
    }
}

extension MeshData {
    func translated(_ d: SIMD3<Float>) -> MeshData {
        var m = self
        m.positions = positions.map { $0 + d }
        return m
    }
}
