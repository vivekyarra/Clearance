import Foundation
import XCTest
@testable import ClearanceKit

/// Mirrors services/api (tests/integration/api/test_api_contract_routes.py) and docs/CONTRACTS.md.
final class APIContractTests: XCTestCase {
    func testRegisterBodyMatchesCaptureCreate() throws {
        var m = CaptureManifest(captureId: "cap_local", siteId: "site_1",
                                device: CaptureDevice(model: "iPhone14,5", os: "iOS 18.1", tier: .vioMono))
        m.startedAt = "2026-10-27T10:00:00Z"
        m.durationS = 95.5
        m.files.relativeDepth = "reldepth"
        let body = try RegisterCaptureRequest.body(manifest: m, nParts: 7)
        XCTAssertEqual(Set(body.keys), ["device", "role", "marker", "n_chunks", "chunking", "started_at", "duration_s",
                                        "privacy", "capture_files"])
        XCTAssertEqual(body["n_chunks"] as? Int, 7)
        XCTAssertEqual(body["chunking"] as? String, "independent")
        XCTAssertEqual(body["role"] as? String, "scanner")
        XCTAssertEqual((body["device"] as? [String: Any])?["tier"] as? String, "T2_VIO_MONO")
        XCTAssertEqual((body["capture_files"] as? [String: Any])?["relative_depth"] as? String, "reldepth")
        XCTAssertNoThrow(try JSONSerialization.data(withJSONObject: body))
        XCTAssertEqual(RegisterCaptureRequest.refreshBody(parts: [2, 5])["parts"] as? [Int], [2, 5])
    }

