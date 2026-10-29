import ClearanceKit
import Foundation

struct APIError: LocalizedError {
    let status: Int
    let url: URL?
    let body: String

    var errorDescription: String? {
        let snippet = body.count > 200 ? String(body.prefix(200)) + "…" : body
        return "HTTP \(status) from \(url?.path ?? "server")\(snippet.isEmpty ? "" : ": \(snippet)")"
    }
}

/// Thin JSON client for the Clearance API (services/api, docs/CONTRACTS.md).
struct APIClient: Sendable {
    let join: SiteJoin
    var session: URLSession = .shared

    func url(_ path: String) -> URL {
        var base = join.apiBase.absoluteString
        while base.hasSuffix("/") { base.removeLast() }
        let p = path.hasPrefix("/") ? path : "/" + path
        return URL(string: base + p) ?? join.apiBase.appendingPathComponent(path)
    }

    /// `bearer` overrides the join token (a capture's device token for its own upload calls).
    func request(_ method: String, _ url: URL, json: Any? = nil, timeout: TimeInterval = 60,
                 bearer: String? = nil) throws -> URLRequest {
        var r = URLRequest(url: url, timeoutInterval: timeout)
        r.httpMethod = method
        r.setValue("application/json", forHTTPHeaderField: "Accept")
        // tokens only ever go to the joined API host
        if url.host == join.apiBase.host, let token = bearer ?? join.token, !token.isEmpty {
            r.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        if let json {
            r.httpBody = try JSONSerialization.data(withJSONObject: json, options: [.sortedKeys])
            r.setValue("application/json", forHTTPHeaderField: "Content-Type")
        }
        return r
    }

    @discardableResult
    func send(_ req: URLRequest) async throws -> (Data, HTTPURLResponse) {
        let (data, resp) = try await session.data(for: req)
        guard let http = resp as? HTTPURLResponse else { throw URLError(.badServerResponse) }
        guard (200..<300).contains(http.statusCode) else {
            throw APIError(status: http.statusCode, url: req.url, body: String(data: data, encoding: .utf8) ?? "")
        }
        return (data, http)
    }

    func getData(_ path: String) async throws -> Data {
        try await send(request("GET", url(path))).0
    }

    func getJSON(_ path: String) async throws -> Any {
        try JSONSerialization.jsonObject(with: try await getData(path))
    }

    func postJSON(_ url: URL, _ body: Any, bearer: String? = nil) async throws -> Data {
        try await send(request("POST", url, json: body, bearer: bearer)).0
    }

    /// Downloads to a temporary file that keeps `ext` (RealityKit picks the loader by extension).
    func download(_ url: URL, ext: String) async throws -> URL {
        var req = URLRequest(url: url, timeoutInterval: 120)
        if url.host == join.apiBase.host, let token = join.token, !token.isEmpty {
            req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
        }
        let (tmp, resp) = try await session.download(for: req)
        guard let http = resp as? HTTPURLResponse, (200..<300).contains(http.statusCode) else {
            throw APIError(status: (resp as? HTTPURLResponse)?.statusCode ?? -1, url: url, body: "")
        }
        let dst = FileManager.default.temporaryDirectory.appendingPathComponent(UUID().uuidString + "." + ext)
        try FileManager.default.moveItem(at: tmp, to: dst)
        return dst
    }
}
