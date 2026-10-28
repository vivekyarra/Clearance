import Foundation

/// Coach message the scanner sends over `WS /live/{session}?role=scanner` (docs/CONTRACTS.md "Coach message"):
/// `{"type": "coach", "site_id", "coverage": {"cell_m", "origin": [x, y], "width", "height", "cells"} | null,
///   "prompt": String | null, "t": Double, "status": {"device", "frames", "tracking"}?}`.
/// `cells` is row-major with row 0 = max y (north up, like the occupancy grid); 0 = unseen,
/// 1 = seen from one direction, 2 = seen from two or more directions. `origin` is the min-x / min-y corner.
public enum CoachMessage {
    /// Keeps one message well under the server's 128 KiB WebSocket limit (5000 m2 at 0.5 m cells).
    public static let maxCells = 20_000

    public static func coverage(_ grid: CoverageGrid) -> [String: Any]? {
        guard let b = grid.bounds else { return nil }
        let w = b.max.ix - b.min.ix + 1
        let h = b.max.iy - b.min.iy + 1
        guard w > 0, h > 0, w * h <= maxCells else { return nil }
        var cells = [Int](repeating: 0, count: w * h)
        for (c, _) in grid.cells {
            let col = c.ix - b.min.ix
            let row = b.max.iy - c.iy
            switch grid.state(c) {
            case .unseen: continue
            case .single: cells[row * w + col] = 1
            case .covered: cells[row * w + col] = 2
            }
        }
        return ["cell_m": grid.cellSize,
                "origin": [Double(b.min.ix) * grid.cellSize, Double(b.min.iy) * grid.cellSize],
                "width": w, "height": h, "cells": cells]
    }

    public static func make(siteId: String, grid: CoverageGrid?, prompt: String?, t: Double,
                            device: String? = nil, frames: Int? = nil, tracking: String? = nil) -> [String: Any] {
        var msg: [String: Any] = ["type": "coach", "site_id": siteId, "t": t,
                                  "coverage": (grid.flatMap { coverage($0) } as Any?) ?? NSNull(),
                                  "prompt": (prompt as Any?) ?? NSNull()]
        var status: [String: Any] = [:]
        if let device { status["device"] = device }
        if let frames { status["frames"] = frames }
        if let tracking { status["tracking"] = tracking }
        if !status.isEmpty { msg["status"] = status }
        return msg
    }

    public static func encode(_ msg: [String: Any]) throws -> String {
        let data = try JSONSerialization.data(withJSONObject: msg, options: [.sortedKeys])
        return String(decoding: data, as: UTF8.self)
    }
}
