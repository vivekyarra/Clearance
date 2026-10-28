import simd
import XCTest
@testable import ClearanceKit

final class KeyframeSelectorTests: XCTestCase {
    func pose(x: Double = 0, yawDeg: Double = 0) -> Mat4 {
        let a = yawDeg * .pi / 180
        return Mat4(columns: (SIMD4(cos(a), 0, -sin(a), 0), SIMD4(0, 1, 0, 0), SIMD4(sin(a), 0, cos(a), 0), SIMD4(x, 0, 0, 1)))
    }

    func testFirstFrameIsKeyframe() {
        XCTAssertTrue(KeyframeSelector().isCandidate(time: 0, pose: pose()))
    }

    func testTranslationThreshold() {
        var s = KeyframeSelector()
        s.commit(time: 0, pose: pose())
        XCTAssertFalse(s.isCandidate(time: 1, pose: pose(x: 0.09)))
        XCTAssertTrue(s.isCandidate(time: 1, pose: pose(x: 0.11)))
    }

    func testRotationThreshold() {
        var s = KeyframeSelector()
        s.commit(time: 0, pose: pose())
        XCTAssertFalse(s.isCandidate(time: 1, pose: pose(yawDeg: 9)))
        XCTAssertTrue(s.isCandidate(time: 1, pose: pose(yawDeg: 11)))
        XCTAssertTrue(s.isCandidate(time: 1, pose: pose(yawDeg: -11)))
    }

    func testRateCap() {
        var s = KeyframeSelector()
        s.commit(time: 0, pose: pose())
        XCTAssertFalse(s.isCandidate(time: 0.05, pose: pose(x: 1)))      // 20 fps
        XCTAssertTrue(s.isCandidate(time: 1.0 / 15.0, pose: pose(x: 1)))  // exactly 15 fps
    }

    func testFastWalkYieldsAtMost15fps() {
        var s = KeyframeSelector()
        var saved = 0
        for i in 0..<600 {  // 10 s at 60 fps, moving 3 m/s
            let t = Double(i) / 60
            let p = pose(x: 3 * t)
            if s.isCandidate(time: t, pose: p) { s.commit(time: t, pose: p); saved += 1 }
        }
        XCTAssertLessThanOrEqual(saved, 151)
        XCTAssertGreaterThan(saved, 140)
        XCTAssertEqual(s.count, saved)
    }

    func testRejectedFrameKeepsNextEligible() {
        var s = KeyframeSelector()
        s.commit(time: 0, pose: pose())
        XCTAssertTrue(s.isCandidate(time: 1, pose: pose(x: 0.2)))   // blur check rejects: no commit
        XCTAssertTrue(s.isCandidate(time: 1.02, pose: pose(x: 0.21)))
    }

    func testMotionMonitor() {
        var m = MotionMonitor(maxLinearSpeed: 1.0, maxAngularSpeedDeg: 90, smoothing: 1.0)
        XCTAssertFalse(m.update(time: 0, pose: pose()))
        XCTAssertFalse(m.update(time: 0.1, pose: pose(x: 0.05)))   // 0.5 m/s
        XCTAssertTrue(m.update(time: 0.2, pose: pose(x: 0.25)))    // 2 m/s
        m.reset()
        XCTAssertFalse(m.update(time: 0, pose: pose()))
        XCTAssertTrue(m.update(time: 0.1, pose: pose(yawDeg: 20)))  // 200 deg/s
    }
}

final class CoverageGridTests: XCTestCase {
    /// Points filling the cell centred at (cx, cy) on the floor.
    func cellPoints(_ cx: Double, _ cy: Double, n: Int = 5) -> [SIMD3<Double>] {
        (0..<n).map { i in SIMD3(cx + 0.02 * Double(i), cy - 0.02 * Double(i), 0.0) }
    }

    func testCellIndexing() {
        let g = CoverageGrid()
        XCTAssertEqual(g.cell(x: 0.1, y: 0.1), .init(ix: 0, iy: 0))
        XCTAssertEqual(g.cell(x: -0.1, y: 0.6), .init(ix: -1, iy: 1))
        XCTAssertEqual(g.center(.init(ix: -1, iy: 1)), SIMD2(-0.25, 0.75))
    }

    func testSingleViewIsNotCovered() {
        var g = CoverageGrid()
        g.observe(cameraPosition: SIMD3(0, -2, 1.5), points: cellPoints(0.25, 0.25))
        XCTAssertEqual(g.state(.init(ix: 0, iy: 0)), .single)
        XCTAssertEqual(g.coveredCount, 0)
        XCTAssertEqual(g.seenCount, 1)
    }

