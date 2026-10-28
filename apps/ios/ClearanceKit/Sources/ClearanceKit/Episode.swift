import Foundation
import simd

/// Simulation episode for replay (CONTRACTS: Episode JSONL line @10 Hz:
/// {"t","robot":[x,y,yaw,v],"peds":[[x,y],...],"events":[{"type","xy":[x,y],"detail"}]}).
public struct RobotPose2D: Equatable, Sendable {
    public var x: Double, y: Double, yaw: Double, v: Double
    public init(x: Double, y: Double, yaw: Double, v: Double = 0) {
        self.x = x; self.y = y; self.yaw = yaw; self.v = v
    }
}

public enum EpisodeEventKind: String, Sendable, CaseIterable {
    case stop, nearMiss = "near_miss", collision, fail, success, replan, other
}

public struct EpisodeEvent: Equatable, Sendable {
    public var t: Double
    public var kind: EpisodeEventKind
    public var rawType: String
    public var xy: SIMD2<Double>?
    public var detail: String?
    public init(t: Double, kind: EpisodeEventKind, rawType: String, xy: SIMD2<Double>?, detail: String?) {
        self.t = t; self.kind = kind; self.rawType = rawType; self.xy = xy; self.detail = detail
    }

    /// Short label shown in AR next to a failure marker or in the event list.
    public var title: String {
        switch kind {
        case .stop: return "Safety stop"
        case .nearMiss: return "Near miss"
        case .collision: return "Contact"
        case .fail: return "Mission stopped"
        case .success: return "Mission complete"
        case .replan: return "Replanned"
        case .other: return rawType.replacingOccurrences(of: "_", with: " ").capitalized
        }
    }
}

public struct EpisodeFrame: Equatable, Sendable {
    public var t: Double
    public var robot: RobotPose2D
    public var peds: [SIMD2<Double>]
    public var events: [EpisodeEvent]
}

public struct Episode: Equatable, Sendable {
    public private(set) var frames: [EpisodeFrame]
    public private(set) var events: [EpisodeEvent]

    public enum ParseError: Error, Equatable { case empty, unreadable }

    public init(frames: [EpisodeFrame]) throws {
        let sorted = frames.sorted { $0.t < $1.t }
        guard !sorted.isEmpty else { throw ParseError.empty }
        self.frames = sorted
        self.events = sorted.flatMap(\.events).sorted { $0.t < $1.t }
    }

    public var startTime: Double { frames.first!.t }
    public var endTime: Double { frames.last!.t }
    public var duration: Double { endTime - startTime }

    /// Final outcome event if any (success / fail / collision).
    public var outcome: EpisodeEvent? {
        events.last(where: { [.success, .fail, .collision].contains($0.kind) })
    }

    // MARK: parsing

    /// Accepts a JSON array of lines, an object holding the array under frames/lines/trajectory/
    /// episode/steps, or JSONL text.
    public static func parse(_ data: Data) throws -> Episode {
        if let obj = try? JSONSerialization.jsonObject(with: data) {
            if let arr = obj as? [Any] { return try Episode(frames: arr.compactMap { frame($0) }) }
            if let dict = obj as? [String: Any] {
                for k in ["frames", "lines", "trajectory", "episode", "steps"] {
                    if let arr = dict[k] as? [Any] { return try Episode(frames: arr.compactMap { frame($0) }) }
                }
                if let text = dict["jsonl"] as? String { return try parse(Data(text.utf8)) }
                if let f = frame(dict) { return try Episode(frames: [f]) }
            }
        }
        guard let text = String(data: data, encoding: .utf8) else { throw ParseError.unreadable }
        var out: [EpisodeFrame] = []
        for line in text.split(whereSeparator: \.isNewline) {
            let trimmed = line.trimmingCharacters(in: .whitespaces)
            guard !trimmed.isEmpty,
                  let o = try? JSONSerialization.jsonObject(with: Data(trimmed.utf8)),
                  let f = frame(o) else { continue }
            out.append(f)
        }
        return try Episode(frames: out)
    }

    static func num(_ a: Any?) -> Double? {
        if let d = a as? Double { return d }
        if let n = a as? NSNumber { return n.doubleValue }
        if let s = a as? String { return Double(s) }
        return nil
    }

    static func xy(_ a: Any?) -> SIMD2<Double>? {
        if let arr = a as? [Any], arr.count >= 2, let x = num(arr[0]), let y = num(arr[1]) { return SIMD2(x, y) }
        if let d = a as? [String: Any], let x = num(d["x"]), let y = num(d["y"]) { return SIMD2(x, y) }
        return nil
    }

    static func frame(_ object: Any) -> EpisodeFrame? {
        guard let d = object as? [String: Any], let t = num(d["t"]) else { return nil }
        let robot: RobotPose2D
        if let arr = d["robot"] as? [Any], arr.count >= 3,
           let x = num(arr[0]), let y = num(arr[1]), let yaw = num(arr[2]) {
            robot = RobotPose2D(x: x, y: y, yaw: yaw, v: arr.count > 3 ? (num(arr[3]) ?? 0) : 0)
        } else if let r = d["robot"] as? [String: Any], let x = num(r["x"]), let y = num(r["y"]) {
            robot = RobotPose2D(x: x, y: y, yaw: num(r["yaw"]) ?? 0, v: num(r["v"]) ?? 0)
        } else {
            return nil
        }
        let peds = (d["peds"] as? [Any] ?? []).compactMap { xy($0) }
        var events: [EpisodeEvent] = []
        for e in d["events"] as? [Any] ?? [] {
            guard let ed = e as? [String: Any], let type = ed["type"] as? String else { continue }
            let kind = EpisodeEventKind(rawValue: type) ?? .other
            events.append(EpisodeEvent(t: num(ed["t"]) ?? t, kind: kind, rawType: type,
                                       xy: xy(ed["xy"]) ?? SIMD2(robot.x, robot.y), detail: detailString(ed["detail"])))
        }
        return EpisodeFrame(t: t, robot: robot, peds: peds, events: events)
    }

