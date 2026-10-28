import ClearanceKit
import Foundation

/// Streams coverage and prompts to the console's coach screen (`/coach/{site_id}`) during a scan.
/// Both ends meet in the site's shared coach session: `POST /live {"site_id", "mode": "coach"}` returns
/// the open session (or opens one), then `WS /live/{session}?role=scanner` carries `CoachMessage`s.
/// The link is best effort: a scan never waits for it, and it reconnects on the next update.
@MainActor
final class CoachLink {
    private let client: APIClient
    private var socket: URLSessionWebSocketTask?
    private var connecting = false
    private var lastSent: TimeInterval = 0
    private var lastPrompt: String?
    private var closed = false
    /// Minimum seconds between coverage updates (prompt changes are sent at once).
    var interval: TimeInterval = 1.0

    init(client: APIClient) { self.client = client }

    func connect() {
        guard socket == nil, !connecting, !closed else { return }
        connecting = true
        Task { [weak self] in
            guard let self else { return }
            defer { self.connecting = false }
            do {
                let data = try await client.postJSON(client.url("live"), ["site_id": client.join.siteId, "mode": "coach"])
                guard let obj = try JSONSerialization.jsonObject(with: data) as? [String: Any],
                      let sid = obj["session_id"] as? String,
                      let url = Self.socketURL(apiBase: client.join.apiBase, session: sid) else { return }
                var req = URLRequest(url: url)
                if let token = client.join.token, !token.isEmpty {
                    req.setValue("Bearer \(token)", forHTTPHeaderField: "Authorization")
                }
                let ws = client.session.webSocketTask(with: req)
                ws.resume()
                socket = ws
                receive(ws)
            } catch {
                socket = nil
            }
        }
    }

    /// `http(s)://host/api` + session -> `ws(s)://host/api/live/{session}?role=scanner`.
    nonisolated static func socketURL(apiBase: URL, session: String) -> URL? {
        guard var c = URLComponents(url: apiBase, resolvingAgainstBaseURL: false) else { return nil }
        c.scheme = c.scheme?.lowercased() == "https" ? "wss" : "ws"
        var path = c.path
        while path.hasSuffix("/") { path.removeLast() }
        c.path = path + "/live/" + session
        c.queryItems = [URLQueryItem(name: "role", value: "scanner")]
        return c.url
    }

    /// Drains server frames (hello, errors, messages from coach screens) and notices disconnects.
    private func receive(_ ws: URLSessionWebSocketTask) {
        ws.receive { [weak self] result in
            Task { @MainActor in
                guard let self, self.socket === ws else { return }
                if case .failure = result {
                    self.socket = nil
                } else {
                    self.receive(ws)
                }
            }
        }
    }

    func update(grid: CoverageGrid, prompt: String?, frames: Int, tracking: String?) {
        let now = Date().timeIntervalSince1970
        guard prompt != lastPrompt || now - lastSent >= interval else { return }
        guard let ws = socket else { connect(); return }
        lastSent = now
        lastPrompt = prompt
        let msg = CoachMessage.make(siteId: client.join.siteId, grid: grid, prompt: prompt, t: now,
                                    device: DeviceInfo.modelIdentifier, frames: frames,
                                    tracking: tracking == nil ? "normal" : "limited")
        guard let text = try? CoachMessage.encode(msg) else { return }
        ws.send(.string(text)) { [weak self] error in
            if error != nil { Task { @MainActor in if self?.socket === ws { self?.socket = nil } } }
        }
    }

    func close() {
        closed = true
        socket?.cancel(with: .normalClosure, reason: nil)
        socket = nil
    }
}
