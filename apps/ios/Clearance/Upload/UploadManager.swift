import ClearanceKit
import CryptoKit
import Foundation
import UIKit

/// Resumable capture upload (PLAN 7.1 / Section 11):
/// 1. `POST /sites/{site}/captures` with the device, marker and part count (RegisterCaptureRequest);
///    the server answers with a capture_id and `upload.chunks`, one presigned URL per ZIP part.
/// 2. Each part is zipped on demand, hashed (SHA-256) and `PUT` to its URL; receipts are saved to
///    upload_state.json after every part, so a restart only sends what is missing. Expired URLs are
///    refreshed with `POST /captures/{id}/upload-urls`.
/// 3. `POST /captures/{id}/complete` (receipts in the body for the log); that starts ingest (J1).
/// Registration uses the site join token from the QR code; the response's per-capture `device_token`
/// (saved in upload_state.json) authorizes refreshes and complete later, after the join token expired.
/// Upload URLs are single-use per part: a 409 means the part already landed (checked against the server).
@MainActor
final class UploadManager: ObservableObject {
    struct Progress: Equatable {
        var fraction: Double
        var status: String
        var running: Bool
        var error: String?
        var completed: Bool
    }

    @Published private(set) var progress: [String: Progress] = [:]
    private var tasks: [String: Task<Void, Never>] = [:]

    var activeCount: Int { progress.values.filter(\.running).count }

    func start(dir: URL, client: APIClient) {
        let key = dir.lastPathComponent
        guard tasks[key] == nil else { return }
        let partMB = max(4, UserDefaults.standard.integer(forKey: SettingsKey.partSizeMB))
        progress[key] = Progress(fraction: progress[key]?.fraction ?? 0, status: "Preparing", running: true, error: nil, completed: false)
        let bgTask = UIApplication.shared.beginBackgroundTask(withName: "clearance-upload-\(key)")
        let manager = self
        let uploader = CaptureUploader(dir: dir, client: client, partBytes: Int64(partMB) << 20) { state, status in
            Task { @MainActor in
                manager.progress[key] = Progress(fraction: state.fraction, status: status, running: !state.completed,
                                                 error: nil, completed: state.completed)
            }
        }
        tasks[key] = Task {
            do {
                try await uploader.run()
                manager.finish(key, error: nil)
            } catch is CancellationError {
                manager.finish(key, error: "Paused")
            } catch {
                manager.finish(key, error: error.localizedDescription)
            }
            if bgTask != .invalid { UIApplication.shared.endBackgroundTask(bgTask) }
        }
    }

    func cancel(_ key: String) {
        tasks[key]?.cancel()
    }

    private func finish(_ key: String, error: String?) {
        tasks[key] = nil
        var p = progress[key] ?? Progress(fraction: 0, status: "", running: false, error: nil, completed: false)
        p.running = false
        p.error = error
        if error == nil { p.completed = true; p.fraction = 1; p.status = "Uploaded" } else { p.status = error ?? "" }
        progress[key] = p
    }
}

/// The upload protocol itself (no UI); runs off the main actor, one run at a time per capture.
final class CaptureUploader {
    let dir: URL
    let client: APIClient
    let partBytes: Int64
    let report: @Sendable (UploadState, String) -> Void
    private var completeURL: URL?

    init(dir: URL, client: APIClient, partBytes: Int64, report: @escaping @Sendable (UploadState, String) -> Void) {
        self.dir = dir
        self.client = client
        self.partBytes = partBytes
        self.report = report
    }

    enum UploadFailure: LocalizedError {
        case notFinalized
        case urlCountMismatch(Int, Int)
        case partCheck(String)
        var errorDescription: String? {
            switch self {
            case .notFinalized: return "This capture has no capture.json yet."
            case .urlCountMismatch(let got, let want): return "Server returned \(got) upload URLs for \(want) parts."
            case .partCheck(let s): return "Part check failed: \(s)"
            }
        }
    }

