import Foundation

/// Upload layout: the capture folder is split into independent ZIP parts (each a complete
/// archive with paths relative to the capture root), uploaded one by one to presigned URLs.
/// A part that finished is never sent again, so an interrupted upload resumes where it stopped.
public struct UploadFile: Codable, Equatable, Sendable {
    public var path: String
    public var size: Int64
    public init(path: String, size: Int64) { self.path = path; self.size = size }
}

public struct UploadPart: Codable, Equatable, Sendable {
    public var index: Int
    public var name: String
    public var files: [String]
    public var bytes: Int64
    public init(index: Int, name: String, files: [String], bytes: Int64) {
        self.index = index; self.name = name; self.files = files; self.bytes = bytes
    }
}

public enum UploadPlanner {
    public static func partName(_ i: Int) -> String { String(format: "part-%04d.zip", i) }

    /// Top-level metadata first (capture.json, poses.jsonl, ...), then per-frame files grouped by
    /// frame stem so a frame's JPEG, depth and confidence maps land in the same part.
    public static func ordered(_ files: [UploadFile]) -> [UploadFile] {
        func key(_ f: UploadFile) -> (Int, String, String) {
            let comps = f.path.split(separator: "/")
            if comps.count == 1 {
                let rank = f.path == "capture.json" ? 0 : 1
                return (rank, f.path, "")
            }
            let stem = (String(comps.last!) as NSString).deletingPathExtension
            return (2, stem, f.path)
        }
        return files.sorted { key($0) < key($1) }
    }

    public static func plan(files: [UploadFile], targetBytes: Int64 = 16 << 20, maxEntries: Int = 4000) -> [UploadPart] {
        var parts: [UploadPart] = []
        var cur: [String] = []
        var bytes: Int64 = 0
        func flush() {
            guard !cur.isEmpty else { return }
            parts.append(UploadPart(index: parts.count, name: partName(parts.count), files: cur, bytes: bytes))
            cur = []
            bytes = 0
        }
        for f in ordered(files) {
            if !cur.isEmpty && (bytes + f.size > targetBytes || cur.count >= maxEntries) { flush() }
            cur.append(f.path)
            bytes += f.size
        }
        flush()
        return parts
    }
}

public struct PartReceipt: Codable, Equatable, Sendable {
    public var index: Int
    public var name: String
    public var size: Int64
    public var sha256: String
    public var etag: String?
    public init(index: Int, name: String, size: Int64, sha256: String, etag: String?) {
        self.index = index; self.name = name; self.size = size; self.sha256 = sha256; self.etag = etag
    }
}

/// Persisted as upload_state.json inside the capture folder (never uploaded itself).
public struct UploadState: Codable, Equatable, Sendable {
    public var captureId: String
    public var siteId: String
    public var parts: [UploadPart]
    public var receipts: [PartReceipt] = []
    public var registered = false
    public var completed = false
    public var lastError: String?
    /// Per-capture device token from registration: authorizes upload-url refreshes and complete for this
    /// capture after the (short-lived) site join token has expired.
    public var deviceToken: String?
    public var updatedAt: Date = Date()

    public init(captureId: String, siteId: String, parts: [UploadPart]) {
        self.captureId = captureId; self.siteId = siteId; self.parts = parts
    }

    public func isDone(_ index: Int) -> Bool { receipts.contains { $0.index == index } }
    public var pendingParts: [UploadPart] { parts.filter { !isDone($0.index) } }
    public var totalBytes: Int64 { parts.reduce(0) { $0 + $1.bytes } }
    public var doneBytes: Int64 {
        parts.filter { isDone($0.index) }.reduce(0) { $0 + $1.bytes }
    }
    public var fraction: Double { totalBytes == 0 ? (completed ? 1 : 0) : Double(doneBytes) / Double(totalBytes) }

    public mutating func record(_ r: PartReceipt) {
        receipts.removeAll { $0.index == r.index }
        receipts.append(r)
        receipts.sort { $0.index < $1.index }
        updatedAt = Date()
    }
}

/// Presigned target for one part. `PUT`: the body is the file. `POST` (S3 presigned POST): a
/// multipart/form-data body with `fields` first and the file last, in the form field `fileField`.
public struct PresignedPart: Equatable, Sendable {
    public var index: Int
    public var url: URL
    public var method: String
    public var headers: [String: String]
    public var fields: [String: String]
    public var fileField: String
    public init(index: Int, url: URL, method: String = "PUT", headers: [String: String] = [:],
                fields: [String: String] = [:], fileField: String = "file") {
        self.index = index; self.url = url; self.method = method; self.headers = headers
        self.fields = fields; self.fileField = fileField
    }

