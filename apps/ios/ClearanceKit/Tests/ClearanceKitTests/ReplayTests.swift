import Foundation
import simd
import XCTest
@testable import ClearanceKit

final class EpisodeTests: XCTestCase {
    let jsonl = """
    {"t":0.0,"robot":[0,0,3.1,0.0],"peds":[[1,1]],"events":[]}
    {"t":0.1,"robot":[0.1,0,-3.1,0.5],"peds":[[1.2,1]],"events":[{"type":"stop","xy":[0.1,0],"detail":"ped ahead"}]}

    {"t":0.2,"robot":[0.2,0.1,-3.0,0.5],"peds":[[1.4,1],[3,3]],"events":[{"type":"near_miss","xy":[0.2,0.3],"detail":{"d":0.4}}]}
    {"t":0.3,"robot":[0.3,0.1,-3.0,0.0],"peds":[],"events":[{"type":"fail","detail":"blocked_timeout"}]}
    """

    func testParseJSONL() throws {
        let e = try Episode.parse(Data(jsonl.utf8))
        XCTAssertEqual(e.frames.count, 4)
        XCTAssertEqual(e.duration, 0.3, accuracy: 1e-12)
        XCTAssertEqual(e.events.map(\.kind), [.stop, .nearMiss, .fail])
        XCTAssertEqual(e.events[0].detail, "ped ahead")
        XCTAssertEqual(e.events[1].detail, "d: 0.4")
        XCTAssertEqual(e.events[2].xy, SIMD2(0.3, 0.1))   // falls back to the robot position
        XCTAssertEqual(e.outcome?.kind, .fail)
    }

    func testParseArrayAndWrappedObject() throws {
        let arr = #"[{"t":1,"robot":{"x":1,"y":2,"yaw":0.5,"v":0.3}},{"t":2,"robot":[2,2,0.5,0.3]}]"#
        let a = try Episode.parse(Data(arr.utf8))
        XCTAssertEqual(a.frames.count, 2)
        XCTAssertEqual(a.frames[0].robot, RobotPose2D(x: 1, y: 2, yaw: 0.5, v: 0.3))
        let wrapped = #"{"episode_index":3,"frames":[{"t":5,"robot":[0,0,0,0]}]}"#
        XCTAssertEqual(try Episode.parse(Data(wrapped.utf8)).frames.count, 1)
        let jsonlField = try JSONSerialization.data(withJSONObject: ["jsonl": jsonl])
        XCTAssertEqual(try Episode.parse(jsonlField).frames.count, 4)
        XCTAssertThrowsError(try Episode.parse(Data("[]".utf8)))
        XCTAssertThrowsError(try Episode.parse(Data("not json".utf8)))
    }

    func testInterpolationAndYawWrap() throws {
        let e = try Episode.parse(Data(jsonl.utf8))
        let r = e.robot(at: 0.05)
        XCTAssertEqual(r.x, 0.05, accuracy: 1e-9)
        // 3.1 -> -3.1 crosses +-pi; the midpoint must be near pi, not near 0.
        XCTAssertGreaterThan(abs(r.yaw), 3.1)
        XCTAssertEqual(e.robot(at: -5), e.frames[0].robot)
        XCTAssertEqual(e.robot(at: 99), e.frames[3].robot)
        XCTAssertEqual(e.frameIndex(at: 0.1), 1)
        XCTAssertEqual(e.frameIndex(at: 0.19), 1)
        let p = e.pedestrians(at: 0.05)
        XCTAssertEqual(p.count, 1)
        XCTAssertEqual(p[0].x, 1.1, accuracy: 1e-9)
        XCTAssertEqual(e.pedestrians(at: 0.15).count, 1)   // counts differ: no interpolation
    }

    func testEventWindowFiresOnce() throws {
        let e = try Episode.parse(Data(jsonl.utf8))
        var fired: [EpisodeEventKind] = []
        var clock = EpisodeClock(start: e.startTime, end: e.endTime)
        clock.isPlaying = true
        clock.loops = false
        for _ in 0..<100 {
            let prev = clock.advance(1.0 / 60.0)
            fired += e.events(after: prev, upTo: clock.time).map(\.kind)
        }
        XCTAssertEqual(fired, [.stop, .nearMiss, .fail])
        XCTAssertFalse(clock.isPlaying)
        XCTAssertEqual(clock.progress, 1)
    }