    func testTwoCloseAnglesAreNotEnough() {
        var g = CoverageGrid()
        // Two camera positions whose bearings differ by ~6 degrees (same or adjacent sector).
        g.observe(cameraPosition: SIMD3(0.25, -3, 1.5), points: cellPoints(0.25, 0.25))
        g.observe(cameraPosition: SIMD3(0.6, -3, 1.5), points: cellPoints(0.25, 0.25))
        XCTAssertEqual(g.state(.init(ix: 0, iy: 0)), .single)
    }

    func testTwoDistinctAnglesCover() {
        var g = CoverageGrid()
        g.observe(cameraPosition: SIMD3(0.25, -2, 1.5), points: cellPoints(0.25, 0.25))  // from south
        g.observe(cameraPosition: SIMD3(2.5, 0.25, 1.5), points: cellPoints(0.25, 0.25)) // from east
        XCTAssertEqual(g.state(.init(ix: 0, iy: 0)), .covered)
        XCTAssertEqual(g.coveredFraction, 1)
    }

    func testFiltersNoiseAndRange() {
        var g = CoverageGrid(minPointsPerView: 3, maxRange: 6)
        g.observe(cameraPosition: SIMD3(0, 0, 1.5), points: [SIMD3(2, 2, 0), SIMD3(2.1, 2.1, 0)])  // 2 points only
        g.observe(cameraPosition: SIMD3(0, 0, 1.5), points: cellPoints(10.25, 0.25))              // too far
        g.observe(cameraPosition: SIMD3(0, 0, 1.5), points: cellPoints(2.25, 0.25).map { SIMD3($0.x, $0.y, 4) }) // above band
        XCTAssertEqual(g.seenCount, 0)
        XCTAssertEqual(g.visited, [.init(ix: 0, iy: 0)])
    }

    func testMultiAngleMask() {
        XCTAssertFalse(CoverageGrid.isMultiAngle(0b1, sectorCount: 12))
        XCTAssertFalse(CoverageGrid.isMultiAngle(0b11, sectorCount: 12))
        XCTAssertTrue(CoverageGrid.isMultiAngle(0b101, sectorCount: 12))
        XCTAssertFalse(CoverageGrid.isMultiAngle((1 << 11) | 1, sectorCount: 12))  // wrap-around neighbours
        XCTAssertTrue(CoverageGrid.isMultiAngle((1 << 10) | 1, sectorCount: 12))
    }

    func testPromptPointsToSingleViewCell() {
        var g = CoverageGrid()
        g.observe(cameraPosition: SIMD3(0, 0, 1.5), points: cellPoints(2.25, 0.25, n: 12))
        // Camera at origin looking along +Y (yaw 90 deg): the cell at +X is to the right.
        let p = g.prompt(cameraPosition: SIMD3(0, 0, 1.5), heading: .pi / 2)
        XCTAssertNotNil(p)
        XCTAssertEqual(p?.cell, .init(ix: 4, iy: 0))
        XCTAssertTrue(p?.text.contains("to your right") ?? false)
        let ahead = g.prompt(cameraPosition: SIMD3(0, 0, 1.5), heading: 0)
        XCTAssertTrue(ahead?.text.contains("ahead") ?? false)
    }

    func testBoundsIncludeVisitedAndSeen() {
        var g = CoverageGrid()
        g.observe(cameraPosition: SIMD3(-1.2, -0.1, 1.5), points: cellPoints(1.25, 2.25))
        let b = g.bounds
        XCTAssertEqual(b?.min, .init(ix: -3, iy: -1))
        XCTAssertEqual(b?.max, .init(ix: 2, iy: 4))
    }

    func testManyObservationsStayConsistent() {
        var g = CoverageGrid()
        var rng = SystemRandomNumberGenerator()
        for k in 0..<200 {
            let a = Double(k) * 0.1
            let cam = SIMD3(4 * cos(a), 4 * sin(a), 1.5)
            let pts = (0..<600).map { _ in
                SIMD3(Double.random(in: -2...2, using: &rng), Double.random(in: -2...2, using: &rng), 0.5)
            }
            g.observe(cameraPosition: cam, points: pts)
        }
        XCTAssertEqual(g.seenCount, 64)  // 4 m x 4 m square, 0.5 m cells
        XCTAssertGreaterThan(g.coveredFraction, 0.95)
    }
}
