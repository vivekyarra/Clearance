import ClearanceKit
import Foundation

/// Capture folders live in Documents/captures/<capture_id>/ (visible in the Files app and
/// Finder file sharing), laid out per the Capture schema:
///   capture.json, poses.jsonl, feature_points.jsonl, marker_sightings.jsonl, arkit_planes.json,
///   frames/*.jpg, depth/*.png (T1), confidence/*.png (T1), upload_state.json (local only).
struct CaptureSummary: Identifiable, Equatable {
    let id: String
    let url: URL
    let manifest: CaptureManifest?
    let bytes: Int64
    let upload: UploadState?
    let modified: Date

    var isFinalized: Bool { manifest != nil }
}

final class CaptureStore {
    static let uploadStateFile = "upload_state.json"
    static let manifestFile = "capture.json"

    let root: URL

    init() {
        let docs = FileManager.default.urls(for: .documentDirectory, in: .userDomainMask)[0]
        root = docs.appendingPathComponent("captures", isDirectory: true)
        try? FileManager.default.createDirectory(at: root, withIntermediateDirectories: true)
    }

    func newCaptureDirectory(captureId: String) throws -> URL {
        let dir = root.appendingPathComponent(captureId, isDirectory: true)
        for sub in ["frames", "depth", "confidence"] {
            try FileManager.default.createDirectory(at: dir.appendingPathComponent(sub, isDirectory: true),
                                                    withIntermediateDirectories: true)
        }
        return dir
    }

    func list() -> [CaptureSummary] {
        let fm = FileManager.default
        guard let dirs = try? fm.contentsOfDirectory(at: root, includingPropertiesForKeys: [.contentModificationDateKey],
                                                      options: [.skipsHiddenFiles]) else { return [] }
        return dirs.compactMap { dir -> CaptureSummary? in
            guard (try? dir.resourceValues(forKeys: [.isDirectoryKey]))?.isDirectory == true else { return nil }
            let manifest = CaptureStore.readManifest(dir)
            let state = CaptureStore.readUploadState(dir)
            let modified = (try? dir.resourceValues(forKeys: [.contentModificationDateKey]))?.contentModificationDate ?? .distantPast
            return CaptureSummary(id: dir.lastPathComponent, url: dir, manifest: manifest,
                                  bytes: CaptureStore.folderSize(dir), upload: state, modified: modified)
        }
        .sorted { $0.modified > $1.modified }
    }

    func delete(_ s: CaptureSummary) throws {
        try FileManager.default.removeItem(at: s.url)
    }

    static func readManifest(_ dir: URL) -> CaptureManifest? {
        guard let d = try? Data(contentsOf: dir.appendingPathComponent(manifestFile)) else { return nil }
        return try? JSONDecoder().decode(CaptureManifest.self, from: d)
    }

    static func writeManifest(_ m: CaptureManifest, to dir: URL) throws {
        let enc = JSONEncoder()
        enc.outputFormatting = [.prettyPrinted, .sortedKeys, .withoutEscapingSlashes]
        try enc.encode(m).write(to: dir.appendingPathComponent(manifestFile), options: .atomic)
    }

    static func readUploadState(_ dir: URL) -> UploadState? {
        guard let d = try? Data(contentsOf: dir.appendingPathComponent(uploadStateFile)) else { return nil }
        let dec = JSONDecoder()
        dec.dateDecodingStrategy = .iso8601
        return try? dec.decode(UploadState.self, from: d)
    }

    static func writeUploadState(_ s: UploadState, to dir: URL) throws {
        let enc = JSONEncoder()
        enc.dateEncodingStrategy = .iso8601
        enc.outputFormatting = [.prettyPrinted, .sortedKeys]
        try enc.encode(s).write(to: dir.appendingPathComponent(uploadStateFile), options: .atomic)
    }

    /// Files to upload, relative to the capture root (local bookkeeping files excluded).
    static func uploadFiles(_ dir: URL) -> [UploadFile] {
        let fm = FileManager.default
        guard let e = fm.enumerator(at: dir, includingPropertiesForKeys: [.fileSizeKey, .isRegularFileKey],
                                    options: [.skipsHiddenFiles]) else { return [] }
        let rootPath = dir.resolvingSymlinksInPath().path
        var out: [UploadFile] = []
        for case let url as URL in e {
            guard let v = try? url.resourceValues(forKeys: [.isRegularFileKey, .fileSizeKey]), v.isRegularFile == true else { continue }
            var rel = url.resolvingSymlinksInPath().path
            guard rel.hasPrefix(rootPath) else { continue }
            rel = String(rel.dropFirst(rootPath.count))
            while rel.hasPrefix("/") { rel.removeFirst() }
            if rel == uploadStateFile || rel.hasSuffix(".tmp") { continue }
            out.append(UploadFile(path: rel, size: Int64(v.fileSize ?? 0)))
        }
        return out
    }

    static func folderSize(_ dir: URL) -> Int64 {
        uploadFiles(dir).reduce(0) { $0 + $1.size }
    }
}
