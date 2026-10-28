import Foundation
import simd
import XCTest
@testable import ClearanceKit

final class BlurMetricTests: XCTestCase {
    func checkerboard(_ w: Int, _ h: Int, cell: Int) -> [UInt8] {
        (0..<(w * h)).map { i in ((i % w) / cell + (i / w) / cell) % 2 == 0 ? 20 : 230 }
    }

    /// 1-D box blur along rows and columns.
    func blurred(_ px: [UInt8], _ w: Int, _ h: Int, radius r: Int) -> [UInt8] {
        var tmp = px, out = px
        for y in 0..<h { for x in 0..<w {
            var s = 0, n = 0
            for k in -r...r where x + k >= 0 && x + k < w { s += Int(px[y * w + x + k]); n += 1 }
            tmp[y * w + x] = UInt8(s / n)
        } }
        for y in 0..<h { for x in 0..<w {
            var s = 0, n = 0
            for k in -r...r where y + k >= 0 && y + k < h { s += Int(tmp[(y + k) * w + x]); n += 1 }
            out[y * w + x] = UInt8(s / n)
        } }
        return out
    }

    func testSharpScoresHigherThanBlurred() {
        let w = 160, h = 120
        let sharp = checkerboard(w, h, cell: 8)
        let soft = blurred(sharp, w, h, radius: 4)
        let a = BlurMetric.laplacianVariance(sharp, width: w, height: h)
        let b = BlurMetric.laplacianVariance(soft, width: w, height: h)
        XCTAssertGreaterThan(a, 10 * b)
        XCTAssertEqual(BlurMetric.laplacianVariance([UInt8](repeating: 77, count: w * h), width: w, height: h), 0)
        XCTAssertEqual(BlurMetric.laplacianVariance([1, 2], width: 2, height: 1), 0)
    }

    func testDownsampleAveragesAndHonoursRowPadding() {
        // 4x2 image stored with 6 bytes per row (2 bytes padding).
        let raw: [UInt8] = [10, 20, 30, 40, 99, 99,
                            30, 40, 50, 60, 99, 99]
        let out = raw.withUnsafeBytes {
            BlurMetric.downsample(base: $0.baseAddress!, width: 4, height: 2, bytesPerRow: 6, factor: 2)
        }
        XCTAssertEqual(out.width, 2)
        XCTAssertEqual(out.height, 1)
        XCTAssertEqual(out.pixels, [25, 45])
        XCTAssertEqual(BlurMetric.factor(width: 1920), 4)
        XCTAssertEqual(BlurMetric.factor(width: 100), 1)
    }
}

final class GeometryTests: XCTestCase {
    let k = Intrinsics(fx: 1500, fy: 1500, cx: 960, cy: 720, width: 1920, height: 1440)

    func testScaling() {
        let s = k.scaled(toWidth: 1440, height: 1080)
        XCTAssertEqual(s.array, [1125, 1125, 720, 540])
        XCTAssertEqual(Intrinsics.savedSize(sensorWidth: 1920, sensorHeight: 1440, maxLongSide: 1440).width, 1440)
        XCTAssertEqual(Intrinsics.savedSize(sensorWidth: 1920, sensorHeight: 1440, maxLongSide: 1440).height, 1080)
        XCTAssertEqual(Intrinsics.savedSize(sensorWidth: 1920, sensorHeight: 1440, maxLongSide: 0).width, 1920)
        XCTAssertEqual(Intrinsics.savedSize(sensorWidth: 1920, sensorHeight: 1440, maxLongSide: 4000).height, 1440)
    }

    func testColumnMajorInit() {
        var m = simd_float3x3(1)
        m[0][0] = 1500; m[1][1] = 1490; m[2][0] = 955; m[2][1] = 718
        let i = Intrinsics(columnMajor: m, width: 1920, height: 1440)
        XCTAssertEqual(i.array, [1500, 1490, 955, 718])
    }

    func testProjectUnprojectRoundTrip() {
        let p = CameraGeometry.unproject(u: 1200, v: 300, depth: 2.5, k: k)
        XCTAssertEqual(p.z, -2.5)
        XCTAssertGreaterThan(p.x, 0)   // right of centre
        XCTAssertGreaterThan(p.y, 0)   // above centre (v < cy)
        let uv = CameraGeometry.project(p, k: k)!
        XCTAssertEqual(uv.x, 1200, accuracy: 1e-9)
        XCTAssertEqual(uv.y, 300, accuracy: 1e-9)
        XCTAssertNil(CameraGeometry.project(SIMD3(0, 0, 1), k: k))
    }

