import Foundation
import ImageIO
import XCTest
@testable import ClearanceKit

final class ChecksumTests: XCTestCase {
    func testCRC32KnownVector() {
        XCTAssertEqual(CRC32.checksum(Data("123456789".utf8)), 0xCBF4_3926)
        XCTAssertEqual(CRC32.checksum(Data()), 0)
    }

    func testCRC32Continuation() {
        let a = Data("hello ".utf8), b = Data("world".utf8)
        XCTAssertEqual(CRC32.checksum(b, previous: CRC32.checksum(a)), CRC32.checksum(a + b))
    }

    func testAdler32KnownVector() {
        XCTAssertEqual(Adler32.checksum(Data("Wikipedia".utf8)), 0x11E6_0398)
        XCTAssertEqual(Adler32.checksum(Data()), 1)
        // Long input exercises the deferred modulo.
        let big = Data(repeating: 0xFF, count: 100_000)
        var a: UInt32 = 1, b: UInt32 = 0
        for byte in big { a = (a + UInt32(byte)) % 65_521; b = (b + a) % 65_521 }
        XCTAssertEqual(Adler32.checksum(big), (b << 16) | a)
    }
}

final class DeflateTests: XCTestCase {
    func testRoundTrip() {
        let text = Data(String(repeating: "{\"frame\":\"000001.jpg\",\"points_world\":[[1.0,2.0,0.5]]}\n", count: 500).utf8)
        let c = Deflate.compress(text)
        XCTAssertNotNil(c)
        XCTAssertLessThan(c!.count, text.count / 5)
        XCTAssertEqual(Deflate.decompress(c!, expectedSize: text.count), text)
    }

    func testIncompressibleReturnsNil() {
        var rng = SystemRandomNumberGenerator()
        let noise = Data((0..<4096).map { _ in UInt8.random(in: 0...255, using: &rng) })
        XCTAssertNil(Deflate.compress(noise))
        XCTAssertNil(Deflate.compress(Data()))
    }

    func testStoredBlocksDecodeWithSystemInflater() {
        for n in [0, 1, 65_535, 65_536, 200_000] {
            let d = Data((0..<n).map { UInt8(truncatingIfNeeded: $0 &* 31) })
            let s = Deflate.storedBlocks(d)
            XCTAssertEqual(s.count, n + max(1, (n + 65_534) / 65_535) * 5)
            XCTAssertEqual(Deflate.decompress(s, expectedSize: n), d, "n=\(n)")
        }
    }

    func testZlibStreamFraming() {
        let d = Data("abcabcabcabcabcabcabcabc".utf8)
        let z = Deflate.zlibStream(d)
        XCTAssertEqual(z[0], 0x78)
        XCTAssertEqual((UInt16(z[0]) << 8 | UInt16(z[1])) % 31, 0)
        XCTAssertEqual(z.readBE(UInt32.self, at: z.count - 4), Adler32.checksum(d))
        XCTAssertEqual(Deflate.decompress(z.subdata(in: 2..<(z.count - 4)), expectedSize: d.count), d)
    }
}

final class ZipTests: XCTestCase {
    var dir: URL!

    override func setUpWithError() throws {
        dir = FileManager.default.temporaryDirectory.appendingPathComponent("zip-\(UUID().uuidString)")
        try FileManager.default.createDirectory(at: dir, withIntermediateDirectories: true)
    }

    override func tearDownWithError() throws { try? FileManager.default.removeItem(at: dir) }

