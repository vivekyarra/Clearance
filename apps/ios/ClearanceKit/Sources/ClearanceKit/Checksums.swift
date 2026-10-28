import Foundation

/// CRC-32 (IEEE 802.3, reflected polynomial 0xEDB88320), as used by ZIP and PNG.
public enum CRC32 {
    static let table: [UInt32] = (0..<256).map { i -> UInt32 in
        var c = UInt32(i)
        for _ in 0..<8 { c = (c & 1) != 0 ? (0xEDB8_8320 ^ (c >> 1)) : (c >> 1) }
        return c
    }

    /// `previous` lets callers continue a running checksum (pass the earlier result).
    public static func checksum(_ bytes: UnsafeRawBufferPointer, previous: UInt32 = 0) -> UInt32 {
        var crc = ~previous
        table.withUnsafeBufferPointer { t in
            for b in bytes { crc = t[Int((crc ^ UInt32(b)) & 0xFF)] ^ (crc >> 8) }
        }
        return ~crc
    }

    public static func checksum(_ data: Data, previous: UInt32 = 0) -> UInt32 {
        data.withUnsafeBytes { checksum($0, previous: previous) }
    }
}

/// Adler-32 (RFC 1950), the trailer of a zlib stream (used inside PNG IDAT).
public enum Adler32 {
    public static func checksum(_ data: Data) -> UInt32 {
        let mod: UInt32 = 65_521
        var a: UInt32 = 1, b: UInt32 = 0
        data.withUnsafeBytes { (buf: UnsafeRawBufferPointer) in
            var i = 0
            let n = buf.count
            while i < n {
                // 5552 is the largest block that cannot overflow UInt32 before the modulo.
                let end = min(i + 5552, n)
                while i < end {
                    a &+= UInt32(buf[i])
                    b &+= a
                    i += 1
                }
                a %= mod
                b %= mod
            }
        }
        return (b << 16) | a
    }
}

extension Data {
    mutating func appendLE<T: FixedWidthInteger>(_ v: T) {
        var le = v.littleEndian
        Swift.withUnsafeBytes(of: &le) { append(contentsOf: $0) }
    }

    mutating func appendBE<T: FixedWidthInteger>(_ v: T) {
        var be = v.bigEndian
        Swift.withUnsafeBytes(of: &be) { append(contentsOf: $0) }
    }

    func readLE<T: FixedWidthInteger>(_ type: T.Type, at offset: Int) -> T? {
        let size = MemoryLayout<T>.size
        guard offset >= 0, offset + size <= count else { return nil }
        var v: T = 0
        for k in 0..<size {
            v |= T(self[startIndex + offset + k]) << (8 * k)
        }
        return v
    }

    func readBE<T: FixedWidthInteger>(_ type: T.Type, at offset: Int) -> T? {
        let size = MemoryLayout<T>.size
        guard offset >= 0, offset + size <= count else { return nil }
        var v: T = 0
        for k in 0..<size {
            v = (v << 8) | T(self[startIndex + offset + k])
        }
        return v
    }
}