    func testClockLoopsAndSeeks() {
        var c = EpisodeClock(start: 0, end: 10)
        c.advance(5)
        XCTAssertEqual(c.time, 0)       // paused
        c.isPlaying = true
        c.rate = 2
        c.advance(2)
        XCTAssertEqual(c.time, 4)
        c.advance(4)
        XCTAssertEqual(c.time, 0)       // looped
        c.seek(-3)
        XCTAssertEqual(c.time, 0)
        c.seek(7)
        XCTAssertEqual(c.progress, 0.7, accuracy: 1e-12)
    }

    func testWrapAngle() {
        XCTAssertEqual(Episode.wrapAngle(3 * .pi), -.pi, accuracy: 1e-12)
        XCTAssertEqual(Episode.wrapAngle(-0.5), -0.5, accuracy: 1e-12)
        XCTAssertEqual(Episode.wrapAngle(7), 7 - 2 * .pi, accuracy: 1e-12)
    }

    func testPathDecimation() throws {
        let e = try Episode.parse(Data(jsonl.utf8))
        XCTAssertEqual(e.path(minStep: 0.05).count, 4)
        XCTAssertEqual(e.path(minStep: 0.5).count, 1)
    }

    func testLongEpisodeSampling() throws {
        var lines: [String] = []
        for i in 0..<36_000 {  // one hour at 10 Hz
            let t = Double(i) / 10
            lines.append(#"{"t":\#(t),"robot":[\#(cos(t)),\#(sin(t)),\#(t + .pi / 2),1]}"#)
        }
        let e = try Episode.parse(Data(lines.joined(separator: "\n").utf8))
        XCTAssertEqual(e.frames.count, 36_000)
        let r = e.robot(at: 1234.56)
        XCTAssertEqual(simd_length(SIMD2(r.x, r.y)), 1, accuracy: 0.01)
    }
}

final class MeshBuilderTests: XCTestCase {
    func area(_ m: MeshData, normalZ: Float) -> Double {
        var a = 0.0
        var i = 0
        while i < m.indices.count {
            let p0 = m.positions[Int(m.indices[i])], p1 = m.positions[Int(m.indices[i + 1])], p2 = m.positions[Int(m.indices[i + 2])]
            let n = simd_cross(p1 - p0, p2 - p0)
            if abs(n.z) > 1e-6, (n.z > 0) == (normalZ > 0), abs(p0.z - p1.z) < 1e-6 { a += Double(simd_length(n)) / 2 }
            i += 3
        }
        return a
    }

    func testTriangulateConvexAndConcave() {
        let square = [SIMD2(0.0, 0.0), SIMD2(1, 0), SIMD2(1, 1), SIMD2(0, 1)]
        XCTAssertEqual(PolygonMath.triangulate(square).count, 2)
        let l = PolygonMath.normalized([SIMD2(0.0, 0.0), SIMD2(2, 0), SIMD2(2, 1), SIMD2(1, 1), SIMD2(1, 2), SIMD2(0, 2)])
        let tris = PolygonMath.triangulate(l)
        XCTAssertEqual(tris.count, 4)
        var total = 0.0
        for (a, b, c) in tris {
            let s = PolygonMath.signedArea([l[a], l[b], l[c]])
            XCTAssertGreaterThan(s, 0)
            total += s
        }
        XCTAssertEqual(total, 3, accuracy: 1e-12)
    }

    func testNormalizedFixesOrientationAndDuplicates() {
        let cw = [SIMD2(0.0, 0.0), SIMD2(0, 1), SIMD2(0, 1), SIMD2(0.5, 1), SIMD2(1, 1), SIMD2(1, 0), SIMD2(0, 0)]
        let n = PolygonMath.normalized(cw)
        XCTAssertEqual(n.count, 4)
        XCTAssertEqual(PolygonMath.signedArea(n), 1, accuracy: 1e-12)
    }

