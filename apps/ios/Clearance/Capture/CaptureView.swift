import ARKit
import ClearanceKit
import RealityKit
import SwiftUI

struct ARViewContainer: UIViewRepresentable {
    let arView: ARView
    func makeUIView(context: Context) -> ARView { arView }
    func updateUIView(_ uiView: ARView, context: Context) {}
}

struct CaptureView: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var uploads: UploadManager
    @StateObject private var engine = CaptureEngine()
    @State private var coach: CoachLink?
    @AppStorage(SettingsKey.autoUpload) private var autoUpload = SettingsDefaults.autoUpload
    @AppStorage(SettingsKey.blurThreshold) private var blurThreshold = SettingsDefaults.blurThreshold

    var body: some View {
        ZStack {
            ARViewContainer(arView: engine.arView).ignoresSafeArea()
            VStack(spacing: 10) {
                topBar
                if let p = engine.prompt { promptBanner(p) }
                if let check = engine.markerCheck, engine.phase == .findingMarker || engine.phase == .preview {
                    Pill(text: check, symbol: "exclamationmark.triangle", color: .yellow)
                }
                Spacer()
                bottomPanel
            }
            .padding()
            .animation(.easeInOut(duration: 0.2), value: engine.prompt)
        }
        .navigationTitle("Scan")
        .navigationBarTitleDisplayMode(.inline)
        .toolbarBackground(.hidden, for: .navigationBar)
        .onAppear {
            engine.startPreview()
            if coach == nil, let client = model.apiClient {
                let link = CoachLink(client: client)
                link.connect()
                coach = link
            }
        }
        .onDisappear {
            engine.stop()
            engine.pause()
            coach?.close()
            coach = nil
        }
        .onChange(of: engine.keyframes) { _, n in
            coach?.update(grid: engine.coverage, prompt: engine.prompt?.text, frames: n, tracking: engine.trackingText)
        }
        .onChange(of: engine.prompt) { _, p in
            coach?.update(grid: engine.coverage, prompt: p?.text, frames: engine.keyframes, tracking: engine.trackingText)
        }
        .onChange(of: engine.phase) { _, phase in
            if case .finished(let dir) = phase, autoUpload, model.apiClient != nil {
                model.startUpload(dir: dir)
            }
        }
    }

    private var topBar: some View {
        HStack(spacing: 8) {
            Pill(text: engine.tier == .lidar ? "LiDAR" : "VIO", symbol: engine.tier == .lidar ? "dot.radiowaves.up.forward" : "camera")
            markerPill
            Spacer()
            if engine.phase == .recording {
                Pill(text: timeString(engine.elapsed), symbol: "record.circle", color: Theme.notCleared)
            }
        }
    }

    @ViewBuilder private var markerPill: some View {
        switch engine.phase {
        case .recording:
            if let r = engine.markerResidualCM, engine.markerVisible {
                Pill(text: String(format: "Marker %.1f cm", r), symbol: "scope", color: r < 3 ? .white : .yellow)
            } else {
                Pill(text: "Site frame locked", symbol: "lock.fill")
            }
        case .findingMarker:
            Pill(text: engine.markerVisible ? "Marker seen" : "Find the marker", symbol: "viewfinder", color: .yellow)
        default:
            EmptyView()
        }
    }

    private func promptBanner(_ p: CaptureEngine.Prompt) -> some View {
        let color: Color
        switch p.level {
        case .info: color = Theme.accent
        case .warning: color = Theme.clearedWithChanges
        case .critical: color = Theme.notCleared
        }
        return Text(p.text)
            .font(.callout.weight(.semibold))
            .foregroundStyle(.white)
            .multilineTextAlignment(.center)
            .padding(.horizontal, 14)
            .padding(.vertical, 10)
            .frame(maxWidth: .infinity)
            .background(color.opacity(0.88), in: RoundedRectangle(cornerRadius: 14, style: .continuous))
            .transition(.move(edge: .top).combined(with: .opacity))
    }

    private var bottomPanel: some View {
        HStack(alignment: .bottom, spacing: 12) {
            VStack(alignment: .leading, spacing: 6) {
                CoverageMapView(grid: engine.coverage, camera: engine.cameraSite, heading: engine.headingSite)
                    .frame(width: 150, height: 150)
                    .background(.black.opacity(0.45), in: RoundedRectangle(cornerRadius: 12))
                Text("Covered \(engine.coverage.coveredCount)/\(engine.coverage.seenCount) cells")
                    .font(.caption2.monospacedDigit())
                    .foregroundStyle(.white)
            }
            VStack(alignment: .leading, spacing: 4) {
                stat("Keyframes", "\(engine.keyframes)")
                stat("Sharpness", "\(Int(engine.sharpness)) / \(Int(blurThreshold))")
                stat("Skipped (blur)", "\(engine.rejectedBlur)")
                stat("Faces blurred", "\(engine.facesBlurred)")
            }
            .padding(10)
            .background(.black.opacity(0.45), in: RoundedRectangle(cornerRadius: 12))
            Spacer(minLength: 0)
            actionButton
        }
    }

    private func stat(_ k: String, _ v: String) -> some View {
        HStack {
            Text(k).foregroundStyle(.white.opacity(0.75))
            Spacer(minLength: 8)
            Text(v).foregroundStyle(.white).monospacedDigit()
        }
        .font(.caption)
        .frame(width: 140)
    }

    @ViewBuilder private var actionButton: some View {
        switch engine.phase {
        case .preview:
            roundButton("Start", color: Theme.accent) {
                if let site = model.site { engine.begin(site: site, store: model.store) }
            }
        case .findingMarker:
            roundButton("Cancel", color: .gray) { engine.stop() }
        case .recording:
            roundButton("Stop", color: Theme.notCleared) { engine.stop() }
        case .finishing:
            ProgressView().tint(.white).frame(width: 72, height: 72)
        case .finished:
            VStack(spacing: 6) {
                Image(systemName: "checkmark.circle.fill").font(.largeTitle).foregroundStyle(Theme.cleared)
                Text(autoUpload ? "Uploading" : "Saved").font(.caption).foregroundStyle(.white)
                Button("New scan") { engine.reset() }.font(.caption).buttonStyle(.borderedProminent)
            }
        case .failed(let msg):
            VStack(spacing: 6) {
                Text(msg).font(.caption).foregroundStyle(.white).frame(maxWidth: 160)
                Button("Back") { engine.reset() }.buttonStyle(.borderedProminent)
            }
        }
    }

    private func roundButton(_ title: String, color: Color, action: @escaping () -> Void) -> some View {
        Button(action: action) {
            Text(title)
                .font(.headline)
                .foregroundStyle(.white)
                .frame(width: 72, height: 72)
                .background(color, in: Circle())
                .overlay(Circle().strokeBorder(.white.opacity(0.8), lineWidth: 3))
        }
        .buttonStyle(.plain)
    }

    private func timeString(_ t: TimeInterval) -> String {
        let s = Int(t)
        return String(format: "%d:%02d", s / 60, s % 60)
    }
}

