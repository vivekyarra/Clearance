import ClearanceKit
import SwiftUI

@main
struct ClearanceApp: App {
    @StateObject private var model = AppModel()

    var body: some Scene {
        WindowGroup {
            HomeView()
                .environmentObject(model)
                .environmentObject(model.uploads)
                .tint(Theme.accent)
                .onOpenURL { url in
                    if let join = SiteJoinParser.parse(url.absoluteString) { model.site = join }
                }
                // A join link (QR code or clearance:// URL) names the server that receives the scans:
                // show that host and ask before the first upload to it.
                .alert("Upload scans to this server?",
                       isPresented: Binding(get: { model.hostPrompt != nil },
                                            set: { if !$0 { model.hostPrompt = nil } }),
                       presenting: model.hostPrompt) { prompt in
                    Button("Upload to \(prompt.host)") { model.confirm(prompt) }
                    Button("Cancel", role: .cancel) { model.hostPrompt = nil }
                } message: { prompt in
                    Text("The site link you joined sends scans of this space to \(prompt.host) (\(prompt.scheme)). " +
                         "Only continue if this is the Clearance server you expect.")
                }
        }
    }
}

/// Settings keys shared by @AppStorage readers.
enum SettingsKey {
    static let markerSizeM = "markerSizeM"
    static let maxLongSide = "maxLongSide"
    static let blurThreshold = "blurThreshold"
    static let leveledAnchoring = "leveledAnchoring"
    static let autoUpload = "autoUpload"
    static let occluderZUp = "occluderZUp"
    static let partSizeMB = "partSizeMB"
}

enum SettingsDefaults {
    static let markerSizeM = 0.160
    static let maxLongSide = 1440
    static let blurThreshold = 35.0
    static let leveledAnchoring = true
    static let autoUpload = true
    static let occluderZUp = true
    static let partSizeMB = 16

    static func register() {
        UserDefaults.standard.register(defaults: [
            SettingsKey.markerSizeM: markerSizeM,
            SettingsKey.maxLongSide: maxLongSide,
            SettingsKey.blurThreshold: blurThreshold,
            SettingsKey.leveledAnchoring: leveledAnchoring,
            SettingsKey.autoUpload: autoUpload,
            SettingsKey.occluderZUp: occluderZUp,
            SettingsKey.partSizeMB: partSizeMB,
        ])
    }
}

/// A pending "upload to this host?" question; `action` runs once the person confirms.
struct HostPrompt: Identifiable {
    let id = UUID()
    let host: String
    let scheme: String
    let action: () -> Void
}

@MainActor
final class AppModel: ObservableObject {
    @Published var site: SiteJoin? {
        didSet { persistSite() }
    }
    @Published var hostPrompt: HostPrompt?
    private static let confirmedHostsKey = "confirmedUploadHosts"

    let store: CaptureStore
    let uploads: UploadManager

    private static let siteKey = "joinedSite"

    init() {
        store = CaptureStore()
        uploads = UploadManager()
        SettingsDefaults.register()
        if let data = UserDefaults.standard.data(forKey: Self.siteKey),
           let s = try? JSONDecoder().decode(SiteJoin.self, from: data) {
            site = s
        }
    }

    private func persistSite() {
        if let site, let data = try? JSONEncoder().encode(site) {
            UserDefaults.standard.set(data, forKey: Self.siteKey)
        } else {
            UserDefaults.standard.removeObject(forKey: Self.siteKey)
        }
    }

    var apiClient: APIClient? { site.map { APIClient(join: $0) } }

    private static func hostKey(_ url: URL) -> String {
        "\(url.scheme?.lowercased() ?? "")://\(APIHostPolicy.displayHost(url).lowercased())"
    }

    func isHostConfirmed(_ url: URL) -> Bool {
        (UserDefaults.standard.stringArray(forKey: Self.confirmedHostsKey) ?? []).contains(Self.hostKey(url))
    }

    func confirm(_ prompt: HostPrompt) {
        if let base = site?.apiBase {
            var hosts = UserDefaults.standard.stringArray(forKey: Self.confirmedHostsKey) ?? []
            let key = Self.hostKey(base)
            if !hosts.contains(key) { hosts.append(key) }
            UserDefaults.standard.set(hosts, forKey: Self.confirmedHostsKey)
        }
        hostPrompt = nil
        prompt.action()
    }

    /// Starts an upload of `dir`, asking first when this API host has not been confirmed on this phone.
    func startUpload(dir: URL) {
        guard let client = apiClient else { return }
        let run = { [uploads] in uploads.start(dir: dir, client: client) }
        if isHostConfirmed(client.join.apiBase) {
            run()
        } else {
            hostPrompt = HostPrompt(host: client.join.apiHostDisplay, scheme: client.join.apiBase.scheme ?? "", action: run)
        }
    }
}