    func testPrismGeometry() {
        let sq = PolygonMath.rectangle(center: .zero, length: 0.6, width: 0.4, yaw: 0)
        let m = MeshBuilder.prism(sq, z0: 0, z1: 1.2)
        XCTAssertEqual(m.triangleCount, 2 * 2 + 4 * 2)
        XCTAssertEqual(m.positions.count, m.normals.count)
        XCTAssertEqual(area(m, normalZ: 1), 0.24, accuracy: 1e-5)
        let b = m.bounds!
        XCTAssertEqual(b.min.z, 0)
        XCTAssertEqual(b.max.z, 1.2, accuracy: 1e-6)
        XCTAssertEqual(b.max.x, 0.3, accuracy: 1e-6)
        // Winding agrees with the stored normals (counter-clockwise seen from outside).
        var i = 0
        while i < m.indices.count {
            let p0 = m.positions[Int(m.indices[i])], p1 = m.positions[Int(m.indices[i + 1])], p2 = m.positions[Int(m.indices[i + 2])]
            XCTAssertGreaterThan(simd_dot(simd_cross(p1 - p0, p2 - p0), m.normals[Int(m.indices[i])]), 0)
            i += 3
        }
        XCTAssertEqual(MeshBuilder.prism(sq, z0: 1, z1: 1).triangleCount, 0)
    }

    func testDoubleSided() {
        let m = MeshBuilder.prism(PolygonMath.regular(radius: 0.3, segments: 8), z0: 0, z1: 1)
        let d = m.doubleSided()
        XCTAssertEqual(d.triangleCount, 2 * m.triangleCount)
        XCTAssertEqual(d.positions.count, 2 * m.positions.count)
        XCTAssertTrue(d.indices.allSatisfy { Int($0) < d.positions.count })
    }

    func testRingDiscRibbon() {
        let r = MeshBuilder.ring(inner: 0.4, outer: 0.5, segments: 64)
        XCTAssertEqual(area(r, normalZ: 1), Double.pi * (0.25 - 0.16), accuracy: 0.005)
        XCTAssertEqual(area(MeshBuilder.disc(radius: 1, segments: 128), normalZ: 1), .pi, accuracy: 0.01)
        let rb = MeshBuilder.ribbon([SIMD2(0, 0), SIMD2(1, 0), SIMD2(1, 0), SIMD2(1, 2)], width: 0.1)
        XCTAssertEqual(area(rb, normalZ: 1), 0.3, accuracy: 1e-5)
        XCTAssertEqual(MeshBuilder.ribbon([SIMD2(0, 0)], width: 0.1).triangleCount, 0)
    }

    func testRobotMeshes() {
        var shape = RobotShape.standard()
        let b = MeshBuilder.robot(shape)
        XCTAssertGreaterThan(b.body.triangleCount, 0)
        XCTAssertGreaterThan(b.accent.triangleCount, 0)
        XCTAssertEqual(b.body.bounds!.max.z, 1.2, accuracy: 1e-5)
        shape.robotClass = "A_service"
        let a = MeshBuilder.robot(shape)
        XCTAssertGreaterThan(a.body.triangleCount, b.body.triangleCount)
        XCTAssertLessThanOrEqual(a.body.bounds!.max.z, 1.2 + 1e-5)
        XCTAssertGreaterThan(a.accent.bounds!.max.x, 0)   // heading marker points to +x
    }

    func testBoxPrimitive() {
        let p = PrimitiveShape(id: "T1", type: "box", label: "table", center: SIMD3(1, 2, 0.375), yaw: .pi / 2,
                               size: SIMD3(1.2, 0.6, 0.75), movable: true)
        let b = MeshBuilder.box(p).bounds!
        XCTAssertEqual(b.min.z, 0, accuracy: 1e-6)
        XCTAssertEqual(b.max.z, 0.75, accuracy: 1e-6)
        XCTAssertEqual(b.max.y - b.min.y, 1.2, accuracy: 1e-5)   // length rotated onto y
        XCTAssertEqual(b.max.x - b.min.x, 0.6, accuracy: 1e-5)
    }
}

final class ReplayModelsTests: XCTestCase {
    func testRobotShapeFromSpec() {
        let spec: [String: Any] = ["robot_id": "service_t1", "class": "A_service",
                                   "footprint_polygon_m": [[0.3, 0.25], [-0.3, 0.25], [-0.3, -0.25], [0.3, -0.25]],
                                   "height_m": 1.1, "safety_stop_distance_m": 0.5]
        let s = RobotShape.from(json: spec)
        XCTAssertEqual(s.robotId, "service_t1")
        XCTAssertEqual(s.robotClass, "A_service")
        XCTAssertEqual(s.footprint.count, 4)
        XCTAssertFalse(s.footprintApprox)
        XCTAssertTrue(s.heightKnown)
        XCTAssertEqual(s.height, 1.1)
        XCTAssertEqual(s.safetyStopDistance, 0.5)
        let sparseSpec: [String: Any] = ["robot_id": "x", "height_m": NSNull(), "footprint_polygon_m": [[0, 0], [1, 0]]]
        let sparse = RobotShape.from(json: sparseSpec)
        XCTAssertTrue(sparse.footprintApprox)
        XCTAssertFalse(sparse.heightKnown)
        XCTAssertEqual(sparse.height, 1.2)
    }

