import Foundation
import simd

/// Triangle mesh as plain arrays (converted to RealityKit `MeshDescriptor` in the app).
/// Coordinates are in the site frame convention: +Z up.
public struct MeshData: Equatable, Sendable {
    public var positions: [SIMD3<Float>] = []
    public var normals: [SIMD3<Float>] = []
    public var indices: [UInt32] = []

    public init() {}

    public var triangleCount: Int { indices.count / 3 }

    public mutating func append(_ o: MeshData) {
        let base = UInt32(positions.count)
        positions += o.positions
        normals += o.normals
        indices += o.indices.map { $0 + base }
    }

    /// Adds a reversed copy of every triangle (with flipped normals) so the mesh renders the
    /// same from both sides whatever the renderer's culling and winding conventions are.
    public func doubleSided() -> MeshData {
        var m = self
        let base = UInt32(positions.count)
        m.positions += positions
        m.normals += normals.map { -$0 }
        var i = 0
        while i + 2 < indices.count {
            m.indices += [indices[i] + base, indices[i + 2] + base, indices[i + 1] + base]
            i += 3
        }
        return m
    }

    public mutating func addTriangle(_ a: SIMD3<Float>, _ b: SIMD3<Float>, _ c: SIMD3<Float>, normal n: SIMD3<Float>) {
        let base = UInt32(positions.count)
        positions += [a, b, c]
        normals += [n, n, n]
        indices += [base, base + 1, base + 2]
    }

    public var bounds: (min: SIMD3<Float>, max: SIMD3<Float>)? {
        guard var lo = positions.first else { return nil }
        var hi = lo
        for p in positions { lo = simd_min(lo, p); hi = simd_max(hi, p) }
        return (lo, hi)
    }
}

public enum PolygonMath {
    public static func signedArea(_ p: [SIMD2<Double>]) -> Double {
        guard p.count >= 3 else { return 0 }
        var a = 0.0
        for i in 0..<p.count {
            let q = p[i], r = p[(i + 1) % p.count]
            a += q.x * r.y - r.x * q.y
        }
        return a / 2
    }

    /// Removes repeated and collinear vertices and returns the polygon counter-clockwise.
    public static func normalized(_ input: [SIMD2<Double>], eps: Double = 1e-9) -> [SIMD2<Double>] {
        var p: [SIMD2<Double>] = []
        for v in input where p.last.map({ simd_distance($0, v) > 1e-7 }) ?? true { p.append(v) }
        if p.count > 1, simd_distance(p[0], p[p.count - 1]) <= 1e-7 { p.removeLast() }
        var changed = true
        while changed && p.count >= 3 {
            changed = false
            for i in 0..<p.count {
                let a = p[(i + p.count - 1) % p.count], b = p[i], c = p[(i + 1) % p.count]
                if abs(cross(b - a, c - b)) <= eps { p.remove(at: i); changed = true; break }
            }
        }
        if signedArea(p) < 0 { p.reverse() }
        return p
    }

    @inline(__always) static func cross(_ a: SIMD2<Double>, _ b: SIMD2<Double>) -> Double { a.x * b.y - a.y * b.x }

    static func pointInTriangle(_ p: SIMD2<Double>, _ a: SIMD2<Double>, _ b: SIMD2<Double>, _ c: SIMD2<Double>) -> Bool {
        let d1 = cross(b - a, p - a), d2 = cross(c - b, p - b), d3 = cross(a - c, p - c)
        return d1 >= 0 && d2 >= 0 && d3 >= 0
    }

    /// Ear-clipping triangulation of a simple polygon given counter-clockwise (see `normalized`).
    /// Returns index triples into `p`, each counter-clockwise.
    public static func triangulate(_ p: [SIMD2<Double>]) -> [(Int, Int, Int)] {
        guard p.count >= 3 else { return [] }
        var idx = Array(0..<p.count)
        var tris: [(Int, Int, Int)] = []
        var guardCount = 0
        while idx.count > 3 && guardCount < 10 * p.count * p.count {
            guardCount += 1
            var clipped = false
            for k in 0..<idx.count {
                let ia = idx[(k + idx.count - 1) % idx.count], ib = idx[k], ic = idx[(k + 1) % idx.count]
                let a = p[ia], b = p[ib], c = p[ic]
                guard cross(b - a, c - b) > 1e-12 else { continue }  // reflex or degenerate
                var inside = false
                for j in idx where j != ia && j != ib && j != ic {
                    if pointInTriangle(p[j], a, b, c) { inside = true; break }
                }
                if inside { continue }
                tris.append((ia, ib, ic))
                idx.remove(at: k)
                clipped = true
                break
            }
            if !clipped { break }  // not simple; fall back to a fan for the rest
        }
        if idx.count == 3 {
            tris.append((idx[0], idx[1], idx[2]))
        } else if idx.count > 3 {
            for k in 1..<(idx.count - 1) { tris.append((idx[0], idx[k], idx[k + 1])) }
        }
        return tris
    }

