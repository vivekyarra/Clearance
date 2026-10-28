import Foundation
import simd

public typealias Mat4 = simd_double4x4

@inline(__always) func v3(_ c: SIMD4<Double>) -> SIMD3<Double> { SIMD3(c.x, c.y, c.z) }

/// Site frame conversion (PLAN Section 6, `PoseRecord` in clearance_core/schemas.py).
///
/// Frames involved:
/// * ARKit world (`W`): right-handed, +Y up (opposite gravity), metres, origin where the session started.
/// * Marker anchor (`A`, ARImageAnchor.transform): origin at the centre of the printed tag, the tag
///   lies in the anchor's X-Z plane, +X = towards the tag's right edge, +Z = towards the tag's bottom
///   edge, +Y = tag normal (pointing out of the printed face, i.e. up for a tag lying on the floor).
/// * Site (`S`): origin at the tag centre, +X = tag right edge, +Y = tag top edge, +Z = up.
///   This equals the cv2.aruco marker frame used by the observer PWA (X right, Y up the tag, Z out).
///
/// Exact mapping, anchor coordinates of the site axes:
///   site X = anchor +X,  site Y = anchor -Z,  site Z = anchor +Y.
/// so for a point p: p_A = R_AS * p_S with R_AS columns (1,0,0), (0,0,-1), (0,1,0).
///
/// Leveled mode (default) keeps the same origin but takes site +Z exactly from ARKit gravity
/// (world +Y) and site +X from the tag's X axis projected onto the horizontal plane. Gravity from
/// the IMU is steadier than the image-anchor tilt, and the two agree when the tag lies flat.
///
/// Camera poses: `T_site_cam = T_site_world * ARCamera.transform`. The camera axes stay in ARKit
/// convention (x right, y up, z backward; looks along -Z) relative to the sensor image, which is
/// what poses.jsonl stores (row-major 4x4).
public enum SiteFrame {
    public static let anchorFromSite = Mat4(columns: (
        SIMD4(1, 0, 0, 0),
        SIMD4(0, 0, -1, 0),
        SIMD4(0, 1, 0, 0),
        SIMD4(0, 0, 0, 1)))

    /// T_world_site: maps site coordinates to ARKit world coordinates.
    public static func worldFromSite(worldFromAnchor a: Mat4, leveled: Bool) -> Mat4 {
        if !leveled { return a * anchorFromSite }
        let origin = v3(a.columns.3)
        let up = SIMD3<Double>(0, 1, 0)
        let tagX = v3(a.columns.0)
        var x = SIMD3(tagX.x, 0, tagX.z)
        if simd_length(x) < 0.1 {
            // Tag X axis is nearly vertical (tag on a wall): derive X from the tag's top direction.
            let tagTop = -v3(a.columns.2)
            let yh = SIMD3(tagTop.x, 0, tagTop.z)
            let yn = simd_length(yh) > 1e-9 ? simd_normalize(yh) : SIMD3<Double>(0, 0, -1)
            x = simd_cross(yn, up)
        }
        x = simd_normalize(x)
        let y = simd_cross(up, x)
        return Mat4(columns: (SIMD4(x, 0), SIMD4(y, 0), SIMD4(up, 0), SIMD4(origin, 1)))
    }

    /// T_site_world.
    public static func siteFromWorld(worldFromAnchor a: Mat4, leveled: Bool) -> Mat4 {
        let ws = worldFromSite(worldFromAnchor: a, leveled: leveled)
        return leveled ? rigidInverse(ws) : ws.inverse
    }

    /// T_site_cam = T_site_world * T_world_cam.
    public static func siteFromCamera(siteFromWorld: Mat4, worldFromCamera: Mat4) -> Mat4 {
        siteFromWorld * worldFromCamera
    }

    public static func rigidInverse(_ m: Mat4) -> Mat4 {
        let r = simd_double3x3(v3(m.columns.0), v3(m.columns.1), v3(m.columns.2))
        let rt = r.transpose
        let t = -(rt * v3(m.columns.3))
        return Mat4(columns: (SIMD4(rt.columns.0, 0), SIMD4(rt.columns.1, 0), SIMD4(rt.columns.2, 0), SIMD4(t, 1)))
    }

    public static func transform(_ m: Mat4, _ p: SIMD3<Double>) -> SIMD3<Double> {
        v3(m * SIMD4(p, 1))
    }

    public static func rotate(_ m: Mat4, _ v: SIMD3<Double>) -> SIMD3<Double> {
        v3(m * SIMD4(v, 0))
    }

    public static func translation(_ m: Mat4) -> SIMD3<Double> { v3(m.columns.3) }

    public static func rotation(_ m: Mat4) -> simd_double3x3 {
        simd_double3x3(v3(m.columns.0), v3(m.columns.1), v3(m.columns.2))
    }

    /// Row-major 16 values (the order used by poses.jsonl `T_world_cam`).
    public static func rowMajor(_ m: Mat4) -> [Double] {
        var out = [Double]()
        out.reserveCapacity(16)
        for r in 0..<4 { for c in 0..<4 { out.append(m[c][r]) } }
        return out
    }

