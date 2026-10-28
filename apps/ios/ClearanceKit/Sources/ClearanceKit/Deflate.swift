import Compression
import Foundation

/// Raw DEFLATE (RFC 1951) helpers. Apple's `COMPRESSION_ZLIB` encodes and decodes raw DEFLATE
/// without the zlib header or trailer, which is exactly what ZIP method 8 stores.
public enum Deflate {
    /// Compresses with Apple's Compression framework. Returns nil when the result would not be
    /// smaller than the input (callers then store the bytes uncompressed).
    public static func compress(_ data: Data) -> Data? {
        guard !data.isEmpty else { return nil }
        let capacity = data.count
        var out = Data(count: capacity)
        let n = out.withUnsafeMutableBytes { (dst: UnsafeMutableRawBufferPointer) -> Int in
            data.withUnsafeBytes { (src: UnsafeRawBufferPointer) -> Int in
                guard let d = dst.bindMemory(to: UInt8.self).baseAddress,
                      let s = src.bindMemory(to: UInt8.self).baseAddress else { return 0 }
                return compression_encode_buffer(d, capacity, s, data.count, nil, COMPRESSION_ZLIB)
            }
        }
        guard n > 0, n < data.count else { return nil }
        out.count = n
        return out
    }

    /// Decompresses raw DEFLATE when the decompressed size is known (ZIP and PNG both know it).
    public static func decompress(_ data: Data, expectedSize: Int) -> Data? {
        if expectedSize == 0 { return Data() }
        guard !data.isEmpty else { return nil }
        var out = Data(count: expectedSize)
        let n = out.withUnsafeMutableBytes { (dst: UnsafeMutableRawBufferPointer) -> Int in
            data.withUnsafeBytes { (src: UnsafeRawBufferPointer) -> Int in
                guard let d = dst.bindMemory(to: UInt8.self).baseAddress,
                      let s = src.bindMemory(to: UInt8.self).baseAddress else { return 0 }
                return compression_decode_buffer(d, expectedSize, s, data.count, nil, COMPRESSION_ZLIB)
            }
        }
        guard n == expectedSize else { return nil }
        return out
    }

    /// Raw DEFLATE made only of stored (uncompressed) blocks; always valid, pure Swift.
    public static func storedBlocks(_ data: Data) -> Data {
        var out = Data()
        out.reserveCapacity(data.count + (data.count / 65_535 + 1) * 5)
        if data.isEmpty {
            out.append(contentsOf: [0x01, 0x00, 0x00, 0xFF, 0xFF])
            return out
        }
        var offset = 0
        while offset < data.count {
            let len = min(65_535, data.count - offset)
            let final = offset + len == data.count
            out.append(final ? 0x01 : 0x00)
            out.appendLE(UInt16(len))
            out.appendLE(~UInt16(len))
            out.append(data.subdata(in: (data.startIndex + offset)..<(data.startIndex + offset + len)))
            offset += len
        }
        return out
    }

    /// zlib stream (RFC 1950) around raw DEFLATE: header 0x78 0x9C, big-endian Adler-32 trailer.
    public static func zlibStream(_ data: Data) -> Data {
        var out = Data([0x78, 0x9C])
        out.append(compress(data) ?? storedBlocks(data))
        out.appendBE(Adler32.checksum(data))
        return out
    }
}