    public static func regular(radius: Double, segments: Int) -> [SIMD2<Double>] {
        (0..<max(3, segments)).map { i in
            let a = 2 * Double.pi * Double(i) / Double(max(3, segments))
            return SIMD2(radius * cos(a), radius * sin(a))
        }
    }

    public static func scaled(_ p: [SIMD2<Double>], _ s: Double) -> [SIMD2<Double>] { p.map { $0 * s } }

    /// Rectangle centred at `c`, length `l` along `yaw`, width `w`, counter-clockwise.
    public static func rectangle(center c: SIMD2<Double>, length l: Double, width w: Double, yaw: Double) -> [SIMD2<Double>] {
        let u = SIMD2(cos(yaw), sin(yaw)), v = SIMD2(-sin(yaw), cos(yaw))
        let hl = l / 2, hw = w / 2
        return [c + hl * u - hw * v, c + hl * u + hw * v, c - hl * u + hw * v, c - hl * u - hw * v]
    }
}

public enum MeshBuilder {
    /// Vertical prism over a polygon from z0 to z1 with flat-shaded caps and sides.
    public static func prism(_ polygon: [SIMD2<Double>], z0: Double, z1: Double) -> MeshData {
        let p = PolygonMath.normalized(polygon)
        var m = MeshData()
        guard p.count >= 3, z1 > z0 else { return m }
        let tris = PolygonMath.triangulate(p)
        let f0 = Float(z0), f1 = Float(z1)
        func at(_ v: SIMD2<Double>, _ z: Float) -> SIMD3<Float> { SIMD3(Float(v.x), Float(v.y), z) }
        for (a, b, c) in tris {
            m.addTriangle(at(p[a], f1), at(p[b], f1), at(p[c], f1), normal: SIMD3(0, 0, 1))
            m.addTriangle(at(p[a], f0), at(p[c], f0), at(p[b], f0), normal: SIMD3(0, 0, -1))
        }
        for i in 0..<p.count {
            let a = p[i], b = p[(i + 1) % p.count]
            let e = b - a
            let len = simd_length(e)
            guard len > 1e-9 else { continue }
            let n = SIMD3(Float(e.y / len), Float(-e.x / len), 0)  // outward for counter-clockwise
            let a0 = at(a, f0), b0 = at(b, f0), b1 = at(b, f1), a1 = at(a, f1)
            m.addTriangle(a0, b0, b1, normal: n)
            m.addTriangle(a0, b1, a1, normal: n)
        }
        return m
    }

    public static func cylinder(radius: Double, z0: Double, z1: Double, segments: Int = 32) -> MeshData {
        prism(PolygonMath.regular(radius: radius, segments: segments), z0: z0, z1: z1)
    }

    /// Flat annulus at height z (double-sided), for halos and pulses.
    public static func ring(inner: Double, outer: Double, z: Double = 0, segments: Int = 48) -> MeshData {
        var m = MeshData()
        let n = max(8, segments)
        let up = SIMD3<Float>(0, 0, 1)
        for i in 0..<n {
            let a0 = 2 * Double.pi * Double(i) / Double(n), a1 = 2 * Double.pi * Double(i + 1) / Double(n)
            func pt(_ r: Double, _ a: Double) -> SIMD3<Float> { SIMD3(Float(r * cos(a)), Float(r * sin(a)), Float(z)) }
            m.addTriangle(pt(inner, a0), pt(outer, a0), pt(outer, a1), normal: up)
            m.addTriangle(pt(inner, a0), pt(outer, a1), pt(inner, a1), normal: up)
        }
        return m.doubleSided()
    }

