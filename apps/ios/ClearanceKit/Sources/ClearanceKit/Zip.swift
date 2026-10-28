import Foundation

public enum ZipError: Error, Equatable {
    case tooLarge
    case tooManyEntries
    case duplicateName(String)
    case invalidName(String)
    case finished
    case corrupt(String)
}

/// Minimal ZIP writer (PKWARE APPNOTE 6.3): stored or deflated entries, UTF-8 names, no ZIP64.
/// Each upload part stays far below 4 GiB and 65 535 entries; the writer refuses anything larger.
public final class ZipWriter {
    public enum Method: Sendable { case store, deflate, automatic }

    private struct Entry {
        let name: Data
        let method: UInt16
        let dosTime: UInt16
        let dosDate: UInt16
        let crc: UInt32
        let compressedSize: UInt32
        let size: UInt32
        let offset: UInt32
    }

    private let handle: FileHandle
    private var offset: UInt64 = 0
    private var entries: [Entry] = []
    private var names: Set<String> = []
    private var isFinished = false
    public let url: URL

    public init(url: URL) throws {
        self.url = url
        FileManager.default.createFile(atPath: url.path, contents: nil)
        handle = try FileHandle(forWritingTo: url)
        try handle.truncate(atOffset: 0)
    }

    deinit { try? handle.close() }

    public var entryCount: Int { entries.count }
    public var bytesWritten: UInt64 { offset }

    /// File types that are already compressed are stored; everything else is deflated when that helps.
    public static func preferredMethod(for name: String) -> Method {
        let ext = (name as NSString).pathExtension.lowercased()
        return ["jpg", "jpeg", "png", "heic", "mp4", "mov", "zip", "usdz", "webm"].contains(ext) ? .store : .automatic
    }

    public func addFile(at fileURL: URL, name: String, method: Method? = nil, modified: Date? = nil) throws {
        let data = try Data(contentsOf: fileURL, options: .mappedIfSafe)
        let date = modified ?? ((try? fileURL.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate ?? Date())
        try add(name: name, data: data, method: method ?? ZipWriter.preferredMethod(for: name), modified: date)
    }

    public func add(name: String, data: Data, method: Method = .automatic, modified: Date = Date()) throws {
        guard !isFinished else { throw ZipError.finished }
        guard !name.isEmpty, !name.hasPrefix("/"), !name.contains("\\"),
              !name.split(separator: "/").contains("..") else { throw ZipError.invalidName(name) }
        guard !names.contains(name) else { throw ZipError.duplicateName(name) }
        guard entries.count < 65_535 else { throw ZipError.tooManyEntries }
        guard data.count < Int(UInt32.max) else { throw ZipError.tooLarge }

        let crc = CRC32.checksum(data)
        var payload = data
        var methodCode: UInt16 = 0
        if method != .store, let deflated = Deflate.compress(data) {
            payload = deflated
            methodCode = 8
        }
        let (time, date) = ZipWriter.dosDateTime(modified)
        let nameData = Data(name.utf8)
        guard offset + UInt64(30 + nameData.count + payload.count) < UInt64(UInt32.max) else { throw ZipError.tooLarge }

        var header = Data()
        header.appendLE(UInt32(0x0403_4B50))
        header.appendLE(UInt16(20))          // version needed: 2.0
        header.appendLE(UInt16(0x0800))      // flags: UTF-8 names
        header.appendLE(methodCode)
        header.appendLE(time)
        header.appendLE(date)
        header.appendLE(crc)
        header.appendLE(UInt32(payload.count))
        header.appendLE(UInt32(data.count))
        header.appendLE(UInt16(nameData.count))
        header.appendLE(UInt16(0))           // extra length
        header.append(nameData)

        let entry = Entry(name: nameData, method: methodCode, dosTime: time, dosDate: date, crc: crc,
                          compressedSize: UInt32(payload.count), size: UInt32(data.count), offset: UInt32(offset))
        try handle.write(contentsOf: header)
        try handle.write(contentsOf: payload)
        offset += UInt64(header.count + payload.count)
        entries.append(entry)
        names.insert(name)
    }

    public func finish() throws {
        guard !isFinished else { return }
        var cd = Data()
        for e in entries {
            cd.appendLE(UInt32(0x0201_4B50))
            cd.appendLE(UInt16(0x0314))      // made by: UNIX, spec 2.0
            cd.appendLE(UInt16(20))
            cd.appendLE(UInt16(0x0800))
            cd.appendLE(e.method)
            cd.appendLE(e.dosTime)
            cd.appendLE(e.dosDate)
            cd.appendLE(e.crc)
            cd.appendLE(e.compressedSize)
            cd.appendLE(e.size)
            cd.appendLE(UInt16(e.name.count))
            cd.appendLE(UInt16(0))           // extra
            cd.appendLE(UInt16(0))           // comment
            cd.appendLE(UInt16(0))           // disk number start
            cd.appendLE(UInt16(0))           // internal attributes
            cd.appendLE(UInt32(0o100644) << 16) // external attributes: regular file rw-r--r--
            cd.appendLE(e.offset)
            cd.append(e.name)
        }
        guard offset + UInt64(cd.count) + 22 < UInt64(UInt32.max) else { throw ZipError.tooLarge }
        var eocd = Data()
        eocd.appendLE(UInt32(0x0605_4B50))
        eocd.appendLE(UInt16(0))
        eocd.appendLE(UInt16(0))
        eocd.appendLE(UInt16(entries.count))
        eocd.appendLE(UInt16(entries.count))
        eocd.appendLE(UInt32(cd.count))
        eocd.appendLE(UInt32(offset))
        eocd.appendLE(UInt16(0))
        try handle.write(contentsOf: cd)
        try handle.write(contentsOf: eocd)
        offset += UInt64(cd.count + eocd.count)
        try handle.close()
        isFinished = true
    }

    /// MS-DOS date/time in the local calendar (seconds have 2 s resolution; years clamp to 1980...2107).
    public static func dosDateTime(_ date: Date, calendar: Calendar = Calendar(identifier: .gregorian)) -> (time: UInt16, date: UInt16) {
        let c = calendar.dateComponents([.year, .month, .day, .hour, .minute, .second], from: date)
        let year: Int = min(max((c.year ?? 1980) - 1980, 0), 127)
        let hour: Int = c.hour ?? 0
        let minute: Int = c.minute ?? 0
        let second: Int = c.second ?? 0
        let month: Int = c.month ?? 1
        let dayOfMonth: Int = c.day ?? 1
        let timeBits: Int = (hour << 11) | (minute << 5) | (second / 2)
        let dateBits: Int = (year << 9) | (month << 5) | dayOfMonth
        return (UInt16(timeBits), UInt16(dateBits))
    }
}

/// Reader for archives produced by `ZipWriter` (stored and deflated entries, no ZIP64).
/// Used to verify parts before upload and by the unit tests.
public struct ZipArchiveReader {
    public struct Entry: Equatable {
        public let name: String
        public let method: UInt16
        public let crc: UInt32
        public let compressedSize: Int
        public let size: Int
        public let localHeaderOffset: Int
    }