    func testWriteAndReadBack() throws {
        let url = dir.appendingPathComponent("part-0000.zip")
        let z = try ZipWriter(url: url)
        let json = Data(String(repeating: "{\"t\":1.0}\n", count: 1000).utf8)
        let jpeg = Data((0..<5000).map { UInt8(truncatingIfNeeded: $0 &* 7919) })
        try z.add(name: "capture.json", data: Data("{}".utf8))
        try z.add(name: "poses.jsonl", data: json)
        try z.add(name: "frames/000000.jpg", data: jpeg, method: .store)
        try z.add(name: "empty.txt", data: Data())
        try z.add(name: "dépôt/ünïcode.txt", data: Data("ok".utf8))
        try z.finish()

        let r = try ZipArchiveReader(data: try Data(contentsOf: url))
        XCTAssertEqual(r.entries.map(\.name), ["capture.json", "poses.jsonl", "frames/000000.jpg", "empty.txt", "dépôt/ünïcode.txt"])
        XCTAssertEqual(r.entries[1].method, 8)
        XCTAssertEqual(r.entries[2].method, 0)
        XCTAssertEqual(try r.contents(named: "poses.jsonl"), json)
        XCTAssertEqual(try r.contents(named: "frames/000000.jpg"), jpeg)
        XCTAssertEqual(try r.contents(named: "empty.txt"), Data())
        XCTAssertEqual(try r.contents(named: "dépôt/ünïcode.txt"), Data("ok".utf8))
    }

    func testRejectsBadNames() throws {
        let z = try ZipWriter(url: dir.appendingPathComponent("a.zip"))
        XCTAssertThrowsError(try z.add(name: "../x", data: Data()))
        XCTAssertThrowsError(try z.add(name: "/abs", data: Data()))
        XCTAssertThrowsError(try z.add(name: "a\\b", data: Data()))
        try z.add(name: "a", data: Data())
        XCTAssertThrowsError(try z.add(name: "a", data: Data())) { XCTAssertEqual($0 as? ZipError, .duplicateName("a")) }
        try z.finish()
        XCTAssertThrowsError(try z.add(name: "b", data: Data()))
    }

    func testAddFileUsesPreferredMethod() throws {
        let src = dir.appendingPathComponent("feature_points.jsonl")
        try Data(String(repeating: "[1.000,2.000,0.000],", count: 2000).utf8).write(to: src)
        let png = dir.appendingPathComponent("d.png")
        try PNGWriter.gray8(width: 4, height: 4, pixels: Array(repeating: 7, count: 16)).write(to: png)
        let url = dir.appendingPathComponent("p.zip")
        let z = try ZipWriter(url: url)
        try z.addFile(at: src, name: "feature_points.jsonl")
        try z.addFile(at: png, name: "depth/000000.png")
        try z.finish()
        let r = try ZipArchiveReader(data: try Data(contentsOf: url))
        XCTAssertEqual(r.entries[0].method, 8)
        XCTAssertEqual(r.entries[1].method, 0)
    }

    func testManyEntries() throws {
        let url = dir.appendingPathComponent("many.zip")
        let z = try ZipWriter(url: url)
        for i in 0..<3000 { try z.add(name: "frames/\(CaptureNaming.frameName(i))", data: Data([UInt8(i & 0xFF)])) }
        try z.finish()
        let r = try ZipArchiveReader(data: try Data(contentsOf: url))
        XCTAssertEqual(r.entries.count, 3000)
        XCTAssertEqual(try r.contents(of: r.entries[2999]), Data([UInt8(2999 & 0xFF)]))
    }

    func testCorruptionDetected() throws {
        let url = dir.appendingPathComponent("c.zip")
        let z = try ZipWriter(url: url)
        try z.add(name: "x.bin", data: Data(repeating: 1, count: 100), method: .store)
        try z.finish()
        var data = try Data(contentsOf: url)
        data[30 + 5 + 10] ^= 0xFF   // flip a payload byte
        let r = try ZipArchiveReader(data: data)
        XCTAssertThrowsError(try r.contents(of: r.entries[0]))
        XCTAssertThrowsError(try ZipArchiveReader(data: Data([1, 2, 3])))
    }

    func testDosDateTime() {
        var cal = Calendar(identifier: .gregorian)
        cal.timeZone = TimeZone(identifier: "UTC")!
        let d = cal.date(from: DateComponents(year: 2026, month: 10, day: 27, hour: 13, minute: 45, second: 31))!
        let (t, day) = ZipWriter.dosDateTime(d, calendar: cal)
        XCTAssertEqual(t, UInt16((13 << 11) | (45 << 5) | 15))
        XCTAssertEqual(day, UInt16(((2026 - 1980) << 9) | (10 << 5) | 27))
    }