    /// Flat disc (double-sided).
    public static func disc(radius: Double, z: Double = 0, segments: Int = 48) -> MeshData {
        var m = MeshData()
        let n = max(8, segments)
        let c = SIMD3<Float>(0, 0, Float(z))
        for i in 0..<n {
            let a0 = 2 * Double.pi * Double(i) / Double(n), a1 = 2 * Double.pi * Double(i + 1) / Double(n)
            m.addTriangle(c, SIMD3(Float(radius * cos(a0)), Float(radius * sin(a0)), Float(z)),
                          SIMD3(Float(radius * cos(a1)), Float(radius * sin(a1)), Float(z)), normal: SIMD3(0, 0, 1))
        }
        return m.doubleSided()
    }

    /// Flat ribbon along a polyline at height z (the robot's path trail on the floor).
    public static func ribbon(_ path: [SIMD2<Double>], width: Double, z: Double = 0.004) -> MeshData {
        var m = MeshData()
        guard path.count >= 2 else { return m }
        let hw = width / 2
        let up = SIMD3<Float>(0, 0, 1)
        for i in 0..<(path.count - 1) {
            let a = path[i], b = path[i + 1]
            let d = b - a
            let len = simd_length(d)
            guard len > 1e-6 else { continue }
            let nrm = SIMD2(-d.y, d.x) / len * hw
            func pt(_ v: SIMD2<Double>) -> SIMD3<Float> { SIMD3(Float(v.x), Float(v.y), Float(z)) }
            m.addTriangle(pt(a - nrm), pt(b - nrm), pt(b + nrm), normal: up)
            m.addTriangle(pt(a - nrm), pt(b + nrm), pt(a + nrm), normal: up)
        }
        return m.doubleSided()
    }

    /// Parametric robot (PLAN 7.9): footprint extrusion, neutral body, no branding.
    /// Class A (service robots with trays): base, slim column and tray tiers. Class B: a single body.
    /// Returns the body and an accent mesh (front marker showing the heading, +x).
    public static func robot(_ shape: RobotShape) -> (body: MeshData, accent: MeshData) {
        let fp = PolygonMath.normalized(shape.footprint)
        let h = max(0.2, shape.height)
        var body = MeshData()
        if shape.robotClass == "A_service" {
            let baseTop = 0.28 * h
            body.append(prism(fp, z0: 0.01, z1: baseTop))
            body.append(prism(PolygonMath.scaled(fp, 0.32), z0: baseTop, z1: h - 0.02))
            let tiers = 3
            for k in 0..<tiers {
                let z = baseTop + (h - baseTop) * Double(k + 1) / Double(tiers) - 0.03
                body.append(prism(PolygonMath.scaled(fp, 0.88), z0: z, z1: z + 0.025))
            }
        } else {
            body.append(prism(fp, z0: 0.01, z1: h))
        }
        let maxX = fp.map(\.x).max() ?? 0.25
        let minX = fp.map(\.x).min() ?? -0.25
        let halfW = max(0.05, (fp.map(\.y).max() ?? 0.25) * 0.35)
        let tip = SIMD2(maxX * 0.95, 0.0)
        let back = max(minX * 0.2, maxX * 0.45)
        let arrow = [tip, SIMD2(back, halfW), SIMD2(back, -halfW)]
        let topZ = shape.robotClass == "A_service" ? 0.28 * h : h
        let accent = prism(arrow, z0: topZ + 0.002, z1: topZ + 0.012)
        return (body.doubleSided(), accent.doubleSided())
    }

    /// Box primitive (twin furniture) as a prism.
    public static func box(_ p: PrimitiveShape) -> MeshData {
        let z0 = max(0, p.center.z - p.size.z / 2)
        if p.type == "cylinder" {
            var c = PolygonMath.regular(radius: p.size.x / 2, segments: 24)
            c = c.map { $0 + SIMD2(p.center.x, p.center.y) }
            return prism(c, z0: z0, z1: z0 + p.size.z).doubleSided()
        }
        let r = PolygonMath.rectangle(center: SIMD2(p.center.x, p.center.y), length: p.size.x, width: p.size.y, yaw: p.yaw)
        return prism(r, z0: z0, z1: z0 + p.size.z).doubleSided()
    }
}
