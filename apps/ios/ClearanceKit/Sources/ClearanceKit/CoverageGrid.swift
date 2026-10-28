import Foundation
import simd

/// Top-down coverage grid (PLAN 7.1): 0.5 m cells in the site frame. A cell counts as covered
/// once it has been observed from at least two viewing directions that differ by 30 degrees or
/// more. Directions are the bearing from the camera to the cell centre, binned into 12 sectors
/// of 30 degrees; two occupied sectors at circular distance >= 2 guarantee >= 30 degrees apart.
public struct CoverageGrid {
    public struct Cell: Hashable, Codable, Sendable {
        public let ix: Int
        public let iy: Int
        public init(ix: Int, iy: Int) { self.ix = ix; self.iy = iy }
    }

    public struct Info: Equatable, Sendable {
        public var sectors: UInt32 = 0
        public var views: Int = 0
        public var points: Int = 0
    }

    public enum State: Equatable, Sendable { case unseen, single, covered }

    public let cellSize: Double
    public let sectorCount: Int
    public var minPointsPerView: Int
    public var maxRange: Double
    public var minRange: Double
    public var zBand: ClosedRange<Double>
    public private(set) var cells: [Cell: Info] = [:]
    public private(set) var visited: Set<Cell> = []

    public init(cellSize: Double = 0.5, sectorCount: Int = 12, minPointsPerView: Int = 3,
                maxRange: Double = 6.0, minRange: Double = 0.3, zBand: ClosedRange<Double> = -0.3...2.5) {
        precondition(sectorCount >= 3 && sectorCount <= 32)
        self.cellSize = cellSize
        self.sectorCount = sectorCount
        self.minPointsPerView = minPointsPerView
        self.maxRange = maxRange
        self.minRange = minRange
        self.zBand = zBand
    }

    public func cell(x: Double, y: Double) -> Cell {
        Cell(ix: Int(floor(x / cellSize)), iy: Int(floor(y / cellSize)))
    }

    public func center(_ c: Cell) -> SIMD2<Double> {
        SIMD2((Double(c.ix) + 0.5) * cellSize, (Double(c.iy) + 0.5) * cellSize)
    }

    public func sector(from camera: SIMD2<Double>, to target: SIMD2<Double>) -> Int {
        let a = atan2(target.y - camera.y, target.x - camera.x) + .pi  // 0...2pi
        let s = Int(floor(a / (2 * .pi) * Double(sectorCount)))
        return ((s % sectorCount) + sectorCount) % sectorCount
    }

    /// One view: camera position and the site-frame points it observed (feature points, depth samples).
    public mutating func observe(cameraPosition cam: SIMD3<Double>, points: [SIMD3<Double>]) {
        let cam2 = SIMD2(cam.x, cam.y)
        visited.insert(cell(x: cam.x, y: cam.y))
        var counts: [Cell: Int] = [:]
        for p in points {
            guard zBand.contains(p.z), p.x.isFinite, p.y.isFinite else { continue }
            let d = simd_distance(SIMD2(p.x, p.y), cam2)
            guard d >= minRange, d <= maxRange else { continue }
            counts[cell(x: p.x, y: p.y), default: 0] += 1
        }
        for (c, n) in counts where n >= minPointsPerView {
            let s = sector(from: cam2, to: center(c))
            var info = cells[c] ?? Info()
            info.sectors |= (1 << UInt32(s))
            info.views += 1
            info.points += n
            cells[c] = info
        }
    }

    public static func isMultiAngle(_ mask: UInt32, sectorCount: Int) -> Bool {
        var bits: [Int] = []
        for i in 0..<sectorCount where mask & (1 << UInt32(i)) != 0 { bits.append(i) }
        guard bits.count >= 2 else { return false }
        for i in 0..<bits.count {
            for j in (i + 1)..<bits.count {
                let d = abs(bits[i] - bits[j])
                if min(d, sectorCount - d) >= 2 { return true }
            }
        }
        return false
    }

    public func state(_ c: Cell) -> State {
        guard let info = cells[c], info.sectors != 0 else { return .unseen }
        return CoverageGrid.isMultiAngle(info.sectors, sectorCount: sectorCount) ? .covered : .single
    }

    public var seenCount: Int { cells.values.filter { $0.sectors != 0 }.count }
    public var coveredCount: Int { cells.keys.filter { state($0) == .covered }.count }

    /// Covered fraction of seen cells (0 when nothing is seen yet).
    public var coveredFraction: Double {
        let s = seenCount
        return s == 0 ? 0 : Double(coveredCount) / Double(s)
    }

    /// Bounding box (min, max) of seen and visited cells, in cell indices.
    public var bounds: (min: Cell, max: Cell)? {
        let all = Array(cells.keys) + Array(visited)
        guard let f = all.first else { return nil }
        var lo = f, hi = f
        for c in all {
            lo = Cell(ix: min(lo.ix, c.ix), iy: min(lo.iy, c.iy))
            hi = Cell(ix: max(hi.ix, c.ix), iy: max(hi.iy, c.iy))
        }
        return (lo, hi)
    }

    public struct Prompt: Equatable, Sendable {
        public let text: String
        public let cell: Cell
        public let distance: Double
    }

    /// Suggests the nearest cell that so far was seen from a single direction.
    /// `heading` = camera viewing yaw in the site frame (radians, about +Z).
    public func prompt(cameraPosition cam: SIMD3<Double>, heading: Double, minPoints: Int = 10) -> Prompt? {
        let cam2 = SIMD2(cam.x, cam.y)
        var best: (Cell, Double)?
        for (c, info) in cells where info.points >= minPoints && state(c) == .single {
            let d = simd_distance(center(c), cam2)
            guard d >= cellSize else { continue }
            if best == nil || d < best!.1 { best = (c, d) }
        }
        guard let found = best else { return nil }
        let c = found.0, d = found.1
        let v = center(c) - cam2
        var rel = atan2(v.y, v.x) - heading
        while rel > .pi { rel -= 2 * .pi }
        while rel < -.pi { rel += 2 * .pi }
        let dir: String
        switch abs(rel) {
        case ..<(Double.pi / 4): dir = "ahead"
        case ..<(3 * Double.pi / 4): dir = rel > 0 ? "to your left" : "to your right"
        default: dir = "behind you"
        }
        let text = "Area \(String(format: "%.1f", d)) m \(dir) was seen from one side only. Walk around it and look again."
        return Prompt(text: text, cell: c, distance: d)
    }

    public mutating func reset() {
        cells.removeAll()
        visited.removeAll()
    }
}
