import simd
import XCTest
@testable import ClearanceKit

final class SiteFrameTests: XCTestCase {
    func assertClose(_ a: SIMD3<Double>, _ b: SIMD3<Double>, _ tol: Double = 1e-9, file: StaticString = #filePath, line: UInt = #line) {
        XCTAssertLessThan(simd_distance(a, b), tol, "\(a) vs \(b)", file: file, line: line)
    }

    /// Anchor for a tag lying flat on the floor: anchor axes are X right, Y up (normal), Z toward tag bottom.
    func flatAnchor(at p: SIMD3<Double>, yaw: Double) -> Mat4 {
        // Rotation about world +Y by yaw.
        let c = cos(yaw), s = sin(yaw)
        return Mat4(columns: (SIMD4(c, 0, -s, 0), SIMD4(0, 1, 0, 0), SIMD4(s, 0, c, 0), SIMD4(p, 1)))
    }

    func testAnchorFromSiteAxes() {
        let m = SiteFrame.anchorFromSite
        assertClose(SiteFrame.rotate(m, SIMD3(1, 0, 0)), SIMD3(1, 0, 0))   // site X = tag right
        assertClose(SiteFrame.rotate(m, SIMD3(0, 1, 0)), SIMD3(0, 0, -1))  // site Y = tag top (anchor -Z)
        assertClose(SiteFrame.rotate(m, SIMD3(0, 0, 1)), SIMD3(0, 1, 0))   // site Z = tag normal (anchor +Y)
        XCTAssertEqual(simd_determinant(SiteFrame.rotation(m)), 1, accuracy: 1e-12)
    }

    func testIdentityAnchorMapsWorldUpToSiteZ() {
        for leveled in [true, false] {
            let s = SiteFrame.siteFromWorld(worldFromAnchor: matrix_identity_double4x4, leveled: leveled)
            assertClose(SiteFrame.transform(s, SIMD3(0, 1.5, 0)), SIMD3(0, 0, 1.5))
            assertClose(SiteFrame.transform(s, SIMD3(0, 0, -2)), SIMD3(0, 2, 0))   // ARKit -Z (forward) -> site +Y
            assertClose(SiteFrame.transform(s, SIMD3(3, 0, 0)), SIMD3(3, 0, 0))
        }
    }

    func testTranslatedRotatedAnchor() {
        let a = flatAnchor(at: SIMD3(1, -1.4, 2), yaw: 0.7)
        for leveled in [true, false] {
            let s = SiteFrame.siteFromWorld(worldFromAnchor: a, leveled: leveled)
            assertClose(SiteFrame.transform(s, SIMD3(1, -1.4, 2)), .zero)             // marker centre = origin
            assertClose(SiteFrame.transform(s, SIMD3(1, -1.4 + 1.6, 2)), SIMD3(0, 0, 1.6))
            // A point 0.5 m along the tag's right edge direction.
            let right = SiteFrame.translation(a) + 0.5 * v3(a.columns.0)
            assertClose(SiteFrame.transform(s, right), SIMD3(0.5, 0, 0))
            // A point 0.3 m toward the tag's top edge (anchor -Z).
            let top = SiteFrame.translation(a) - 0.3 * v3(a.columns.2)
            assertClose(SiteFrame.transform(s, top), SIMD3(0, 0.3, 0))
        }
    }

    func testLeveledIgnoresTagTilt() {
        // Tag tilted 3 degrees about its X axis; leveled frame keeps Z = gravity up.
        let tilt = 3.0 * .pi / 180
        let rx = Mat4(columns: (SIMD4(1, 0, 0, 0), SIMD4(0, cos(tilt), sin(tilt), 0),
                                SIMD4(0, -sin(tilt), cos(tilt), 0), SIMD4(0, 0, 0, 1)))
        let a = flatAnchor(at: SIMD3(0.2, 0, -1), yaw: -1.1) * rx
        let ws = SiteFrame.worldFromSite(worldFromAnchor: a, leveled: true)
        assertClose(SiteFrame.rotate(ws, SIMD3(0, 0, 1)), SIMD3(0, 1, 0))
        let flat = SiteFrame.worldFromSite(worldFromAnchor: flatAnchor(at: SIMD3(0.2, 0, -1), yaw: -1.1), leveled: true)
        XCTAssertLessThan(SiteFrame.rotationAngle(ws, flat), 1e-9)
        // Non-leveled follows the tag normal instead.
        let raw = SiteFrame.worldFromSite(worldFromAnchor: a, leveled: false)
        XCTAssertEqual(SiteFrame.rotationAngle(raw, flat), tilt, accuracy: 1e-9)
    }

