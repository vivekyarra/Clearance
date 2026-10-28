import Foundation

/// Codable mirror of clearance_core/schemas.py `Capture` (PLAN 6.1). Field names match the Python
/// models exactly; extra top-level keys (anchoring, clock, app, stats) are ignored by pydantic
/// and carry details the reconstruction jobs can use.
public enum CaptureTier: String, Codable, Sendable {
    case lidar = "T1_LIDAR"
    case vioMono = "T2_VIO_MONO"
}

public struct CaptureDevice: Codable, Equatable, Sendable {
    public var model: String
    public var os: String
    public var tier: CaptureTier
    public init(model: String, os: String, tier: CaptureTier) {
        self.model = model; self.os = os; self.tier = tier
    }
}

public struct CaptureMarker: Codable, Equatable, Sendable {
    public var family: String
    public var id: Int
    public var sizeM: Double
    enum CodingKeys: String, CodingKey { case family, id, sizeM = "size_m" }
    public init(family: String = "tag36h11", id: Int = 0, sizeM: Double = 0.160) {
        self.family = family; self.id = id; self.sizeM = sizeM
    }
}

public struct CaptureFiles: Codable, Equatable, Sendable {
    public var frames: String?
    public var poses: String?
    public var depth: String?
    public var confidence: String?
    public var featurePoints: String?
    public var video: String?
    public var observerPose: String?
    /// T2: directory of precomputed relative-depth maps named like the frames (optional).
    public var relativeDepth: String?
    enum CodingKeys: String, CodingKey {
        case frames, poses, depth, confidence, video
        case featurePoints = "feature_points"
        case observerPose = "observer_pose"
        case relativeDepth = "relative_depth"
    }
    public init(frames: String? = "frames", poses: String? = "poses.jsonl", depth: String? = nil,
                confidence: String? = nil, featurePoints: String? = "feature_points.jsonl",
                video: String? = nil, observerPose: String? = nil, relativeDepth: String? = nil) {
        self.frames = frames; self.poses = poses; self.depth = depth; self.confidence = confidence
        self.featurePoints = featurePoints; self.video = video; self.observerPose = observerPose
        self.relativeDepth = relativeDepth
    }
}

public struct CapturePrivacy: Codable, Equatable, Sendable {
    public var facesBlurredOnDevice: Bool
    public var faceDetector: String
    public var faceBoxesBlurred: Int
    enum CodingKeys: String, CodingKey {
        case facesBlurredOnDevice = "faces_blurred_on_device"
        case faceDetector = "face_detector"
        case faceBoxesBlurred = "face_boxes_blurred"
    }
    public init(facesBlurredOnDevice: Bool = true, faceDetector: String = "VNDetectFaceRectanglesRequest",
                faceBoxesBlurred: Int = 0) {
        self.facesBlurredOnDevice = facesBlurredOnDevice; self.faceDetector = faceDetector
        self.faceBoxesBlurred = faceBoxesBlurred
    }
}

public struct CaptureAnchoring: Codable, Equatable, Sendable {
    public var method: String               // "arkit_image_leveled" | "arkit_image"
    public var tWorldSite: [Double]          // row-major ARKit world <- site, at lock time
    public var relocks: Int
    enum CodingKeys: String, CodingKey {
        case method, relocks
        case tWorldSite = "T_arkitworld_site"
    }
    public init(method: String, tWorldSite: [Double], relocks: Int = 0) {
        self.method = method; self.tWorldSite = tWorldSite; self.relocks = relocks
    }
}

public struct CaptureClock: Codable, Equatable, Sendable {
    public var t0Uptime: Double     // ARFrame.timestamp of t = 0 (seconds since device boot)
    public var t0Unix: Double       // wall clock at t = 0
    enum CodingKeys: String, CodingKey { case t0Uptime = "t0_uptime_s", t0Unix = "t0_unix_s" }
    public init(t0Uptime: Double, t0Unix: Double) { self.t0Uptime = t0Uptime; self.t0Unix = t0Unix }
}

public struct CaptureStats: Codable, Equatable, Sendable {
    public var keyframes: Int = 0
    public var rejectedBlur: Int = 0
    public var limitedTrackingFrames: Int = 0
    public var coveredCells: Int = 0
    public var seenCells: Int = 0
    enum CodingKeys: String, CodingKey {
        case keyframes
        case rejectedBlur = "rejected_blur"
        case limitedTrackingFrames = "limited_tracking_keyframes"
        case coveredCells = "covered_cells"
        case seenCells = "seen_cells"
    }
    public init() {}
}

