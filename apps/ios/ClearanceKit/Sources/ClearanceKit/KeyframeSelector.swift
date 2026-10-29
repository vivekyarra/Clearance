import Foundation
import simd

/// Keyframe rule: save when the camera moved more than 10 cm or rotated more than 10
/// degrees since the last saved keyframe, never faster than 15 fps.
///
/// `isCandidate` does not change state; call `commit` once the frame passed the blur check and
/// was handed to the writer, so a rejected frame keeps the next one eligible.
public struct KeyframeSelector {
    public var minTranslation: Double
    public var minRotation: Double
    public var minInterval: Double
    public private(set) var lastPose: Mat4?
    public private(set) var lastTime: Double?
    public private(set) var count = 0

    public init(minTranslation: Double = 0.10, minRotationDeg: Double = 10, maxFPS: Double = 15) {
        self.minTranslation = minTranslation
        self.minRotation = minRotationDeg * .pi / 180
        self.minInterval = 1.0 / maxFPS
    }

    public func isCandidate(time: Double, pose: Mat4) -> Bool {
        guard let lp = lastPose, let lt = lastTime else { return true }
        if time - lt < minInterval - 1e-9 { return false }
        if simd_distance(SiteFrame.translation(pose), SiteFrame.translation(lp)) > minTranslation { return true }
        return SiteFrame.rotationAngle(lp, pose) > minRotation
    }

    public mutating func commit(time: Double, pose: Mat4) {
        lastPose = pose
        lastTime = time
        count += 1
    }

    public mutating func reset() {
        lastPose = nil
        lastTime = nil
        count = 0
    }
}

/// Smoothed camera speed, used for the "slow down" prompt.
public struct MotionMonitor {
    public var maxLinearSpeed: Double
    public var maxAngularSpeed: Double
    public var smoothing: Double
    public private(set) var linearSpeed: Double = 0
    public private(set) var angularSpeed: Double = 0
    private var last: (t: Double, pose: Mat4)?

    public init(maxLinearSpeed: Double = 1.0, maxAngularSpeedDeg: Double = 90, smoothing: Double = 0.3) {
        self.maxLinearSpeed = maxLinearSpeed
        self.maxAngularSpeed = maxAngularSpeedDeg * .pi / 180
        self.smoothing = smoothing
    }

    /// Returns true when the smoothed motion is faster than the limits.
    @discardableResult
    public mutating func update(time: Double, pose: Mat4) -> Bool {
        defer { last = (time, pose) }
        guard let l = last else { return false }
        let dt = time - l.t
        guard dt > 1e-4 else { return isTooFast }
        let v = simd_distance(SiteFrame.translation(pose), SiteFrame.translation(l.pose)) / dt
        let w = SiteFrame.rotationAngle(l.pose, pose) / dt
        linearSpeed += smoothing * (v - linearSpeed)
        angularSpeed += smoothing * (w - angularSpeed)
        return isTooFast
    }

    public var isTooFast: Bool { linearSpeed > maxLinearSpeed || angularSpeed > maxAngularSpeed }

    public mutating func reset() {
        last = nil
        linearSpeed = 0
        angularSpeed = 0
    }
}