    func run() async throws {
        guard var manifest = CaptureStore.readManifest(dir) else { throw UploadFailure.notFinalized }
        var state: UploadState
        if let s = CaptureStore.readUploadState(dir), !s.parts.isEmpty {
            state = s
        } else {
            let parts = UploadPlanner.plan(files: CaptureStore.uploadFiles(dir), targetBytes: partBytes)
            state = UploadState(captureId: manifest.captureId, siteId: manifest.siteId, parts: parts)
            try CaptureStore.writeUploadState(state, to: dir)
        }
        if state.completed { report(state, "Uploaded"); return }

        report(state, "Registering")
        var targets = try await register(&state, &manifest)

        // Re-evaluated every pass: a re-registration may reset receipts.
        while let part = state.pendingParts.first {
            try Task.checkCancellation()
            report(state, "Uploading part \(part.index + 1) of \(state.parts.count)")
            let zip = try buildPart(part)
            defer { try? FileManager.default.removeItem(at: zip) }
            let data = try Data(contentsOf: zip, options: .mappedIfSafe)
            let sha = SHA256.hash(data: data).map { String(format: "%02x", $0) }.joined()

            var attempt = 0
            while true {
                try Task.checkCancellation()
                guard let target = targets.first(where: { $0.index == part.index }) else {
                    throw UploadFailure.urlCountMismatch(targets.count, state.parts.count)
                }
                do {
                    let etag = try await put(zip, to: target)
                    state.record(PartReceipt(index: part.index, name: part.name, size: Int64(data.count), sha256: sha, etag: etag))
                    state.lastError = nil
                    try CaptureStore.writeUploadState(state, to: dir)
                    report(state, "Uploaded part \(part.index + 1) of \(state.parts.count)")
                    break
                } catch let e as APIError where e.status == 409 {
                    // single-use upload URL: the part may have landed before a crash lost its receipt
                    if try await serverHasPart(state.captureId, index: part.index) {
                        state.record(PartReceipt(index: part.index, name: part.name, size: Int64(data.count), sha256: sha, etag: nil))
                        try CaptureStore.writeUploadState(state, to: dir)
                        break
                    }
                    throw e
                } catch let e as APIError where e.status == 403 || e.status == 400 {
                    // Presigned URL expired or was rejected: ask for fresh URLs once per attempt.
                    attempt += 1
                    if attempt > 3 { throw e }
                    targets = try await register(&state, &manifest)
                } catch {
                    attempt += 1
                    state.lastError = error.localizedDescription
                    try? CaptureStore.writeUploadState(state, to: dir)
                    if attempt > 5 { throw error }
                    let delay = UInt64(min(30, 1 << attempt)) * 1_000_000_000
                    report(state, "Retrying part \(part.index + 1) in \(min(30, 1 << attempt)) s")
                    try await Task.sleep(nanoseconds: delay)
                }
            }
        }

        report(state, "Finishing")
        let completeURL = self.completeURL ?? client.url("captures/\(state.captureId)/complete")
        let body: [String: Any] = [
            "capture_id": state.captureId,
            "site_id": state.siteId,
            "n_parts": state.parts.count,
            "content_type": "application/zip",
            "parts": state.receipts.map { r -> [String: Any] in
                var d: [String: Any] = ["index": r.index, "name": r.name, "size": r.size, "sha256": r.sha256]
                if let e = r.etag { d["etag"] = e }
                return d
            },
        ]
        _ = try await client.postJSON(completeURL, body, bearer: state.deviceToken)
        state.completed = true
        try CaptureStore.writeUploadState(state, to: dir)
        report(state, "Uploaded")
    }

    /// First run: register the capture. Later runs (resume, expired URLs): ask for fresh URLs for the
    /// pending parts of the same capture; only if the server no longer knows it, register again.
    private func register(_ state: inout UploadState, _ manifest: inout CaptureManifest) async throws -> [PresignedPart] {
        if state.registered {
            let pending = state.pendingParts.map(\.index)
            if pending.isEmpty { return [] }
            do {
                let data = try await client.postJSON(client.url("captures/\(state.captureId)/upload-urls"),
                                                     RegisterCaptureRequest.refreshBody(parts: pending),
                                                     bearer: state.deviceToken)
                let resp = try RegisterCaptureResponse.parse(data, base: client.join.apiBase)
                if let c = resp.completeURL { completeURL = c }
                return resp.parts
            } catch let e as APIError where e.status == 404 {
                state.registered = false
                state.receipts.removeAll()
            }
        }
        let body = try RegisterCaptureRequest.body(manifest: manifest, nParts: state.parts.count)
        let data = try await client.postJSON(client.url("sites/\(state.siteId)/captures"), body)
        let resp = try RegisterCaptureResponse.parse(data, base: client.join.apiBase)
        if let newId = resp.captureId, newId != state.captureId {
            // The server assigned the id: record it in capture.json before any part is built.
            if !state.receipts.isEmpty { state.receipts.removeAll() }
            state.captureId = newId
            manifest.captureId = newId
            try CaptureStore.writeManifest(manifest, to: dir)
        }
        guard resp.parts.count >= state.parts.count else {
            throw UploadFailure.urlCountMismatch(resp.parts.count, state.parts.count)
        }
        state.deviceToken = resp.deviceToken
        state.registered = true
        try CaptureStore.writeUploadState(state, to: dir)
        completeURL = resp.completeURL
        return resp.parts
    }

