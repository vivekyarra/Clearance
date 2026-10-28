import Foundation

/// Grayscale PNG encoder (8- and 16-bit). Used for depth (uint16 millimetres) and ARKit
/// confidence maps (uint8 0/1/2), where exact sample values matter more than anything else.
public enum PNGWriter {
    public static let signature = Data([0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A])

    public static func gray16(width: Int, height: Int, pixels: [UInt16]) -> Data {
        precondition(width > 0 && height > 0 && pixels.count == width * height)
        var raw = Data(capacity: height * (1 + width * 2))
        for y in 0..<height {
            raw.append(0)  // filter type: none
            for x in 0..<width {
                let v = pixels[y * width + x]
                raw.append(UInt8(v >> 8))
                raw.append(UInt8(v & 0xFF))
            }
        }
        return encode(width: width, height: height, bitDepth: 16, scanlines: raw)
    }

    public static func gray8(width: Int, height: Int, pixels: [UInt8]) -> Data {
        precondition(width > 0 && height > 0 && pixels.count == width * height)
        var raw = Data(capacity: height * (1 + width))
        pixels.withUnsafeBufferPointer { buf in
            for y in 0..<height {
                raw.append(0)
                raw.append(contentsOf: UnsafeBufferPointer(rebasing: buf[(y * width)..<((y + 1) * width)]))
            }
        }
        return encode(width: width, height: height, bitDepth: 8, scanlines: raw)
    }

    static func encode(width: Int, height: Int, bitDepth: UInt8, scanlines: Data) -> Data {
        var ihdr = Data()
        ihdr.appendBE(UInt32(width))
        ihdr.appendBE(UInt32(height))
        ihdr.append(contentsOf: [bitDepth, 0, 0, 0, 0])  // grayscale, deflate, adaptive filter set 0, no interlace
        var out = signature
        appendChunk(&out, type: "IHDR", data: ihdr)
        appendChunk(&out, type: "IDAT", data: Deflate.zlibStream(scanlines))
        appendChunk(&out, type: "IEND", data: Data())
        return out
    }

    static func appendChunk(_ out: inout Data, type: String, data: Data) {
        let typeData = Data(type.utf8)
        out.appendBE(UInt32(data.count))
        out.append(typeData)
        out.append(data)
        var crcInput = typeData
        crcInput.append(data)
        out.appendBE(CRC32.checksum(crcInput))
    }
}

public enum DepthEncoding {
    /// Metres (Float32, ARKit sceneDepth) to uint16 millimetres; invalid or non-positive -> 0.
    public static func millimetres(fromMetres m: [Float]) -> [UInt16] {
        m.map { v in
            guard v.isFinite, v > 0 else { return 0 }
            let mm = (Double(v) * 1000).rounded()
            return mm >= 65_535 ? 65_535 : UInt16(mm)
        }
    }
}