    /// Multipart framing for a presigned POST: write `prefix`, then the file bytes, then `suffix`.
    public func multipart(boundary: String, filename: String) -> (prefix: Data, suffix: Data, contentType: String) {
        let crlf = "\r\n"
        var head = ""
        for (k, v) in fields.sorted(by: { $0.key < $1.key }) {
            head += "--\(boundary)" + crlf + "Content-Disposition: form-data; name=\"\(k)\"" + crlf + crlf + v + crlf
        }
        head += "--\(boundary)" + crlf
        head += "Content-Disposition: form-data; name=\"\(fileField)\"; filename=\"\(filename)\"" + crlf
        head += "Content-Type: application/zip" + crlf + crlf
        let tail = crlf + "--\(boundary)--" + crlf
        return (Data(head.utf8), Data(tail.utf8), "multipart/form-data; boundary=\(boundary)")
    }
}

/// Body of `POST /sites/{site_id}/captures` (services/api CaptureCreate, docs/CONTRACTS.md "Capture upload"):
/// `{"device", "role", "marker", "n_chunks", "chunking": "independent", "started_at", "duration_s",
///   "privacy", "capture_files"}`. capture.json itself travels as the first entry of part 0.
public enum RegisterCaptureRequest {
    public static func body(manifest: CaptureManifest, nParts: Int) throws -> [String: Any] {
        let m = (try JSONSerialization.jsonObject(with: JSONEncoder().encode(manifest)) as? [String: Any]) ?? [:]
        var body: [String: Any] = ["role": manifest.role, "n_chunks": nParts, "chunking": "independent"]
        for (src, dst) in [("device", "device"), ("marker", "marker"), ("started_at", "started_at"),
                           ("duration_s", "duration_s"), ("privacy", "privacy"), ("files", "capture_files")] {
            if let v = m[src] { body[dst] = v }
        }
        return body
    }

    /// Body of `POST /captures/{capture_id}/upload-urls`: fresh URLs for the given parts of the same capture.
    public static func refreshBody(parts: [Int]) -> [String: Any] { ["parts": parts] }
}

/// Response of `POST /sites/{id}/captures` and `POST /captures/{id}/upload-urls` (services/api):
/// `{"capture_id": "...", "upload": {"chunks": [{"part": 0, "url": "...", "method": "PUT",
///   "headers": {...}, "key": "...", "expires_in_s": 3600}], "files": [...]}, "complete_url": "..."}`.
/// For older servers the parser also reads `parts` / `upload_urls` lists (at the root or under "upload").
public struct RegisterCaptureResponse: Equatable, Sendable {
    public var captureId: String?
    public var parts: [PresignedPart]
    public var completeURL: URL?
    public var deviceToken: String?

    public enum ParseError: Error, Equatable { case notJSON, noParts }

    public static func parse(_ data: Data, base: URL? = nil) throws -> RegisterCaptureResponse {
        guard let root = (try? JSONSerialization.jsonObject(with: data)) as? [String: Any] else { throw ParseError.notJSON }
        let nested = root["upload"] as? [String: Any] ?? [:]
        func value(_ k: String) -> Any? { root[k] ?? nested[k] }
        func url(_ s: String) -> URL? { URL(string: s, relativeTo: base)?.absoluteURL }

        let cid = (value("capture_id") as? String) ?? (value("id") as? String)
        var parts: [PresignedPart] = []
        let list = nested["chunks"] ?? value("parts") ?? value("upload_urls") ?? value("urls") ?? value("part_urls")
        if let strings = list as? [String] {
            for (i, s) in strings.enumerated() { if let u = url(s) { parts.append(PresignedPart(index: i, url: u)) } }
        } else if let dicts = list as? [[String: Any]] {
            for (i, d) in dicts.enumerated() {
                guard let s = (d["url"] as? String) ?? (d["upload_url"] as? String), let u = url(s) else { continue }
                let idx = (d["index"] as? Int) ?? (d["part"] as? Int) ?? (d["n"] as? Int) ?? i
                let method = ((d["method"] as? String) ?? "PUT").uppercased()
                let headers = (d["headers"] as? [String: String]) ?? [:]
                let fields = (d["fields"] as? [String: String]) ?? [:]
                let fileField = (d["file_field"] as? String) ?? "file"
                parts.append(PresignedPart(index: idx, url: u, method: method, headers: headers, fields: fields,
                                           fileField: fileField))
            }
        }
        guard !parts.isEmpty else { throw ParseError.noParts }
        parts.sort { $0.index < $1.index }
        let complete = (value("complete_url") as? String).flatMap(url)
        return RegisterCaptureResponse(captureId: cid, parts: parts, completeURL: complete,
                                       deviceToken: root["device_token"] as? String)
    }
}