    #if os(macOS)
    /// Cross-check with the system unzip tool.
    func testSystemUnzipAcceptsArchive() throws {
        let url = dir.appendingPathComponent("sys.zip")
        let z = try ZipWriter(url: url)
        try z.add(name: "capture.json", data: Data("{\"capture_id\":\"cap_1\"}".utf8))
        try z.add(name: "poses.jsonl", data: Data(String(repeating: "{\"t\":0.5}\n", count: 300).utf8))
        try z.add(name: "frames/000000.jpg", data: Data(repeating: 9, count: 1000), method: .store)
        try z.finish()
        let p = Process()
        p.executableURL = URL(fileURLWithPath: "/usr/bin/unzip")
        p.arguments = ["-tq", url.path]
        try p.run()
        p.waitUntilExit()
        XCTAssertEqual(p.terminationStatus, 0)
    }
    #endif
}

final class PNGTests: XCTestCase {
    func decode(_ data: Data) -> CGImage? {
        guard let src = CGImageSourceCreateWithData(data as CFData, nil) else { return nil }
        return CGImageSourceCreateImageAtIndex(src, 0, nil)
    }

    func testChunkLayout() {
        let png = PNGWriter.gray16(width: 3, height: 2, pixels: [0, 1, 255, 256, 1000, 65_535])
        XCTAssertEqual(png.prefix(8), PNGWriter.signature)
        XCTAssertEqual(png.readBE(UInt32.self, at: 8), 13)
        XCTAssertEqual(String(data: png.subdata(in: 12..<16), encoding: .ascii), "IHDR")
        XCTAssertEqual(png.readBE(UInt32.self, at: 16), 3)
        XCTAssertEqual(png.readBE(UInt32.self, at: 20), 2)
        XCTAssertEqual(png[24], 16)
        XCTAssertEqual(png[25], 0)
        XCTAssertEqual(png.readBE(UInt32.self, at: 29), CRC32.checksum(png.subdata(in: 12..<29)))
        XCTAssertEqual(String(data: png.suffix(8).prefix(4), encoding: .ascii), "IEND")
    }

    func testSixteenBitValuesSurviveImageIO() {
        let w = 8, h = 4
        let px: [UInt16] = (0..<(w * h)).map { UInt16($0 * 2000) }
        let img = decode(PNGWriter.gray16(width: w, height: h, pixels: px))
        XCTAssertNotNil(img)
        XCTAssertEqual(img?.width, w)
        XCTAssertEqual(img?.height, h)
        XCTAssertEqual(img?.bitsPerComponent, 16)
        guard let cf = img?.dataProvider?.data, let bytes = CFDataGetBytePtr(cf), let bpr = img?.bytesPerRow else {
            return XCTFail("no pixel data")
        }
        let big = img!.bitmapInfo.contains(.byteOrder16Big) || !img!.bitmapInfo.contains(.byteOrder16Little)
        for y in 0..<h {
            for x in 0..<w {
                let o = y * bpr + x * 2
                let v = big ? (UInt16(bytes[o]) << 8 | UInt16(bytes[o + 1])) : (UInt16(bytes[o + 1]) << 8 | UInt16(bytes[o]))
                XCTAssertEqual(v, px[y * w + x])
            }
        }
    }

    func testEightBitDecodes() {
        let px: [UInt8] = [0, 1, 2, 2, 1, 0]
        let img = decode(PNGWriter.gray8(width: 3, height: 2, pixels: px))
        XCTAssertEqual(img?.bitsPerComponent, 8)
        XCTAssertEqual(img?.width, 3)
    }

    func testLargeDepthMapCompresses() {
        let w = 256, h = 192
        let px = (0..<(w * h)).map { UInt16(1500 + ($0 % w)) }
        let png = PNGWriter.gray16(width: w, height: h, pixels: px)
        XCTAssertLessThan(png.count, w * h * 2)
        XCTAssertEqual(decode(png)?.height, h)
    }

    func testDepthEncoding() {
        XCTAssertEqual(DepthEncoding.millimetres(fromMetres: [0, -1, .nan, .infinity, 0.0015, 1.2344, 70]),
                       [0, 0, 0, 0, 2, 1234, 65_535])
    }
}
