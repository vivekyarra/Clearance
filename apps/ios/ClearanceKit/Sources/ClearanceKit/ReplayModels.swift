import Foundation
import simd

/// The parts of `RobotSpec` the AR replay needs to build the parametric robot.
public struct RobotShape: Equatable, Sendable {
    public var robotId: String
    public var robotClass: String          // "A_service" | "B_amr"
    public var footprint: [SIMD2<Double>]  // centred on the rotation centre, +x forward
    public var height: Double
    public var footprintApprox: Bool
    public var heightKnown: Bool
    public var safetyStopDistance: Double?

    public init(robotId: String, robotClass: String, footprint: [SIMD2<Double>], height: Double,
                footprintApprox: Bool, heightKnown: Bool, safetyStopDistance: Double? = nil) {
        self.robotId = robotId; self.robotClass = robotClass; self.footprint = footprint
        self.height = height; self.footprintApprox = footprintApprox; self.heightKnown = heightKnown
        self.safetyStopDistance = safetyStopDistance
    }

    /// Default body when the spec leaves the footprint or height empty: 0.5 m square, 1.2 m tall,
    /// shown with an "approximate shape" note in the UI.
    public static func standard(robotId: String = "robot") -> RobotShape {
        RobotShape(robotId: robotId, robotClass: "B_amr",
                   footprint: [SIMD2(0.25, 0.25), SIMD2(-0.25, 0.25), SIMD2(-0.25, -0.25), SIMD2(0.25, -0.25)],
                   height: 1.2, footprintApprox: true, heightKnown: false)
    }

    /// From a RobotSpec JSON object (catalog entry).
    public static func from(json: [String: Any]) -> RobotShape {
        let id = json["robot_id"] as? String ?? "robot"
        let cls = json["class"] as? String ?? json["robot_class"] as? String ?? "B_amr"
        var shape = RobotShape.standard(robotId: id)
        shape.robotClass = cls
        if let poly = json["footprint_polygon_m"] as? [Any] {
            let pts = poly.compactMap { Episode.xy($0) }
            if pts.count >= 3, abs(PolygonMath.signedArea(pts)) > 1e-4 {
                shape.footprint = pts
                shape.footprintApprox = (json["footprint_approx"] as? Bool) ?? false
            }
        }
        if let h = Episode.num(json["height_m"]), h > 0.05, h < 5 {
            shape.height = h
            shape.heightKnown = true
        }
        shape.safetyStopDistance = Episode.num(json["safety_stop_distance_m"])
        return shape
    }
}

/// Verdict as decided by clearance_core/verdict.py and returned with the analysis.
public struct VerdictBadge: Equatable, Sendable {
    public enum Label: String, Sendable {
        case cleared = "Cleared"
        case clearedWithChanges = "Cleared with changes"
        case notCleared = "Not cleared"
    }
    public var robotId: String
    public var label: Label
    public var mainReason: String

    public init(robotId: String, label: Label, mainReason: String) {
        self.robotId = robotId; self.label = label; self.mainReason = mainReason
    }

    public static func parse(_ d: [String: Any]) -> VerdictBadge? {
        guard let rid = d["robot_id"] as? String, let l = d["label"] as? String,
              let label = Label(rawValue: l) else { return nil }
        return VerdictBadge(robotId: rid, label: label, mainReason: d["main_reason"] as? String ?? "")
    }
}

/// Twin primitive (box / cylinder) for the layout overlay.
public struct PrimitiveShape: Equatable, Sendable {
    public var id: String
    public var type: String
    public var label: String
    public var center: SIMD3<Double>   // pose.xyz (site frame)
    public var yaw: Double
    public var size: SIMD3<Double>     // box: l (along yaw), w, h ; cylinder: d, d, h
    public var movable: Bool

    public init(id: String, type: String, label: String, center: SIMD3<Double>, yaw: Double,
                size: SIMD3<Double>, movable: Bool) {
        self.id = id; self.type = type; self.label = label; self.center = center
        self.yaw = yaw; self.size = size; self.movable = movable
    }