    func testDepthSamples() {
        let w = 32, h = 24
        var depth = [Float](repeating: 2, count: w * h)
        depth[0] = .nan
        let conf = [UInt8](repeating: 2, count: w * h)
        let pts = CameraGeometry.depthSamples(depth: depth, width: w, height: h, confidence: conf, k: k, step: 8)
        XCTAssertEqual(pts.count, 4 * 3)
        XCTAssertTrue(pts.allSatisfy { abs($0.z + 2) < 1e-9 })
        let low = [UInt8](repeating: 0, count: w * h)
        XCTAssertTrue(CameraGeometry.depthSamples(depth: depth, width: w, height: h, confidence: low, k: k, step: 8).isEmpty)
    }

    func testUprightOrientation() {
        XCTAssertEqual(UprightOrientation.from(worldUpInCamera: SIMD3(0, 1, 0)), .up)       // landscape, sensor upright
        XCTAssertEqual(UprightOrientation.from(worldUpInCamera: SIMD3(0, -1, 0)), .down)
        XCTAssertEqual(UprightOrientation.from(worldUpInCamera: SIMD3(-1, 0.1, 0)), .right) // portrait
        XCTAssertEqual(UprightOrientation.from(worldUpInCamera: SIMD3(1, 0, 0)), .left)
        XCTAssertEqual(UprightOrientation.right.inverse, .left)
        XCTAssertEqual(UprightOrientation.down.inverse, .down)
    }
}

final class CaptureSchemaTests: XCTestCase {
    func testCaptureJSONMatchesPythonFieldNames() throws {
        var m = CaptureManifest(captureId: "cap_1", siteId: "site_1",
                                device: CaptureDevice(model: "iPhone14,5", os: "iOS 17.5", tier: .vioMono))
        m.files.depth = nil
        m.startedAt = "2026-10-27T10:00:00Z"
        m.durationS = 12.5
        let obj = try JSONSerialization.jsonObject(with: JSONEncoder().encode(m)) as! [String: Any]
        XCTAssertEqual(obj["capture_id"] as? String, "cap_1")
        XCTAssertEqual(obj["site_id"] as? String, "site_1")
        XCTAssertEqual(obj["role"] as? String, "scanner")
        XCTAssertEqual((obj["device"] as? [String: Any])?["tier"] as? String, "T2_VIO_MONO")
        XCTAssertEqual((obj["marker"] as? [String: Any])?["size_m"] as? Double, 0.16)
        let files = obj["files"] as! [String: Any]
        XCTAssertEqual(files["poses"] as? String, "poses.jsonl")
        XCTAssertEqual(files["feature_points"] as? String, "feature_points.jsonl")
        XCTAssertNil(files["depth"])
        XCTAssertEqual((obj["privacy"] as? [String: Any])?["faces_blurred_on_device"] as? Bool, true)
        XCTAssertEqual(obj["duration_s"] as? Double, 12.5)
        let back = try JSONDecoder().decode(CaptureManifest.self, from: JSONEncoder().encode(m))
        XCTAssertEqual(back, m)
    }

    func testPoseLine() throws {
        let line = try JSONLines.line(PoseLine(t: 1.5, frame: "000001.jpg", tWorldCam: Array(repeating: 0, count: 16),
                                               k: [1, 2, 3, 4], w: 1440, h: 1080, tracking: "normal"))
        XCTAssertEqual(line.last, 0x0A)
        let obj = try JSONSerialization.jsonObject(with: line) as! [String: Any]
        XCTAssertEqual((obj["T_world_cam"] as? [Double])?.count, 16)
        XCTAssertEqual(obj["K"] as? [Double], [1, 2, 3, 4])
        XCTAssertEqual(obj["frame"] as? String, "000001.jpg")
        XCTAssertEqual(obj["tracking"] as? String, "normal")
    }

