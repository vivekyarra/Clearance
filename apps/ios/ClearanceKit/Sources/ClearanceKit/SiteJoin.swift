import Foundation

/// What the console's site QR code tells the phone. The QR itself encodes (docs/CONTRACTS.md "Site QR code")
/// `<web base>/join/<site_id>?api=<url-encoded API base>&name=<url-encoded site name>&token=<join token>`; the
/// short-lived join token authorizes capture registration and the coach session for that site only.
/// API bases must be https unless they are on the local network (APIHostPolicy). Accepted payloads:
/// * JSON: `{"api": "https://host/api", "site_id": "site_x", "token": "...", "name": "Cafe"}`
///   (also `api_base`, `site`, `site_name`; the API's `qr` site object parses this way).
/// * App URL: `clearance://join?api=https%3A%2F%2Fhost%2Fapi&site=site_x&token=...`
/// * Console URL: `https://host/join/site_x` or `https://host/sites/site_x?token=...`; the API
///   base is then `https://host/api` (the console proxies /api/* to the API service) unless an
///   `api` query item is present.
public struct SiteJoin: Codable, Equatable, Sendable {
    public var apiBase: URL
    public var siteId: String
    public var token: String?
    public var siteName: String?

    enum CodingKeys: String, CodingKey {
        case token
        case apiBase = "api_base"
        case siteId = "site_id"
        case siteName = "site_name"
    }

    public init(apiBase: URL, siteId: String, token: String? = nil, siteName: String? = nil) {
        self.apiBase = apiBase; self.siteId = siteId; self.token = token; self.siteName = siteName
    }

    public var displayName: String { siteName.flatMap { $0.isEmpty ? nil : $0 } ?? siteId }

    /// `host[:port]` of the API this join sends uploads to; shown to the person before the first upload.
    public var apiHostDisplay: String { APIHostPolicy.displayHost(apiBase) }
}

/// Which API base URLs a join link may name. App Transport Security keeps only NSAllowsLocalNetworking, so
/// plain http is accepted only for the local network (loopback, private and link-local IP literals,
/// `.local` names and unqualified host names); everything else must be https.
public enum APIHostPolicy {
    public static func isLocalNetworkHost(_ rawHost: String) -> Bool {
        var host = rawHost.lowercased()
        if host.hasPrefix("[") && host.hasSuffix("]") { host = String(host.dropFirst().dropLast()) }
        if host.isEmpty { return false }
        if host == "localhost" || host.hasSuffix(".local") || host.hasSuffix(".localhost") { return true }
        if let v4 = ipv4(host) {
            let (a, b) = (v4[0], v4[1])
            return a == 10 || a == 127 || (a == 172 && (16...31).contains(b)) || (a == 192 && b == 168)
                || (a == 169 && b == 254)
        }
        if host.contains(":") {  // IPv6 literal: loopback, link-local fe80::/10, unique local fc00::/7
            if host == "::1" { return true }
            let first = host.split(separator: ":", omittingEmptySubsequences: false).first.map(String.init) ?? ""
            guard let w = UInt16(first, radix: 16), !first.isEmpty else { return false }
            return (w & 0xffc0) == 0xfe80 || (w & 0xfe00) == 0xfc00
        }
        // unqualified names (no dot) resolve on the local network only
        return !host.contains(".") && host.unicodeScalars.allSatisfy { CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "-")).contains($0) }
    }

    static func ipv4(_ host: String) -> [Int]? {
        let parts = host.split(separator: ".", omittingEmptySubsequences: false)
        guard parts.count == 4 else { return nil }
        var out: [Int] = []
        for p in parts {
            guard !p.isEmpty, p.count <= 3, p.allSatisfy(\.isNumber), let v = Int(p), v <= 255 else { return nil }
            out.append(v)
        }
        return out
    }

    /// https anywhere; http only on the local network.
    public static func allowed(scheme: String, host: String) -> Bool {
        switch scheme.lowercased() {
        case "https": return !host.isEmpty
        case "http": return isLocalNetworkHost(host)
        default: return false
        }
    }

    public static func displayHost(_ url: URL) -> String {
        guard let c = URLComponents(url: url, resolvingAgainstBaseURL: false), let host = c.host else {
            return url.absoluteString
        }
        let h = host.contains(":") ? "[\(host)]" : host
        return c.port.map { "\(h):\($0)" } ?? h
    }
}