    func testLeveledWallTagFallback() {
        // Tag on a wall: tag X vertical (world +Y), tag normal along world +Z.
        let a = Mat4(columns: (SIMD4(0, 1, 0, 0), SIMD4(0, 0, 1, 0), SIMD4(1, 0, 0, 0), SIMD4(0, 1, 0, 1)))
        let ws = SiteFrame.worldFromSite(worldFromAnchor: a, leveled: true)
        let r = SiteFrame.rotation(ws)
        XCTAssertEqual(simd_determinant(r), 1, accuracy: 1e-9)
        assertClose(r.columns.2, SIMD3(0, 1, 0))
        XCTAssertEqual(r.columns.0.y, 0, accuracy: 1e-12)
    }

    func testCameraPoseInSiteFrame() {
        // ARKit camera at 1.5 m height looking horizontally along world -Z (identity rotation).
        let cam = Mat4(columns: (SIMD4(1, 0, 0, 0), SIMD4(0, 1, 0, 0), SIMD4(0, 0, 1, 0), SIMD4(0, 1.5, 3, 1)))
        let s = SiteFrame.siteFromWorld(worldFromAnchor: matrix_identity_double4x4, leveled: true)
        let tsc = SiteFrame.siteFromCamera(siteFromWorld: s, worldFromCamera: cam)
        assertClose(SiteFrame.translation(tsc), SIMD3(0, -3, 1.5))
        // Viewing direction (-Z of the camera) is site +Y; camera up (+Y) is site +Z.
        assertClose(SiteFrame.rotate(tsc, SIMD3(0, 0, -1)), SIMD3(0, 1, 0))
        assertClose(SiteFrame.rotate(tsc, SIMD3(0, 1, 0)), SIMD3(0, 0, 1))
        XCTAssertEqual(SiteFrame.viewingYaw(siteFromCamera: tsc), .pi / 2, accuracy: 1e-12)
    }

    func testRowMajorRoundTrip() {
        var m = Mat4()
        for c in 0..<4 { for r in 0..<4 { m[c][r] = Double(r * 10 + c) } }
        let rm = SiteFrame.rowMajor(m)
        XCTAssertEqual(rm, (0..<16).map { Double(($0 / 4) * 10 + $0 % 4) })
        XCTAssertEqual(rm[3], m[3][0])  // translation x is row 0, column 3
        XCTAssertEqual(SiteFrame.fromRowMajor(rm), m)
        XCTAssertNil(SiteFrame.fromRowMajor([1, 2, 3]))
    }

    func testRigidInverse() {
        let a = flatAnchor(at: SIMD3(1, 2, 3), yaw: 2.2)
        let i = SiteFrame.rigidInverse(a) * a
        for c in 0..<4 { for r in 0..<4 { XCTAssertEqual(i[c][r], c == r ? 1 : 0, accuracy: 1e-12) } }
    }

    func testMarkerLockNeedsStableSamples() {
        var lock = MarkerLock(requiredSamples: 4, maxPositionSpread: 0.01, maxAngleSpreadDeg: 1.5)
        let base = SiteFrame.worldFromSite(worldFromAnchor: flatAnchor(at: SIMD3(0, 0, -1), yaw: 0.3), leveled: true)
        XCTAssertNil(lock.add(base))
        XCTAssertNil(lock.add(base))
        var jumped = base
        jumped.columns.3.x += 0.2
        XCTAssertNil(lock.add(jumped))
        XCTAssertNil(lock.add(base))   // window still contains the jump
        for _ in 0..<3 { _ = lock.add(base) }
        let locked = lock.add(base)
        XCTAssertNotNil(locked)
        XCTAssertLessThan(simd_distance(SiteFrame.translation(locked!), SiteFrame.translation(base)), 1e-9)
    }

    func testMarkerLockAveragesJitter() {
        var lock = MarkerLock(requiredSamples: 4)
        var out: Mat4?
        for k in 0..<4 {
            let a = flatAnchor(at: SIMD3(0.002 * Double(k), 0, 0), yaw: 0.001 * Double(k))
            out = lock.add(SiteFrame.worldFromSite(worldFromAnchor: a, leveled: true))
        }
        XCTAssertNotNil(out)
        XCTAssertEqual(SiteFrame.translation(out!).x, 0.003, accuracy: 1e-9)
        XCTAssertEqual(simd_determinant(SiteFrame.rotation(out!)), 1, accuracy: 1e-9)
    }

    func testPoseSmootherConvergesAndSnaps() {
        var s = PoseSmoother(alpha: 0.5)
        let a = flatAnchor(at: .zero, yaw: 0)
        let b = flatAnchor(at: SIMD3(0.1, 0, 0), yaw: 0.2)
        _ = s.update(a)
        var m = a
        for _ in 0..<40 { m = s.update(b) }
        XCTAssertLessThan(simd_distance(SiteFrame.translation(m), SIMD3(0.1, 0, 0)), 1e-6)
        XCTAssertLessThan(SiteFrame.rotationAngle(m, b), 1e-6)
        let far = flatAnchor(at: SIMD3(2, 0, 0), yaw: 0)
        XCTAssertEqual(s.update(far), far)
    }
}
