import ClearanceKit
import SwiftUI
import UIKit

enum Theme {
    static let accent = Color(red: 0.10, green: 0.55, blue: 0.62)
    static let cleared = Color(red: 0.13, green: 0.66, blue: 0.38)
    static let clearedWithChanges = Color(red: 0.95, green: 0.62, blue: 0.10)
    static let notCleared = Color(red: 0.86, green: 0.22, blue: 0.20)
    static let panel = Color.black.opacity(0.55)

    static func color(for label: VerdictBadge.Label) -> Color {
        switch label {
        case .cleared: return cleared
        case .clearedWithChanges: return clearedWithChanges
        case .notCleared: return notCleared
        }
    }

    static func symbol(for label: VerdictBadge.Label) -> String {
        switch label {
        case .cleared: return "checkmark.seal.fill"
        case .clearedWithChanges: return "arrow.triangle.2.circlepath.circle.fill"
        case .notCleared: return "xmark.octagon.fill"
        }
    }

    static func color(for kind: EpisodeEventKind) -> Color {
        switch kind {
        case .stop: return clearedWithChanges
        case .nearMiss, .collision, .fail: return notCleared
        case .success: return cleared
        case .replan, .other: return .white
        }
    }
}

/// Rounded translucent pill used across the HUDs.
struct Pill: View {
    var text: String
    var symbol: String?
    var color: Color = .white

    var body: some View {
        HStack(spacing: 6) {
            if let symbol { Image(systemName: symbol) }
            Text(text).lineLimit(1)
        }
        .font(.footnote.weight(.semibold))
        .foregroundStyle(color)
        .padding(.horizontal, 10)
        .padding(.vertical, 6)
        .background(.ultraThinMaterial.opacity(0.9), in: Capsule())
    }
}

/// Verdict badge (Cleared / Cleared with changes / Not cleared), as decided by the server.
struct VerdictBadgeView: View {
    var badge: VerdictBadge
    var compact = false

    var body: some View {
        HStack(alignment: .top, spacing: 10) {
            Image(systemName: Theme.symbol(for: badge.label))
                .font(compact ? .title3 : .title2)
                .foregroundStyle(.white)
            VStack(alignment: .leading, spacing: 2) {
                Text(badge.label.rawValue)
                    .font(compact ? .subheadline.weight(.bold) : .headline.weight(.bold))
                    .foregroundStyle(.white)
                if !compact && !badge.mainReason.isEmpty {
                    Text(badge.mainReason)
                        .font(.caption)
                        .foregroundStyle(.white.opacity(0.9))
                        .lineLimit(2)
                }
            }
        }
        .padding(.horizontal, 12)
        .padding(.vertical, 8)
        .background(Theme.color(for: badge.label).opacity(0.92), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
        .shadow(color: .black.opacity(0.25), radius: 6, y: 2)
        .accessibilityElement(children: .combine)
    }
}

extension UIColor {
    static let clearanceRobotBody = UIColor(white: 0.88, alpha: 1)
    static let clearanceRobotAccent = UIColor(white: 0.35, alpha: 1)
    static let clearanceAmber = UIColor(red: 0.98, green: 0.66, blue: 0.12, alpha: 1)
    static let clearanceRed = UIColor(red: 0.92, green: 0.20, blue: 0.18, alpha: 1)
    static let clearanceGreen = UIColor(red: 0.16, green: 0.72, blue: 0.42, alpha: 1)
    static let clearanceTeal = UIColor(red: 0.12, green: 0.64, blue: 0.72, alpha: 1)
}