public struct CaptureManifest: Codable, Equatable, Sendable {
    public var captureId: String
    public var siteId: String
    public var device: CaptureDevice
    public var role: String
    public var marker: CaptureMarker
    public var files: CaptureFiles
    public var privacy: CapturePrivacy
    public var startedAt: String
    public var durationS: Double
    public var anchoring: CaptureAnchoring?
    public var clock: CaptureClock?
    public var app: [String: String]?
    public var stats: CaptureStats?

    enum CodingKeys: String, CodingKey {
        case device, role, marker, files, privacy, anchoring, clock, app, stats
        case captureId = "capture_id"
        case siteId = "site_id"
        case startedAt = "started_at"
        case durationS = "duration_s"
    }

    public init(captureId: String, siteId: String, device: CaptureDevice, role: String = "scanner",
                marker: CaptureMarker = CaptureMarker(), files: CaptureFiles = CaptureFiles(),
                privacy: CapturePrivacy = CapturePrivacy(), startedAt: String = "", durationS: Double = 0,
                anchoring: CaptureAnchoring? = nil, clock: CaptureClock? = nil,
                app: [String: String]? = nil, stats: CaptureStats? = nil) {
        self.captureId = captureId; self.siteId = siteId; self.device = device; self.role = role
        self.marker = marker; self.files = files; self.privacy = privacy; self.startedAt = startedAt
        self.durationS = durationS; self.anchoring = anchoring; self.clock = clock; self.app = app
        self.stats = stats
    }

    public static func newCaptureId() -> String {
        "cap_" + UUID().uuidString.replacingOccurrences(of: "-", with: "").lowercased().prefix(16)
    }
}

/// One line of poses.jsonl (clearance_core.schemas.PoseRecord).
public struct PoseLine: Codable, Equatable, Sendable {
    public var t: Double
    public var frame: String
    public var tWorldCam: [Double]
    public var k: [Double]
    public var w: Int
    public var h: Int
    public var tracking: String   // "normal" | "limited" | "not_available"

    enum CodingKeys: String, CodingKey {
        case t, frame, w, h, tracking
        case tWorldCam = "T_world_cam"
        case k = "K"
    }

    public init(t: Double, frame: String, tWorldCam: [Double], k: [Double], w: Int, h: Int, tracking: String) {
        self.t = t; self.frame = frame; self.tWorldCam = tWorldCam; self.k = k
        self.w = w; self.h = h; self.tracking = tracking
    }
}

public enum CaptureNaming {
    public static func frameStem(_ index: Int) -> String { String(format: "%06d", index) }
    public static func frameName(_ index: Int) -> String { frameStem(index) + ".jpg" }
    public static func mapName(_ index: Int) -> String { frameStem(index) + ".png" }
}

public enum JSONLines {
    public static func line<T: Encodable>(_ value: T) throws -> Data {
        let enc = JSONEncoder()
        enc.outputFormatting = [.sortedKeys, .withoutEscapingSlashes]
        var d = try enc.encode(value)
        d.append(0x0A)
        return d
    }

    /// feature_points.jsonl line: {"frame": "...", "t": ..., "points_world": [[x,y,z],...]} in the
    /// site frame, millimetre precision (written by hand: this file is large).
    public static func featurePointsLine(frame: String, t: Double, points: [SIMD3<Double>]) -> Data {
        var s = "{\"frame\":\"\(frame)\",\"t\":\(fmt(t, 4)),\"points_world\":["
        s.reserveCapacity(64 + points.count * 26)
        var first = true
        for p in points where p.x.isFinite && p.y.isFinite && p.z.isFinite {
            if !first { s += "," }
            first = false
            s += "[\(fmt(p.x, 3)),\(fmt(p.y, 3)),\(fmt(p.z, 3))]"
        }
        s += "]}\n"
        return Data(s.utf8)
    }

    static func fmt(_ v: Double, _ digits: Int) -> String {
        let r = String(format: "%.\(digits)f", v)
        return r == "-0.000" || r == "-0.0000" ? String(r.dropFirst()) : r
    }
}