public enum SiteJoinParser {
    static let siteIdChars = CharacterSet.alphanumerics.union(CharacterSet(charactersIn: "_-."))

    public static func parse(_ payload: String) -> SiteJoin? {
        let s = payload.trimmingCharacters(in: .whitespacesAndNewlines)
        if s.hasPrefix("{") { return parseJSON(s) }
        return parseURL(s)
    }

    static func validSite(_ id: String?) -> String? {
        guard let id, !id.isEmpty, id.count <= 128,
              id.unicodeScalars.allSatisfy({ siteIdChars.contains($0) }) else { return nil }
        return id
    }

    static func validAPI(_ s: String?) -> URL? {
        guard let s, var c = URLComponents(string: s),
              let scheme = c.scheme?.lowercased(), scheme == "http" || scheme == "https",
              let host = c.host, !host.isEmpty, c.user == nil, c.password == nil,
              APIHostPolicy.allowed(scheme: scheme, host: host) else { return nil }
        c.query = nil
        c.fragment = nil
        while c.path.hasSuffix("/") { c.path.removeLast() }
        return c.url
    }

    static func parseJSON(_ s: String) -> SiteJoin? {
        guard let obj = (try? JSONSerialization.jsonObject(with: Data(s.utf8))) as? [String: Any] else { return nil }
        func str(_ keys: [String]) -> String? {
            for k in keys { if let v = obj[k] as? String, !v.isEmpty { return v } }
            return nil
        }
        guard let api = validAPI(str(["api", "api_base", "apiBase", "base_url"])),
              let site = validSite(str(["site_id", "site", "siteId"])) else { return nil }
        return SiteJoin(apiBase: api, siteId: site, token: str(["token"]), siteName: str(["name", "site_name"]))
    }

    static func parseURL(_ s: String) -> SiteJoin? {
        guard let c = URLComponents(string: s), let scheme = c.scheme?.lowercased() else { return nil }
        var q: [String: String] = [:]
        for item in c.queryItems ?? [] { if let v = item.value, !v.isEmpty { q[item.name] = v } }
        let token = q["token"]
        let name = q["name"] ?? q["site_name"]
        if scheme == "clearance" {
            guard let api = validAPI(q["api"] ?? q["api_base"]),
                  let site = validSite(q["site"] ?? q["site_id"]) else { return nil }
            return SiteJoin(apiBase: api, siteId: site, token: token, siteName: name)
        }
        guard scheme == "http" || scheme == "https", let host = c.host, !host.isEmpty else { return nil }
        var site = validSite(q["site"] ?? q["site_id"])
        if site == nil {
            let parts = c.path.split(separator: "/").map(String.init)
            for (i, p) in parts.enumerated() where ["join", "sites", "site", "scan", "observe"].contains(p) && i + 1 < parts.count {
                site = validSite(parts[i + 1])
                if site != nil { break }
            }
        }
        guard let siteId = site else { return nil }
        if q["api"] != nil || q["api_base"] != nil {
            // an explicit API base that fails the policy is refused, never replaced by the page host
            guard let api = validAPI(q["api"] ?? q["api_base"]) else { return nil }
            return SiteJoin(apiBase: api, siteId: siteId, token: token, siteName: name)
        }
        guard APIHostPolicy.allowed(scheme: scheme, host: host) else { return nil }
        var base = URLComponents()
        base.scheme = scheme
        base.host = host
        base.port = c.port
        base.path = "/api"
        guard let api = base.url else { return nil }
        return SiteJoin(apiBase: api, siteId: siteId, token: token, siteName: name)
    }
}
