import ClearanceKit
import SwiftUI

struct UploadsView: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var uploads: UploadManager
    @State private var items: [CaptureSummary] = []
    @State private var confirmDelete: CaptureSummary?

    var body: some View {
        List {
            if items.isEmpty {
                ContentUnavailableView("No captures yet", systemImage: "camera.viewfinder",
                                       description: Text("Finished scans appear here and upload to the joined site."))
            }
            ForEach(items) { item in
                row(item)
                    .swipeActions {
                        Button("Delete", role: .destructive) { confirmDelete = item }
                    }
            }
        }
        .navigationTitle("Captures")
        .refreshable { reload() }
        .onAppear { reload() }
        .onReceive(uploads.$progress) { _ in reload() }
        .confirmationDialog("Delete this capture from the phone?", isPresented: Binding(
            get: { confirmDelete != nil }, set: { if !$0 { confirmDelete = nil } }), titleVisibility: .visible) {
            Button("Delete", role: .destructive) {
                if let c = confirmDelete { try? model.store.delete(c); reload() }
                confirmDelete = nil
            }
        }
    }

    private func reload() { items = model.store.list() }

    @ViewBuilder private func row(_ item: CaptureSummary) -> some View {
        let live = uploads.progress[item.id]
        let completed = live?.completed ?? item.upload?.completed ?? false
        let fraction = live?.fraction ?? item.upload?.fraction ?? 0
        VStack(alignment: .leading, spacing: 6) {
            HStack {
                Text(item.manifest?.captureId ?? item.id).font(.subheadline.monospaced())
                Spacer()
                if completed {
                    Label("Uploaded", systemImage: "checkmark.icloud").foregroundStyle(Theme.cleared).font(.caption)
                }
            }
            if let m = item.manifest {
                Text("\(m.device.tier.rawValue) · \(m.stats?.keyframes ?? 0) keyframes · \(Int(m.durationS)) s · \(ByteCountFormatter.string(fromByteCount: item.bytes, countStyle: .file))")
                    .font(.caption)
                    .foregroundStyle(.secondary)
                if let s = m.stats {
                    Text("Coverage: \(s.coveredCells) of \(s.seenCells) cells from two or more angles · \(m.privacy.faceBoxesBlurred) face areas blurred")
                        .font(.caption2)
                        .foregroundStyle(.secondary)
                }
            } else {
                Text("Scan not finished (no capture.json)").font(.caption).foregroundStyle(.secondary)
            }
            if !completed && item.isFinalized {
                if live?.running == true || fraction > 0 {
                    ProgressView(value: fraction)
                }
                HStack {
                    Text(live?.status ?? item.upload?.lastError ?? "Ready to upload")
                        .font(.caption2)
                        .foregroundStyle(live?.error == nil ? Color.secondary : Theme.notCleared)
                        .lineLimit(2)
                    Spacer()
                    if live?.running == true {
                        Button("Pause") { uploads.cancel(item.id) }.buttonStyle(.bordered)
                    } else {
                        Button(fraction > 0 ? "Resume" : "Upload") {
                            model.startUpload(dir: item.url)
                        }
                        .buttonStyle(.borderedProminent)
                        .disabled(model.apiClient == nil)
                    }
                }
            }
        }
        .padding(.vertical, 4)
    }
}