/// Live top-down coverage mini-map (site frame, +Y up on screen). Amber = seen from one side,
/// green = seen from at least two directions, dots = where the phone has been.
struct CoverageMapView: View {
    var grid: CoverageGrid
    var camera: SIMD3<Double>?
    var heading: Double

    var body: some View {
        Canvas { ctx, size in
            var lo = SIMD2<Double>(-2, -2), hi = SIMD2<Double>(2, 2)
            if let b = grid.bounds {
                lo = simd_min(lo, SIMD2(Double(b.min.ix), Double(b.min.iy)) * grid.cellSize)
                hi = simd_max(hi, SIMD2(Double(b.max.ix + 1), Double(b.max.iy + 1)) * grid.cellSize)
            }
            if let c = camera {
                lo = simd_min(lo, SIMD2(c.x, c.y) - 1)
                hi = simd_max(hi, SIMD2(c.x, c.y) + 1)
            }
            let span = max(hi.x - lo.x, hi.y - lo.y)
            let scale = Double(min(size.width, size.height) - 12) / span
            let mid = (lo + hi) / 2
            let halfW = Double(size.width) / 2, halfH = Double(size.height) / 2
            func pt(_ x: Double, _ y: Double) -> CGPoint {
                let sx: Double = halfW + (x - mid.x) * scale
                let sy: Double = halfH - (y - mid.y) * scale
                return CGPoint(x: sx, y: sy)
            }
            let cs = grid.cellSize * scale
            for (cell, _) in grid.cells {
                let state = grid.state(cell)
                guard state != .unseen else { continue }
                let o = pt(Double(cell.ix) * grid.cellSize, Double(cell.iy + 1) * grid.cellSize)
                let side = CGFloat(max(1, cs - 1))
                let r = CGRect(x: o.x + 0.5, y: o.y + 0.5, width: side, height: side)
                ctx.fill(Path(r), with: .color(state == .covered ? Theme.cleared.opacity(0.85) : Theme.clearedWithChanges.opacity(0.75)))
            }
            for cell in grid.visited {
                let c = grid.center(cell)
                let p = pt(c.x, c.y)
                ctx.fill(Path(ellipseIn: CGRect(x: p.x - 1.5, y: p.y - 1.5, width: 3, height: 3)), with: .color(.white.opacity(0.6)))
            }
            // Marker at the origin.
            let o = pt(0, 0)
            ctx.fill(Path(CGRect(x: o.x - 3, y: o.y - 3, width: 6, height: 6)), with: .color(.white))
            if let c = camera {
                let p = pt(c.x, c.y)
                let px = Double(p.x), py = Double(p.y)
                var tri = Path()
                let a = -heading  // screen y points down
                func rot(_ dx: Double, _ dy: Double) -> CGPoint {
                    let rx: Double = px + dx * cos(a) - dy * sin(a)
                    let ry: Double = py + dx * sin(a) + dy * cos(a)
                    return CGPoint(x: rx, y: ry)
                }
                tri.move(to: rot(9, 0))
                tri.addLine(to: rot(-6, 5))
                tri.addLine(to: rot(-6, -5))
                tri.closeSubpath()
                ctx.fill(tri, with: .color(Theme.accent))
                ctx.stroke(tri, with: .color(.white), lineWidth: 1)
            }
        }
        .accessibilityLabel("Coverage map: \(grid.coveredCount) of \(grid.seenCount) cells covered")
    }
}