    public static func fromRowMajor(_ a: [Double]) -> Mat4? {
        guard a.count == 16 else { return nil }
        var m = Mat4()
        for r in 0..<4 { for c in 0..<4 { m[c][r] = a[r * 4 + c] } }
        return m
    }

    public static func double(_ m: simd_float4x4) -> Mat4 {
        Mat4(columns: (SIMD4<Double>(m.columns.0), SIMD4<Double>(m.columns.1),
                       SIMD4<Double>(m.columns.2), SIMD4<Double>(m.columns.3)))
    }

    public static func float(_ m: Mat4) -> simd_float4x4 {
        simd_float4x4(columns: (SIMD4<Float>(m.columns.0), SIMD4<Float>(m.columns.1),
                                SIMD4<Float>(m.columns.2), SIMD4<Float>(m.columns.3)))
    }

    /// Angle (radians) of the relative rotation between two poses.
    public static func rotationAngle(_ a: Mat4, _ b: Mat4) -> Double {
        let rel = rotation(a).transpose * rotation(b)
        let tr = rel[0][0] + rel[1][1] + rel[2][2]
        return acos(min(1, max(-1, (tr - 1) / 2)))
    }

    /// Heading (yaw about site +Z) of the camera's viewing direction (-Z axis), radians.
    public static func viewingYaw(siteFromCamera m: Mat4) -> Double {
        let fwd = -v3(m.columns.2)
        return atan2(fwd.y, fwd.x)
    }

    /// World up (+Y in ARKit world) expressed in camera coordinates.
    public static func worldUpInCamera(worldFromCamera m: Mat4) -> SIMD3<Double> {
        rotation(m).transpose * SIMD3(0, 1, 0)
    }
}

/// Locks the site frame once the marker pose is stable over several consecutive sightings.
public struct MarkerLock {
    public var requiredSamples: Int
    public var maxPositionSpread: Double
    public var maxAngleSpread: Double
    public private(set) var samples: [Mat4] = []

    public init(requiredSamples: Int = 8, maxPositionSpread: Double = 0.01, maxAngleSpreadDeg: Double = 1.5) {
        self.requiredSamples = requiredSamples
        self.maxPositionSpread = maxPositionSpread
        self.maxAngleSpread = maxAngleSpreadDeg * .pi / 180
    }

    public mutating func reset() { samples.removeAll() }

    /// Adds a T_world_site sample; returns the averaged transform once the last `requiredSamples`
    /// samples agree within the spread limits.
    public mutating func add(_ worldFromSite: Mat4) -> Mat4? {
        samples.append(worldFromSite)
        if samples.count > requiredSamples { samples.removeFirst(samples.count - requiredSamples) }
        guard samples.count == requiredSamples else { return nil }
        let mean = MarkerLock.average(samples)
        for s in samples {
            if simd_distance(SiteFrame.translation(s), SiteFrame.translation(mean)) > maxPositionSpread { return nil }
            if SiteFrame.rotationAngle(s, mean) > maxAngleSpread { return nil }
        }
        return mean
    }

    /// Mean origin plus Gram-Schmidt of the mean X and Z axes.
    public static func average(_ ms: [Mat4]) -> Mat4 {
        precondition(!ms.isEmpty)
        var o = SIMD3<Double>(repeating: 0), x = o, z = o
        for m in ms {
            o += v3(m.columns.3); x += v3(m.columns.0); z += v3(m.columns.2)
        }
        let n = Double(ms.count)
        o /= n
        z = simd_normalize(z)
        x = simd_normalize(x - simd_dot(x, z) * z)
        let y = simd_cross(z, x)
        return Mat4(columns: (SIMD4(x, 0), SIMD4(y, 0), SIMD4(z, 0), SIMD4(o, 1)))
    }
}

/// Exponential smoothing of a rigid transform (position lerp + rotation slerp), used to keep the
/// AR replay steady while the marker anchor keeps refining.
public struct PoseSmoother {
    public var alpha: Double
    public private(set) var value: Mat4?

    public init(alpha: Double = 0.15) { self.alpha = alpha }

    public mutating func reset() { value = nil }

    /// Snaps instead of smoothing when the jump is larger than `snapDistance` metres.
    public mutating func update(_ target: Mat4, snapDistance: Double = 0.25) -> Mat4 {
        guard let cur = value,
              simd_distance(SiteFrame.translation(cur), SiteFrame.translation(target)) < snapDistance else {
            value = target
            return target
        }
        let qa = simd_quatd(SiteFrame.rotation(cur))
        let qb = simd_quatd(SiteFrame.rotation(target))
        let q = simd_slerp(qa, qb, alpha)
        let t = SiteFrame.translation(cur) + alpha * (SiteFrame.translation(target) - SiteFrame.translation(cur))
        let r = simd_double3x3(q)
        let m = Mat4(columns: (SIMD4(r.columns.0, 0), SIMD4(r.columns.1, 0), SIMD4(r.columns.2, 0), SIMD4(t, 1)))
        value = m
        return m
    }
}