    public let data: Data
    public let entries: [Entry]

    public init(data: Data) throws {
        self.data = data
        guard data.count >= 22 else { throw ZipError.corrupt("too short") }
        var eocd = -1
        var i = data.count - 22
        let lowest = max(0, data.count - 22 - 65_535)
        while i >= lowest {
            if data.readLE(UInt32.self, at: i) == 0x0605_4B50 { eocd = i; break }
            i -= 1
        }
        guard eocd >= 0,
              let count = data.readLE(UInt16.self, at: eocd + 10),
              let cdSize = data.readLE(UInt32.self, at: eocd + 12),
              let cdOffset = data.readLE(UInt32.self, at: eocd + 16) else { throw ZipError.corrupt("no end record") }
        guard Int(cdOffset) + Int(cdSize) <= eocd else { throw ZipError.corrupt("central directory bounds") }
        var out: [Entry] = []
        var p = Int(cdOffset)
        for _ in 0..<Int(count) {
            guard data.readLE(UInt32.self, at: p) == 0x0201_4B50,
                  let method = data.readLE(UInt16.self, at: p + 10),
                  let crc = data.readLE(UInt32.self, at: p + 16),
                  let csize = data.readLE(UInt32.self, at: p + 20),
                  let size = data.readLE(UInt32.self, at: p + 24),
                  let nlen = data.readLE(UInt16.self, at: p + 28),
                  let elen = data.readLE(UInt16.self, at: p + 30),
                  let clen = data.readLE(UInt16.self, at: p + 32),
                  let off = data.readLE(UInt32.self, at: p + 42) else { throw ZipError.corrupt("central header") }
            let nameStart = data.startIndex + p + 46
            guard p + 46 + Int(nlen) <= data.count,
                  let name = String(data: data.subdata(in: nameStart..<(nameStart + Int(nlen))), encoding: .utf8)
            else { throw ZipError.corrupt("name") }
            out.append(Entry(name: name, method: method, crc: crc, compressedSize: Int(csize), size: Int(size),
                             localHeaderOffset: Int(off)))
            p += 46 + Int(nlen) + Int(elen) + Int(clen)
        }
        entries = out
    }

    public func contents(of entry: Entry) throws -> Data {
        let p = entry.localHeaderOffset
        guard data.readLE(UInt32.self, at: p) == 0x0403_4B50,
              let nlen = data.readLE(UInt16.self, at: p + 26),
              let elen = data.readLE(UInt16.self, at: p + 28) else { throw ZipError.corrupt("local header") }
        let start = p + 30 + Int(nlen) + Int(elen)
        guard start + entry.compressedSize <= data.count else { throw ZipError.corrupt("entry bounds") }
        let raw = data.subdata(in: (data.startIndex + start)..<(data.startIndex + start + entry.compressedSize))
        let bytes: Data
        switch entry.method {
        case 0: bytes = raw
        case 8:
            guard let d = Deflate.decompress(raw, expectedSize: entry.size) else { throw ZipError.corrupt("inflate \(entry.name)") }
            bytes = d
        default: throw ZipError.corrupt("method \(entry.method)")
        }
        guard CRC32.checksum(bytes) == entry.crc else { throw ZipError.corrupt("crc \(entry.name)") }
        return bytes
    }

    public func contents(named name: String) throws -> Data? {
        guard let e = entries.first(where: { $0.name == name }) else { return nil }
        return try contents(of: e)
    }
}