    public static func parse(_ d: [String: Any]) -> PrimitiveShape? {
        guard let id = d["id"] as? String,
              let pose = d["pose"] as? [String: Any],
              let xyz = pose["xyz"] as? [Any], xyz.count >= 3,
              let x = Episode.num(xyz[0]), let y = Episode.num(xyz[1]), let z = Episode.num(xyz[2]),
              let size = d["size"] as? [Any], size.count >= 3,
              let l = Episode.num(size[0]), let w = Episode.num(size[1]), let h = Episode.num(size[2]) else { return nil }
        return PrimitiveShape(id: id, type: d["type"] as? String ?? "box", label: d["label"] as? String ?? "",
                              center: SIMD3(x, y, z), yaw: Episode.num(pose["yaw"]) ?? 0,
                              size: SIMD3(l, w, h), movable: d["movable"] as? Bool ?? false)
    }

    public static func parseAll(twin: [String: Any]) -> [PrimitiveShape] {
        (twin["primitives"] as? [[String: Any]] ?? []).compactMap { parse($0) }
    }
}

/// Optimizer change in the overrides format:
/// {"move": {obj_id: {"dx","dy","dyaw"}}, "remove": [obj_id], "add_boxes": [Primitive...]}.
public struct LayoutChange: Equatable, Sendable {
    public var moves: [String: SIMD3<Double>]   // dx, dy, dyaw
    public var removed: [String]
    public var added: [PrimitiveShape]
    public var description: String

    public init(moves: [String: SIMD3<Double>] = [:], removed: [String] = [], added: [PrimitiveShape] = [],
                description: String = "") {
        self.moves = moves; self.removed = removed; self.added = added; self.description = description
    }

    public var isEmpty: Bool { moves.isEmpty && removed.isEmpty && added.isEmpty }

    public static func parse(_ d: [String: Any], description: String = "") -> LayoutChange {
        var c = LayoutChange(description: description)
        for (id, v) in d["move"] as? [String: Any] ?? [:] {
            guard let m = v as? [String: Any] else { continue }
            c.moves[id] = SIMD3(Episode.num(m["dx"]) ?? 0, Episode.num(m["dy"]) ?? 0, Episode.num(m["dyaw"]) ?? 0)
        }
        c.removed = d["remove"] as? [String] ?? []
        c.added = (d["add_boxes"] as? [[String: Any]] ?? []).compactMap { PrimitiveShape.parse($0) }
        return c
    }

    /// Primitives after the change is applied (moved ones are translated and rotated about z).
    public func apply(to prims: [PrimitiveShape]) -> [PrimitiveShape] {
        var out: [PrimitiveShape] = []
        for var p in prims where !removed.contains(p.id) {
            if let m = moves[p.id] {
                p.center.x += m.x
                p.center.y += m.y
                p.yaw += m.z
            }
            out.append(p)
        }
        return out + added
    }
}

/// What the replay screen needs from `GET /analyses/{id}`: the as-is batch, the batch run with
/// the optimizer's change, that change, and the verdicts. Tolerant of missing pieces.
public struct AnalysisReplayRefs: Equatable, Sendable {
    public var beforeBatchId: String?
    public var afterBatchId: String?
    public var change: LayoutChange?
    public var verdicts: [VerdictBadge]

    public init(beforeBatchId: String? = nil, afterBatchId: String? = nil, change: LayoutChange? = nil,
                verdicts: [VerdictBadge] = []) {
        self.beforeBatchId = beforeBatchId; self.afterBatchId = afterBatchId
        self.change = change; self.verdicts = verdicts
    }

    public static func parse(_ d: [String: Any], robotId: String? = nil) -> AnalysisReplayRefs {
        var r = AnalysisReplayRefs()
        let batches = (d["batches"] as? [Any] ?? []).compactMap { b -> String? in
            if let s = b as? String { return s }
            return (b as? [String: Any])?["batch_id"] as? String
        }
        r.beforeBatchId = batches.first
        if let opt = (d["optimizer"] as? [[String: Any]])?.first
            ?? ((d["optimizer"] as? [String: Any])?["results"] as? [[String: Any]])?.first {
            if let ch = opt["change"] as? [String: Any] {
                r.change = LayoutChange.parse(ch, description: opt["description"] as? String ?? "")
            }
            let ids = opt["batch_ids"] as? [String] ?? []
            r.afterBatchId = ids.last
        }
        r.verdicts = (d["verdicts"] as? [[String: Any]] ?? []).compactMap { VerdictBadge.parse($0) }
        if let rid = robotId { r.verdicts = r.verdicts.filter { $0.robotId == rid } }
        return r
    }
}