    func testVerdictParse() {
        XCTAssertEqual(VerdictBadge.parse(["robot_id": "r", "label": "Cleared with changes", "main_reason": "move T4"])?.label,
                       .clearedWithChanges)
        XCTAssertNil(VerdictBadge.parse(["robot_id": "r", "label": "Maybe"]))
    }

    func testLayoutChangeApply() {
        let prims = [
            PrimitiveShape(id: "T4", type: "box", label: "table", center: SIMD3(1, 1, 0.4), yaw: 0, size: SIMD3(1, 1, 0.8), movable: true),
            PrimitiveShape(id: "C1", type: "cylinder", label: "stool", center: SIMD3(2, 1, 0.3), yaw: 0, size: SIMD3(0.4, 0.4, 0.6), movable: true),
        ]
        let pose: [String: Any] = ["xyz": [0, 0, 0.1], "yaw": 0]
        let added: [String: Any] = ["id": "B1", "type": "box", "label": "box", "pose": pose, "size": [0.2, 0.2, 0.2]]
        let overrides: [String: Any] = ["move": ["T4": ["dx": 0.18, "dy": 0, "dyaw": 0.26]], "remove": ["C1"],
                                        "add_boxes": [added]]
        let change = LayoutChange.parse(overrides)
        XCTAssertFalse(change.isEmpty)
        let after = change.apply(to: prims)
        XCTAssertEqual(after.map(\.id), ["T4", "B1"])
        XCTAssertEqual(after[0].center.x, 1.18, accuracy: 1e-12)
        XCTAssertEqual(after[0].yaw, 0.26, accuracy: 1e-12)
        XCTAssertTrue(LayoutChange.parse([:]).isEmpty)
    }

    func testAnalysisRefs() {
        let opt: [String: Any] = ["change": ["move": ["T4": ["dx": 0.1, "dy": 0, "dyaw": 0]]], "description": "Move T4 0.10 m east",
                                  "batch_ids": ["sb_3", "sb_4"]]
        let analysis: [String: Any] = [
            "batches": ["sb_1", ["batch_id": "sb_2"]] as [Any],
            "optimizer": [opt],
            "verdicts": [["robot_id": "r1", "label": "Cleared", "main_reason": "ok"],
                         ["robot_id": "r2", "label": "Not cleared", "main_reason": "narrowest gap 0.62 m vs 0.70 m needed"]],
        ]
        let r = AnalysisReplayRefs.parse(analysis, robotId: "r2")
        XCTAssertEqual(r.beforeBatchId, "sb_1")
        XCTAssertEqual(r.afterBatchId, "sb_4")
        XCTAssertEqual(r.change?.description, "Move T4 0.10 m east")
        XCTAssertEqual(r.verdicts.map(\.label), [.notCleared])
        let empty = AnalysisReplayRefs.parse([:])
        XCTAssertNil(empty.beforeBatchId)
        XCTAssertNil(empty.change)
    }

    func testPrimitivesFromTwin() {
        let pose: [String: Any] = ["xyz": [1, 2, 0.4], "yaw": 0.1]
        let table: [String: Any] = ["id": "T1", "type": "box", "label": "table", "pose": pose, "size": [1, 0.6, 0.8], "movable": true]
        let bad: [String: Any] = ["id": "bad"]
        let twin: [String: Any] = ["primitives": [table, bad]]
        let p = PrimitiveShape.parseAll(twin: twin)
        XCTAssertEqual(p.count, 1)
        XCTAssertEqual(p[0].size, SIMD3(1, 0.6, 0.8))
        XCTAssertTrue(p[0].movable)
    }
}