    private func buildPart(_ part: UploadPart) throws -> URL {
        let tmpDir = FileManager.default.temporaryDirectory.appendingPathComponent("clearance-upload", isDirectory: true)
        try FileManager.default.createDirectory(at: tmpDir, withIntermediateDirectories: true)
        let url = tmpDir.appendingPathComponent("\(dir.lastPathComponent)-\(part.name)")
        let zip = try ZipWriter(url: url)
        for rel in part.files {
            try zip.addFile(at: dir.appendingPathComponent(rel), name: rel)
        }
        try zip.finish()
        let reader = try ZipArchiveReader(data: try Data(contentsOf: url, options: .mappedIfSafe))
        guard reader.entries.map(\.name) == part.files else { throw UploadFailure.partCheck(part.name) }
        return url
    }

    /// True when GET /captures/{id} no longer lists the part (server key upload/part_NNNN.zip) as missing.
    private func serverHasPart(_ captureId: String, index: Int) async throws -> Bool {
        guard let st = try await client.getJSON("captures/\(captureId)") as? [String: Any],
              let missing = st["missing"] as? [String] else { return false }
        let key = String(format: "/upload/part_%04d.zip", index)
        return !missing.contains { $0.hasSuffix(key) }
    }

    private func put(_ file: URL, to target: PresignedPart) async throws -> String? {
        var req = URLRequest(url: target.url, timeoutInterval: 300)
        req.httpMethod = target.method
        for (k, v) in target.headers { req.setValue(v, forHTTPHeaderField: k) }
        // Signed upload URLs authorize themselves: no bearer token is sent with the file.
        var bodyFile = file
        var cleanup: URL?
        defer { if let cleanup { try? FileManager.default.removeItem(at: cleanup) } }
        if target.method == "POST" && !target.fields.isEmpty {
            // S3 presigned POST (its policy caps the size): multipart body, policy fields first, file last
            let boundary = "clearance-\(UUID().uuidString)"
            let framing = target.multipart(boundary: boundary, filename: file.lastPathComponent)
            let tmp = FileManager.default.temporaryDirectory.appendingPathComponent("\(UUID().uuidString).multipart")
            FileManager.default.createFile(atPath: tmp.path, contents: nil)
            let out = try FileHandle(forWritingTo: tmp)
            defer { try? out.close() }
            try out.write(contentsOf: framing.prefix)
            let input = try FileHandle(forReadingFrom: file)
            defer { try? input.close() }
            while let chunk = try input.read(upToCount: 4 << 20), !chunk.isEmpty {
                try out.write(contentsOf: chunk)
            }
            try out.write(contentsOf: framing.suffix)
            try out.close()
            req.setValue(framing.contentType, forHTTPHeaderField: "Content-Type")
            bodyFile = tmp
            cleanup = tmp
        } else if target.headers.keys.first(where: { $0.lowercased() == "content-type" }) == nil {
            req.setValue("application/zip", forHTTPHeaderField: "Content-Type")
        }
        let (body, resp) = try await client.session.upload(for: req, fromFile: bodyFile)
        guard let http = resp as? HTTPURLResponse else { throw URLError(.badServerResponse) }
        guard (200..<300).contains(http.statusCode) else {
            throw APIError(status: http.statusCode, url: target.url, body: String(data: body, encoding: .utf8) ?? "")
        }
        return http.value(forHTTPHeaderField: "ETag")
    }
}
