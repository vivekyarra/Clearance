import Foundation
import simd

/// Sharpness check: variance of the 4-neighbour Laplacian on a box-downsampled luma image.
/// Downsampling first keeps the cost low (about 480x360 samples per check) and makes the score
/// less sensitive to sensor noise. The threshold is a setting; calibrate it on the device.
public enum BlurMetric {
    /// Box-average `factor` x `factor` blocks of an 8-bit plane (e.g. the Y plane of ARKit's
    /// 420f capturedImage). `bytesPerRow` may include row padding.
    public static func downsample(base: UnsafeRawPointer, width: Int, height: Int, bytesPerRow: Int,
                                  factor: Int) -> (pixels: [UInt8], width: Int, height: Int) {
        let f = max(1, factor)
        let ow = width / f, oh = height / f
        guard ow > 0, oh > 0 else { return ([], 0, 0) }
        var out = [UInt8](repeating: 0, count: ow * oh)
        let src = base.assumingMemoryBound(to: UInt8.self)
        let area = f * f
        out.withUnsafeMutableBufferPointer { dst in
            for oy in 0..<oh {
                for ox in 0..<ow {
                    var sum = 0
                    for dy in 0..<f {
                        let row = src + (oy * f + dy) * bytesPerRow + ox * f
                        for dx in 0..<f { sum += Int(row[dx]) }
                    }
                    dst[oy * ow + ox] = UInt8(sum / area)
                }
            }
        }
        return (out, ow, oh)
    }

    public static func downsample(_ pixels: [UInt8], width: Int, height: Int, factor: Int)
        -> (pixels: [UInt8], width: Int, height: Int) {
        pixels.withUnsafeBytes { buf in
            downsample(base: buf.baseAddress!, width: width, height: height, bytesPerRow: width, factor: factor)
        }
    }

    /// Factor that brings `width` down to roughly `targetWidth`.
    public static func factor(width: Int, targetWidth: Int = 480) -> Int {
        max(1, Int((Double(width) / Double(max(1, targetWidth))).rounded()))
    }

    public static func laplacianVariance(_ px: [UInt8], width: Int, height: Int) -> Double {
        guard width >= 3, height >= 3, px.count >= width * height else { return 0 }
        var sum = 0.0, sumSq = 0.0
        px.withUnsafeBufferPointer { p in
            for y in 1..<(height - 1) {
                let r = y * width
                for x in 1..<(width - 1) {
                    let c = Int(p[r + x])
                    let l = Int(p[r + x - 1]) + Int(p[r + x + 1]) + Int(p[r - width + x]) + Int(p[r + width + x]) - 4 * c
                    let d = Double(l)
                    sum += d
                    sumSq += d * d
                }
            }
        }
        let n = Double((width - 2) * (height - 2))
        let mean = sum / n
        return max(0, sumSq / n - mean * mean)
    }
}

/// Pinhole intrinsics in pixels for an image of `width` x `height`.
/// Pixel coordinates use the top-left corner of the image as the origin with v pointing down,
/// as ARKit's `ARCamera.intrinsics` does, so resizing scales fx, fy, cx, cy by the size ratio.
public struct Intrinsics: Equatable, Codable, Sendable {
    public var fx: Double, fy: Double, cx: Double, cy: Double
    public var width: Int, height: Int

    public init(fx: Double, fy: Double, cx: Double, cy: Double, width: Int, height: Int) {
        self.fx = fx; self.fy = fy; self.cx = cx; self.cy = cy
        self.width = width; self.height = height
    }

    /// From ARKit's column-major 3x3 (fx = m[0][0], fy = m[1][1], cx = m[2][0], cy = m[2][1]).
    public init(columnMajor m: simd_float3x3, width: Int, height: Int) {
        self.init(fx: Double(m[0][0]), fy: Double(m[1][1]), cx: Double(m[2][0]), cy: Double(m[2][1]),
                  width: width, height: height)
    }

    public func scaled(toWidth w: Int, height h: Int) -> Intrinsics {
        let sx = Double(w) / Double(width), sy = Double(h) / Double(height)
        return Intrinsics(fx: fx * sx, fy: fy * sy, cx: cx * sx, cy: cy * sy, width: w, height: h)
    }

    /// [fx, fy, cx, cy] as stored in poses.jsonl `K`.
    public var array: [Double] { [fx, fy, cx, cy] }

    /// Saved image size for a long-side cap (0 or >= the sensor long side keeps the sensor size).
    public static func savedSize(sensorWidth w: Int, sensorHeight h: Int, maxLongSide: Int) -> (width: Int, height: Int) {
        let long = max(w, h)
        guard maxLongSide > 0, maxLongSide < long else { return (w, h) }
        let s = Double(maxLongSide) / Double(long)
        return (max(1, Int((Double(w) * s).rounded())), max(1, Int((Double(h) * s).rounded())))
    }
}

/// Pinhole geometry in the ARKit camera convention: x right, y up, z backward (the camera looks
/// along -Z); image u grows to the right and v grows downward.
public enum CameraGeometry {
    public static func unproject(u: Double, v: Double, depth: Double, k: Intrinsics) -> SIMD3<Double> {
        SIMD3((u - k.cx) / k.fx * depth, -(v - k.cy) / k.fy * depth, -depth)
    }

    /// Returns nil for points behind the camera.
    public static func project(_ p: SIMD3<Double>, k: Intrinsics) -> SIMD2<Double>? {
        let d = -p.z
        guard d > 1e-6 else { return nil }
        return SIMD2(k.fx * p.x / d + k.cx, -k.fy * p.y / d + k.cy)
    }

    /// Samples a depth map on a regular grid and returns camera-space points (metres).
    /// `confidence` (same size, ARKit levels 0/1/2) filters samples below `minConfidence`.
    public static func depthSamples(depth: [Float], width: Int, height: Int, confidence: [UInt8]?,
                                    minConfidence: UInt8 = 1, k: Intrinsics, step: Int = 16,
                                    maxDepth: Float = 5) -> [SIMD3<Double>] {
        guard width > 0, height > 0, depth.count == width * height else { return [] }
        // Intrinsics of the depth map: the full image intrinsics scaled to the depth resolution.
        let kd = k.scaled(toWidth: width, height: height)
        var out: [SIMD3<Double>] = []
        var y = step / 2
        while y < height {
            var x = step / 2
            while x < width {
                let i = y * width + x
                let d = depth[i]
                if d.isFinite, d > 0.1, d < maxDepth, (confidence.map { $0[i] >= minConfidence } ?? true) {
                    out.append(unproject(u: Double(x) + 0.5, v: Double(y) + 0.5, depth: Double(d), k: kd))
                }
                x += step
            }
            y += step
        }
        return out
    }
}

/// EXIF-style orientation (same cases and meaning as CGImagePropertyOrientation) that makes the
/// sensor image upright, derived from where world-up points in the camera image.
public enum UprightOrientation: String, Sendable {
    case up, down, left, right

    /// `g` = world up expressed in ARKit camera coordinates (x right, y up of the sensor image).
    /// `.right` (EXIF 6) means the content's top is the image's left edge, so the image must be
    /// rotated 90 degrees clockwise for display; that is the usual case for a phone held upright.
    public static func from(worldUpInCamera g: SIMD3<Double>) -> UprightOrientation {
        if abs(g.y) >= abs(g.x) { return g.y >= 0 ? .up : .down }
        return g.x < 0 ? .right : .left
    }

    public var inverse: UprightOrientation {
        switch self {
        case .up: return .up
        case .down: return .down
        case .left: return .right
        case .right: return .left
        }
    }
}
