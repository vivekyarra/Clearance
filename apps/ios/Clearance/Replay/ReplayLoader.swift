import ClearanceKit
import Foundation

/// What the replay screen plays.
struct ReplayBundle {
    var asIs: Episode
    var withChanges: Episode?
    var robot: RobotShape
    var primitives: [PrimitiveShape]
    var change: LayoutChange?
    var verdict: VerdictBadge?
    var occluderFile: URL?
    var notes: [String]
}

struct ReplayRequest: Equatable {
    var analysisId: String = ""
    var robotId: String = ""
    var asIsBatchId: String = ""
    var withChangesBatchId: String = ""
    var episode: Int = 0
}

struct CatalogRobot: Identifiable, Hashable {
    let id: String
    let title: String
    let json: [String: Any]

    static func == (a: CatalogRobot, b: CatalogRobot) -> Bool { a.id == b.id }
    func hash(into h: inout Hasher) { h.combine(id) }
}

enum ReplayLoader {
    static func catalog(_ client: APIClient) async throws -> [CatalogRobot] {
        let obj = try await client.getJSON("catalog/robots")
        let list = (obj as? [[String: Any]]) ?? ((obj as? [String: Any])?["robots"] as? [[String: Any]]) ?? []
        return list.compactMap { d in
            guard let id = d["robot_id"] as? String else { return nil }
            let title = [d["vendor"] as? String, d["model"] as? String].compactMap { $0 }.joined(separator: " ")
            return CatalogRobot(id: id, title: title.isEmpty ? id : title, json: d)
        }
    }

    static func analysisRefs(_ client: APIClient, analysisId: String, robotId: String?) async throws -> AnalysisReplayRefs {
        let obj = try await client.getJSON("analyses/\(analysisId)")
        guard let d = obj as? [String: Any] else { return AnalysisReplayRefs() }
        return AnalysisReplayRefs.parse(d, robotId: robotId?.isEmpty == false ? robotId : nil)
    }

    static func episode(_ client: APIClient, batchId: String, index: Int) async throws -> Episode {
        try Episode.parse(try await client.getData("sim/batches/\(batchId)/episodes/\(index)"))
    }

    static func load(_ req: ReplayRequest, client: APIClient, robots: [CatalogRobot], occluderZUp: Bool) async throws -> ReplayBundle {
        var notes: [String] = []
        var refs = AnalysisReplayRefs()
        let robotId = req.robotId.trimmingCharacters(in: .whitespaces)
        if !req.analysisId.isEmpty {
            do {
                refs = try await analysisRefs(client, analysisId: req.analysisId, robotId: robotId)
            } catch {
                notes.append("Analysis details unavailable (\(error.localizedDescription)).")
            }
        }
        let asIsId = req.asIsBatchId.isEmpty ? (refs.beforeBatchId ?? "") : req.asIsBatchId
        let changedId = req.withChangesBatchId.isEmpty ? (refs.afterBatchId ?? "") : req.withChangesBatchId
        guard !asIsId.isEmpty else {
            throw NSError(domain: "Clearance.Replay", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "Enter a batch id or an analysis id with batches."])
        }

        async let asIs = episode(client, batchId: asIsId, index: req.episode)
        async let twinObj: Any? = try? await client.getJSON("sites/\(client.join.siteId)/twin")
        var changed: Episode?
        if !changedId.isEmpty {
            do { changed = try await episode(client, batchId: changedId, index: req.episode) } catch {
                notes.append("The with-changes episode could not be loaded (\(error.localizedDescription)).")
            }
        }

        var shape = RobotShape.standard(robotId: robotId.isEmpty ? "robot" : robotId)
        if let r = robots.first(where: { $0.id == robotId }) {
            shape = RobotShape.from(json: r.json)
        }
        if robots.first(where: { $0.id == robotId }) == nil {
            notes.append("Robot shape is the standard 0.5 m body; pick a robot to use its catalog shape.")
        } else {
            if shape.footprintApprox {
                notes.append("Robot shape is built from the catalog's overall dimensions.")
            }
            if !shape.heightKnown {
                notes.append("Robot height uses the standard 1.2 m; the catalog entry lists no height.")
            }
        }

        let twin = await twinObj as? [String: Any]
        let prims = twin.map { PrimitiveShape.parseAll(twin: $0) } ?? []
        var occluder: URL?
        if let twin {
            occluder = await fetchOccluder(client, twin: twin)
            if occluder == nil { notes.append("No occluder mesh for this twin: real furniture will not hide the robot.") }
        }
        return ReplayBundle(asIs: try await asIs, withChanges: changed, robot: shape, primitives: prims,
                            change: refs.change, verdict: refs.verdicts.first, occluderFile: occluder, notes: notes)
    }

    /// Twin.assets["occluder_usdz"] is an absolute URL or a storage key. Storage keys are served by
    /// `GET /sites/{site_id}/twin/assets/occluder.usdz?version=N` (docs/CONTRACTS.md "Twin assets"),
    /// which also redirects to an absolute URL when the twin stores one.
    static func fetchOccluder(_ client: APIClient, twin: [String: Any]) async -> URL? {
        guard let url = occluderURL(client, twin: twin) else { return nil }
        return try? await client.download(url, ext: "usdz")
    }

    static func occluderURL(_ client: APIClient, twin: [String: Any]) -> URL? {
        let assets = twin["assets"] as? [String: Any] ?? [:]
        guard let s = assets["occluder_usdz"] as? String, !s.isEmpty else { return nil }
        if let u = URL(string: s), let scheme = u.scheme?.lowercased(), scheme == "http" || scheme == "https" {
            return u
        }
        let site = (twin["site_id"] as? String) ?? client.join.siteId
        let version = (twin["version"] as? Int).map { "?version=\($0)" } ?? ""
        return client.url("sites/\(site)/twin/assets/occluder.usdz\(version)")
    }
}