    func testParsesAPIRegisterResponse() throws {
        let json = #"""
        {"capture_id":"cap_ab12","site_id":"site_1","status":"registered","files":[],
         "upload":{"chunks":[{"part":1,"key":"captures/cap_ab12/upload/part_0001.zip","method":"PUT",
                              "url":"http://10.0.0.2:8000/upload/t1","headers":{"Content-Type":"application/octet-stream"},
                              "expires_in_s":3600},
                             {"part":0,"key":"captures/cap_ab12/upload/part_0000.zip","method":"PUT",
                              "url":"http://10.0.0.2:8000/upload/t0","headers":{},"expires_in_s":3600}],
                   "files":[]},
         "complete_url":"http://10.0.0.2:8000/captures/cap_ab12/complete"}
        """#
        let r = try RegisterCaptureResponse.parse(Data(json.utf8))
        XCTAssertEqual(r.captureId, "cap_ab12")
        XCTAssertEqual(r.parts.map(\.index), [0, 1])
        XCTAssertEqual(r.parts[1].url.absoluteString, "http://10.0.0.2:8000/upload/t1")
        XCTAssertEqual(r.parts[1].headers["Content-Type"], "application/octet-stream")
        XCTAssertEqual(r.completeURL?.absoluteString, "http://10.0.0.2:8000/captures/cap_ab12/complete")
        // POST /captures/{id}/upload-urls answers with the same "upload" object
        let refresh = try RegisterCaptureResponse.parse(Data(#"""
        {"capture_id":"cap_ab12","upload":{"chunks":[{"part":1,"url":"/upload/t9","method":"PUT","headers":{}}],"files":[]}}
        """#.utf8), base: URL(string: "http://10.0.0.2:8000/"))
        XCTAssertEqual(refresh.parts.map(\.index), [1])
        XCTAssertEqual(refresh.parts[0].url.absoluteString, "http://10.0.0.2:8000/upload/t9")
    }

    func testSiteQRJoinURL() {
        // GET /sites/{id}/qr.png encodes <web>/join/<site_id>?api=<API base>&name=<site name>
        let j = SiteJoinParser.parse("http://192.168.1.20:5173/join/site_ab12?api=http%3A%2F%2F192.168.1.20%3A8000&name=Corner%20Caf%C3%A9")
        XCTAssertEqual(j?.apiBase.absoluteString, "http://192.168.1.20:8000")
        XCTAssertEqual(j?.siteId, "site_ab12")
        XCTAssertEqual(j?.siteName, "Corner Café")
        // the /join page's "Open in the Clearance app" link
        let a = SiteJoinParser.parse("clearance://join?api=http%3A%2F%2F192.168.1.20%3A8000&site=site_ab12&name=Corner%20Cafe")
        XCTAssertEqual(a?.apiBase.absoluteString, "http://192.168.1.20:8000")
        XCTAssertEqual(a?.displayName, "Corner Cafe")
        // the API's JSON site payload ("qr" object) also parses
        let p = SiteJoinParser.parse(#"{"v":1,"kind":"clearance-site","site_id":"site_ab12","name":"Cafe","api_base":"http://h:8000","join_url":"http://h:5173/join/site_ab12"}"#)
        XCTAssertEqual(p?.apiBase.absoluteString, "http://h:8000")
    }

    func testCoachMessageCoverageLayout() throws {
        var g = CoverageGrid(cellSize: 0.5, minPointsPerView: 1)
        // seen once from the west: cell (2, 0) single; seen from west and north: cell (2, 1) covered
        g.observe(cameraPosition: SIMD3(0.25, 0.25, 1.4), points: [SIMD3(1.25, 0.25, 0.5), SIMD3(1.25, 0.75, 0.5)])
        g.observe(cameraPosition: SIMD3(1.25, 2.75, 1.4), points: [SIMD3(1.25, 0.75, 0.5)])
        let cov = try XCTUnwrap(CoachMessage.coverage(g))
        let w = try XCTUnwrap(cov["width"] as? Int), h = try XCTUnwrap(cov["height"] as? Int)
        let cells = try XCTUnwrap(cov["cells"] as? [Int])
        XCTAssertEqual(cells.count, w * h)
        XCTAssertEqual(cov["origin"] as? [Double], [0, 0])
        XCTAssertEqual(cov["cell_m"] as? Double, 0.5)
        // row 0 = max y
        func at(_ ix: Int, _ iy: Int) -> Int { cells[(h - 1 - iy) * w + ix] }
        XCTAssertEqual(at(2, 0), 1)
        XCTAssertEqual(at(2, 1), 2)
        XCTAssertEqual(at(0, 0), 0)

        let msg = CoachMessage.make(siteId: "site_1", grid: g, prompt: "Walk left", t: 12.5, device: "iPhone14,5",
                                    frames: 40, tracking: "normal")
        let back = try JSONSerialization.jsonObject(with: Data(CoachMessage.encode(msg).utf8)) as! [String: Any]
        XCTAssertEqual(back["type"] as? String, "coach")
        XCTAssertEqual(back["site_id"] as? String, "site_1")
        XCTAssertEqual(back["prompt"] as? String, "Walk left")
        XCTAssertEqual(back["t"] as? Double, 12.5)
        XCTAssertEqual((back["status"] as? [String: Any])?["frames"] as? Int, 40)
        let empty = CoachMessage.make(siteId: "s", grid: nil, prompt: nil, t: 1)
        XCTAssertTrue(empty["coverage"] is NSNull && empty["prompt"] is NSNull && empty["status"] == nil)
    }
    // ---------------------------------------------------------------- transport security + tokens
    func testAPIHostPolicyAllowsHttpOnlyOnTheLocalNetwork() {
        for host in ["localhost", "127.0.0.1", "10.1.2.3", "172.16.0.9", "172.31.255.1", "192.168.1.20",
                     "169.254.10.10", "laptop.local", "laptop", "::1", "fe80::1", "fd12:3456::1", "[fe80::1]"] {
            XCTAssertTrue(APIHostPolicy.allowed(scheme: "http", host: host), host)
        }
        for host in ["example.com", "8.8.8.8", "172.32.0.1", "192.169.1.1", "11.0.0.1", "evil.local.example.com",
                     "2001:db8::1", "1.2.3", "300.1.1.1", ""] {
            XCTAssertFalse(APIHostPolicy.allowed(scheme: "http", host: host), host)
        }
        XCTAssertTrue(APIHostPolicy.allowed(scheme: "https", host: "api.example.com"))
        XCTAssertFalse(APIHostPolicy.allowed(scheme: "ftp", host: "10.0.0.1"))
    }

    func testJoinLinksToPublicHttpHostsAreRefused() {
        // a QR code that redirects uploads to a public plain-http server is not a valid join
        XCTAssertNil(SiteJoinParser.parse("clearance://join?api=http%3A%2F%2Fevil.example.com&site=site_ab12"))
        XCTAssertNil(SiteJoinParser.parse(#"{"api":"http://203.0.113.5:8000","site_id":"site_ab12"}"#))
        XCTAssertNil(SiteJoinParser.parse("http://evil.example.com/join/site_ab12"))
        // an explicit but refused api item is never replaced by the page host
        XCTAssertNil(SiteJoinParser.parse("https://console.example.com/join/site_ab12?api=http%3A%2F%2Fevil.example.com"))
        XCTAssertNil(SiteJoinParser.parse("clearance://join?api=https%3A%2F%2Fuser%3Apw%40api.example.com&site=s"))
        let ok = SiteJoinParser.parse("clearance://join?api=https%3A%2F%2Fapi.example.com%3A8443&site=site_ab12&token=jt.mac")
        XCTAssertEqual(ok?.apiHostDisplay, "api.example.com:8443")
        XCTAssertEqual(ok?.token, "jt.mac")
        let lan = SiteJoinParser.parse("http://192.168.1.20:5173/join/site_ab12?api=http%3A%2F%2F192.168.1.20%3A8000&token=jt.mac")
        XCTAssertEqual(lan?.apiHostDisplay, "192.168.1.20:8000")
        XCTAssertEqual(lan?.token, "jt.mac")
    }

    func testRegisterResponseCarriesDeviceTokenAndPresignedPost() throws {
        let json = #"""
        {"capture_id":"cap_1","device_token":"dev.tok","device_token_expires_in_s":172800,
         "upload":{"chunks":[{"part":0,"key":"captures/cap_1/upload/part_0000.zip","method":"POST",
                              "url":"https://storage.example.com/bucket","fields":{"key":"p/captures/cap_1/upload/part_0000.zip","policy":"pol"},
                              "file_field":"file","headers":{},"expires_in_s":3600,"max_bytes":1073741824}]}}
        """#
        let r = try RegisterCaptureResponse.parse(Data(json.utf8))
        XCTAssertEqual(r.deviceToken, "dev.tok")
        XCTAssertEqual(r.parts[0].method, "POST")
        XCTAssertEqual(r.parts[0].fields["policy"], "pol")
        let m = r.parts[0].multipart(boundary: "B", filename: "part_0000.zip")
        let head = String(decoding: m.prefix, as: UTF8.self)
        XCTAssertTrue(head.hasPrefix("--B\r\nContent-Disposition: form-data; name=\"key\"\r\n\r\np/captures/cap_1/upload/part_0000.zip\r\n"))
        XCTAssertTrue(head.hasSuffix("name=\"file\"; filename=\"part_0000.zip\"\r\nContent-Type: application/zip\r\n\r\n"))
        XCTAssertLessThan(head.range(of: "policy")!.lowerBound, head.range(of: "name=\"file\"")!.lowerBound)
        XCTAssertEqual(String(decoding: m.suffix, as: UTF8.self), "\r\n--B--\r\n")
        XCTAssertEqual(m.contentType, "multipart/form-data; boundary=B")
    }

    func testUploadStateKeepsDeviceTokenAndReadsOlderFiles() throws {
        var st = UploadState(captureId: "cap_1", siteId: "s", parts: [])
        st.deviceToken = "dev.tok"
        let back = try JSONDecoder().decode(UploadState.self, from: try JSONEncoder().encode(st))
        XCTAssertEqual(back.deviceToken, "dev.tok")
        var old = try JSONSerialization.jsonObject(with: try JSONEncoder().encode(st)) as! [String: Any]
        old.removeValue(forKey: "deviceToken")
        let legacy = try JSONDecoder().decode(UploadState.self, from: try JSONSerialization.data(withJSONObject: old))
        XCTAssertNil(legacy.deviceToken)
    }
}