    static func detailString(_ a: Any?) -> String? {
        guard let a, !(a is NSNull) else { return nil }
        if let s = a as? String { return s.isEmpty ? nil : s }
        return displayValue(a)
    }

    /// Compact, human-readable rendering for AR labels: `{"d": 0.4}` becomes `d: 0.4` (shortest number form).
    static func displayValue(_ a: Any) -> String {
        if let n = a as? NSNumber {
            if CFGetTypeID(n) == CFBooleanGetTypeID() { return n.boolValue ? "true" : "false" }
            let d = n.doubleValue
            return d == d.rounded() && abs(d) < 1e15 ? String(Int64(d)) : "\(d)"
        }
        if let s = a as? String { return s }
        if let dict = a as? [String: Any] {
            return dict.keys.sorted().map { "\($0): \(displayValue(dict[$0]!))" }.joined(separator: ", ")
        }
        if let arr = a as? [Any] { return "[" + arr.map(displayValue).joined(separator: ", ") + "]" }
        return "\(a)"
    }

    // MARK: sampling

    /// Index of the last frame with t <= time (clamped).
    public func frameIndex(at time: Double) -> Int {
        var lo = 0, hi = frames.count - 1
        if time <= frames[0].t { return 0 }
        if time >= frames[hi].t { return hi }
        while lo < hi {
            let mid = (lo + hi + 1) / 2
            if frames[mid].t <= time { lo = mid } else { hi = mid - 1 }
        }
        return lo
    }

    /// Robot pose linearly interpolated between samples (yaw along the shorter arc).
    public func robot(at time: Double) -> RobotPose2D {
        if time <= frames[0].t { return frames[0].robot }
        let i = frameIndex(at: time)
        guard i + 1 < frames.count else { return frames[i].robot }
        let a = frames[i], b = frames[i + 1]
        let span = b.t - a.t
        let u = span > 1e-9 ? min(1, max(0, (time - a.t) / span)) : 0
        let dyaw = Episode.wrapAngle(b.robot.yaw - a.robot.yaw)
        return RobotPose2D(x: a.robot.x + u * (b.robot.x - a.robot.x),
                           y: a.robot.y + u * (b.robot.y - a.robot.y),
                           yaw: Episode.wrapAngle(a.robot.yaw + u * dyaw),
                           v: a.robot.v + u * (b.robot.v - a.robot.v))
    }

    /// Pedestrian positions; interpolated when the neighbouring samples have the same count.
    public func pedestrians(at time: Double) -> [SIMD2<Double>] {
        let i = frameIndex(at: time)
        guard i + 1 < frames.count, frames[i].peds.count == frames[i + 1].peds.count else { return frames[i].peds }
        let a = frames[i], b = frames[i + 1]
        let span = b.t - a.t
        let u = span > 1e-9 ? min(1, max(0, (time - a.t) / span)) : 0
        return zip(a.peds, b.peds).map { $0 + u * ($1 - $0) }
    }

    /// Events with t in (from, to]; used each display frame to trigger effects exactly once.
    public func events(after from: Double, upTo to: Double) -> [EpisodeEvent] {
        events.filter { $0.t > from && $0.t <= to }
    }

    /// Robot path as a polyline (for the floor trail), decimated to `minStep` metres.
    public func path(minStep: Double = 0.05) -> [SIMD2<Double>] {
        var out: [SIMD2<Double>] = []
        for f in frames {
            let p = SIMD2(f.robot.x, f.robot.y)
            if let l = out.last, simd_distance(l, p) < minStep { continue }
            out.append(p)
        }
        return out
    }

    public static func wrapAngle(_ a: Double) -> Double {
        var r = fmod(a + .pi, 2 * .pi)
        if r < 0 { r += 2 * .pi }
        return r - .pi
    }
}

/// Wall-clock playback state for an episode (real time by default).
public struct EpisodeClock: Equatable, Sendable {
    public var time: Double
    public var rate: Double = 1
    public var isPlaying = false
    public var loops = true
    public let start: Double
    public let end: Double

    public init(start: Double, end: Double) {
        self.start = start; self.end = end; self.time = start
    }

    /// Advances by `dt` seconds of wall time; returns the previous time.
    @discardableResult
    public mutating func advance(_ dt: Double) -> Double {
        let prev = time
        guard isPlaying else { return prev }
        time += dt * rate
        if time >= end {
            if loops { time = start } else { time = end; isPlaying = false }
        }
        return prev
    }

    public mutating func seek(_ t: Double) { time = min(max(t, start), end) }
    public var progress: Double { end > start ? (time - start) / (end - start) : 0 }
}