    func testFeaturePointsLine() throws {
        let d = JSONLines.featurePointsLine(frame: "000002.jpg", t: 0.25,
                                            points: [SIMD3(1, -0.0001, 2.34567), SIMD3(.nan, 0, 0)])
        let obj = try JSONSerialization.jsonObject(with: d) as! [String: Any]
        XCTAssertEqual(obj["frame"] as? String, "000002.jpg")
        let pts = obj["points_world"] as! [[Double]]
        XCTAssertEqual(pts, [[1, 0, 2.346]])
        let empty = try JSONSerialization.jsonObject(with: JSONLines.featurePointsLine(frame: "a", t: 0, points: [])) as! [String: Any]
        XCTAssertEqual((empty["points_world"] as? [Any])?.count, 0)
    }

    func testNaming() {
        XCTAssertEqual(CaptureNaming.frameName(123), "000123.jpg")
        XCTAssertEqual(CaptureNaming.mapName(7), "000007.png")
        XCTAssertTrue(CaptureManifest.newCaptureId().hasPrefix("cap_"))
        XCTAssertEqual(CaptureManifest.newCaptureId().count, 20)
    }
}

final class SiteJoinTests: XCTestCase {
    func testJSONPayload() {
        let j = SiteJoinParser.parse(#"{"api":"https://pg.example.com/api/","site_id":"site_ab12","token":"t1","name":"Cafe"}"#)
        XCTAssertEqual(j?.apiBase.absoluteString, "https://pg.example.com/api")
        XCTAssertEqual(j?.siteId, "site_ab12")
        XCTAssertEqual(j?.token, "t1")
        XCTAssertEqual(j?.displayName, "Cafe")
    }

    func testAppURL() {
        let j = SiteJoinParser.parse("clearance://join?api=http%3A%2F%2F192.168.1.20%3A8000&site=site_x")
        XCTAssertEqual(j?.apiBase.absoluteString, "http://192.168.1.20:8000")
        XCTAssertEqual(j?.siteId, "site_x")
        XCTAssertNil(j?.token)
    }

    func testConsoleURL() {
        let j = SiteJoinParser.parse("https://console.example.com:3000/join/site_77?token=abc")
        XCTAssertEqual(j?.apiBase.absoluteString, "https://console.example.com:3000/api")
        XCTAssertEqual(j?.siteId, "site_77")
        XCTAssertEqual(j?.token, "abc")
        let k = SiteJoinParser.parse("https://c.example.com/sites/site_9?api=https://api.example.com")
        XCTAssertEqual(k?.apiBase.absoluteString, "https://api.example.com")
    }

    func testRejectsUnusablePayloads() {
        XCTAssertNil(SiteJoinParser.parse(""))
        XCTAssertNil(SiteJoinParser.parse("hello"))
        XCTAssertNil(SiteJoinParser.parse("https://example.com/"))
        XCTAssertNil(SiteJoinParser.parse(#"{"api":"ftp://x","site_id":"s"}"#))
        XCTAssertNil(SiteJoinParser.parse(#"{"api":"https://x.com","site_id":"bad id/../"}"#))
        XCTAssertNil(SiteJoinParser.parse("clearance://join?site=s"))
    }
}

final class UploadPlanTests: XCTestCase {
    func files() -> [UploadFile] {
        var f = [UploadFile(path: "frames/000001.jpg", size: 400_000), UploadFile(path: "capture.json", size: 900),
                 UploadFile(path: "poses.jsonl", size: 50_000), UploadFile(path: "feature_points.jsonl", size: 3_000_000)]
        for i in 0..<100 {
            f.append(UploadFile(path: "frames/\(CaptureNaming.frameName(i + 2))", size: 400_000))
            f.append(UploadFile(path: "depth/\(CaptureNaming.mapName(i + 2))", size: 60_000))
            f.append(UploadFile(path: "confidence/\(CaptureNaming.mapName(i + 2))", size: 10_000))
        }
        return f
    }

    func testEveryFileExactlyOnceAndMetadataFirst() {
        let input = files()
        let parts = UploadPlanner.plan(files: input, targetBytes: 4 << 20)
        let all = parts.flatMap(\.files)
        XCTAssertEqual(all.count, input.count)
        XCTAssertEqual(Set(all), Set(input.map(\.path)))
        XCTAssertEqual(all.first, "capture.json")
        XCTAssertEqual(parts.map(\.index), Array(0..<parts.count))
        XCTAssertEqual(parts[0].name, "part-0000.zip")
        XCTAssertEqual(parts.reduce(0) { $0 + $1.bytes }, input.reduce(0) { $0 + $1.size })
        for p in parts { XCTAssertLessThanOrEqual(p.bytes, 4 << 20) }
    }

    func testFrameFilesStayTogether() {
        let parts = UploadPlanner.plan(files: files(), targetBytes: 1 << 20)
        for i in 2..<102 {
            let stem = CaptureNaming.frameStem(i)
            let owners = Set(parts.filter { part in part.files.contains { path in path.contains(stem) } }.map(\.index))
            XCTAssertLessThanOrEqual(owners.count, 2, stem)  // grouping is greedy by size, splits are rare
        }
        let ordered = UploadPlanner.ordered(files()).map(\.path)
        let i = ordered.firstIndex(of: "frames/000002.jpg")!
        XCTAssertEqual(Set(ordered[(i - 2)...i]), ["confidence/000002.png", "depth/000002.png", "frames/000002.jpg"])
    }

    func testOversizedFileGetsOwnPartAndEntryCap() {
        let parts = UploadPlanner.plan(files: [UploadFile(path: "a.bin", size: 100), UploadFile(path: "frames/big.jpg", size: 10_000)],
                                       targetBytes: 1000)
        XCTAssertEqual(parts.count, 2)
        let many = (0..<25).map { UploadFile(path: "frames/\($0).jpg", size: 1) }
        XCTAssertEqual(UploadPlanner.plan(files: many, targetBytes: 1 << 30, maxEntries: 10).count, 3)
        XCTAssertTrue(UploadPlanner.plan(files: []).isEmpty)
    }

    func testUploadStateResume() throws {
        let parts = UploadPlanner.plan(files: files(), targetBytes: 4 << 20)
        var s = UploadState(captureId: "cap_1", siteId: "site_1", parts: parts)
        XCTAssertEqual(s.pendingParts.count, parts.count)
        s.record(PartReceipt(index: 1, name: parts[1].name, size: 10, sha256: "x", etag: "\"e\""))
        s.record(PartReceipt(index: 0, name: parts[0].name, size: 10, sha256: "y", etag: nil))
        s.record(PartReceipt(index: 1, name: parts[1].name, size: 11, sha256: "z", etag: nil))
        XCTAssertEqual(s.receipts.map(\.index), [0, 1])
        XCTAssertEqual(s.receipts[1].sha256, "z")
        XCTAssertEqual(s.pendingParts.map(\.index), Array(2..<parts.count))
        let enc = JSONEncoder(); enc.dateEncodingStrategy = .iso8601
        let dec = JSONDecoder(); dec.dateDecodingStrategy = .iso8601
        let back = try dec.decode(UploadState.self, from: enc.encode(s))
        XCTAssertEqual(back.receipts, s.receipts)
        XCTAssertEqual(back.parts, s.parts)
        XCTAssertGreaterThan(back.fraction, 0)
    }

    func testRegisterResponseShapes() throws {
        let a = try RegisterCaptureResponse.parse(Data(#"{"capture_id":"cap_9","upload_urls":["https://s3/x?sig=1","https://s3/y"]}"#.utf8))
        XCTAssertEqual(a.captureId, "cap_9")
        XCTAssertEqual(a.parts.map(\.index), [0, 1])
        XCTAssertEqual(a.parts[0].method, "PUT")
        let b = try RegisterCaptureResponse.parse(Data(#"""
        {"id":"cap_8","upload":{"parts":[{"index":1,"url":"/upload/1","headers":{"Content-Type":"application/zip"}},
        {"index":0,"url":"/upload/0","method":"put"}]},"complete_url":"/captures/cap_8/complete"}
        """#.utf8), base: URL(string: "http://10.0.0.2:8000/"))
        XCTAssertEqual(b.captureId, "cap_8")
        XCTAssertEqual(b.parts.map(\.index), [0, 1])
        XCTAssertEqual(b.parts[0].url.absoluteString, "http://10.0.0.2:8000/upload/0")
        XCTAssertEqual(b.parts[1].headers["Content-Type"], "application/zip")
        XCTAssertEqual(b.completeURL?.absoluteString, "http://10.0.0.2:8000/captures/cap_8/complete")
        XCTAssertThrowsError(try RegisterCaptureResponse.parse(Data("[]".utf8)))
        XCTAssertThrowsError(try RegisterCaptureResponse.parse(Data(#"{"capture_id":"c"}"#.utf8)))
    }
}
