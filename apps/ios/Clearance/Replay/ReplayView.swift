import ClearanceKit
import RealityKit
import SwiftUI

/// Picks what to replay: an analysis (fills batches, layout change and verdict) and/or batch ids.
struct ReplaySetupView: View {
    @EnvironmentObject var model: AppModel
    @AppStorage("replay.analysisId") private var analysisId = ""
    @AppStorage("replay.robotId") private var robotId = ""
    @AppStorage("replay.asIsBatch") private var asIsBatch = ""
    @AppStorage("replay.withChangesBatch") private var withChangesBatch = ""
    @AppStorage("replay.episode") private var episode = 0
    @AppStorage(SettingsKey.occluderZUp) private var occluderZUp = SettingsDefaults.occluderZUp
    @State private var robots: [CatalogRobot] = []
    @State private var loading = false
    @State private var error: String?
    @State private var bundle: ReplayBundle?
    @State private var presenting = false

    var body: some View {
        Form {
            Section {
                TextField("Analysis id (optional)", text: $analysisId)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                Picker("Robot", selection: $robotId) {
                    Text("Standard 0.5 m shape").tag("")
                    ForEach(robots) { r in Text(r.title).tag(r.id) }
                }
            } header: {
                Text("Analysis")
            } footer: {
                Text("With an analysis id the app takes the as-is batch, the batch run with the recommended layout change, and the verdict for the chosen robot.")
            }
            Section("Episodes") {
                TextField("As-is batch id", text: $asIsBatch)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                TextField("With-changes batch id (optional)", text: $withChangesBatch)
                    .textInputAutocapitalization(.never).autocorrectionDisabled()
                Stepper("Episode \(episode)", value: $episode, in: 0...9999)
            }
            Section {
                Button {
                    Task { await open() }
                } label: {
                    HStack {
                        Label("Open AR replay", systemImage: "arkit")
                        Spacer()
                        if loading { ProgressView() }
                    }
                }
                .disabled(loading || (analysisId.isEmpty && asIsBatch.isEmpty))
                if let error {
                    Text(error).font(.footnote).foregroundStyle(Theme.notCleared)
                }
            } footer: {
                Text("Stand where you can see the floor marker. The replay appears once the marker is recognised and stays aligned while it is in view.")
            }
        }
        .navigationTitle("AR replay")
        .task { await loadCatalog() }
        .fullScreenCover(isPresented: $presenting) {
            if let bundle { ReplayARScreen(bundle: bundle) }
        }
    }

    private func loadCatalog() async {
        guard robots.isEmpty, let client = model.apiClient else { return }
        robots = (try? await ReplayLoader.catalog(client)) ?? []
    }

    private func open() async {
        guard let client = model.apiClient else { return }
        loading = true
        error = nil
        defer { loading = false }
        let req = ReplayRequest(analysisId: analysisId.trimmingCharacters(in: .whitespaces),
                                robotId: robotId,
                                asIsBatchId: asIsBatch.trimmingCharacters(in: .whitespaces),
                                withChangesBatchId: withChangesBatch.trimmingCharacters(in: .whitespaces),
                                episode: episode)
        do {
            bundle = try await ReplayLoader.load(req, client: client, robots: robots, occluderZUp: occluderZUp)
            presenting = true
        } catch {
            self.error = error.localizedDescription
        }
    }
}

/// Full-screen AR replay with verdict badge, before/after toggle and a timeline of events.
struct ReplayARScreen: View {
    @Environment(\.dismiss) private var dismiss
    @StateObject private var controller: ReplayController
    @State private var scrubbing = false
    @State private var scrubValue = 0.0
    @State private var toastVisible = false
    @State private var showNotes = true

    init(bundle: ReplayBundle) {
        _controller = StateObject(wrappedValue: ReplayController(bundle: bundle))
    }

    var body: some View {
        ZStack {
            ARViewContainer(arView: controller.arView).ignoresSafeArea()
            if !controller.aligned { markerSearchOverlay }
            VStack(spacing: 10) {
                header
                if let ev = controller.lastEvent, toastVisible { eventToast(ev) }
                if showNotes, !controller.bundle.notes.isEmpty { notesCard }
                Spacer()
                transport
            }
            .padding()
        }
        .statusBarHidden()
        .onAppear { controller.start() }
        .onDisappear { controller.stop() }
        .onChange(of: controller.lastEvent) { _, ev in
            guard ev != nil else { return }
            withAnimation(.spring(duration: 0.3)) { toastVisible = true }
            DispatchQueue.main.asyncAfter(deadline: .now() + 2.5) {
                withAnimation(.easeOut(duration: 0.4)) { toastVisible = false }
            }
        }
    }

    // MARK: pieces

    private var header: some View {
        VStack(spacing: 8) {
            HStack(alignment: .top) {
                Button { dismiss() } label: {
                    Image(systemName: "xmark").font(.headline).foregroundStyle(.white)
                        .frame(width: 40, height: 40).background(.ultraThinMaterial, in: Circle())
                }
                Spacer()
                if let v = controller.bundle.verdict {
                    VerdictBadgeView(badge: v)
                        .frame(maxWidth: 300, alignment: .trailing)
                }
            }
            HStack {
                if controller.hasWithChanges {
                    Picker("Layout", selection: $controller.mode) {
                        ForEach(ReplayController.Mode.allCases) { m in Text(m.rawValue).tag(m) }
                    }
                    .pickerStyle(.segmented)
                    .frame(maxWidth: 280)
                    .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 9))
                }
                Spacer()
                alignmentPill
            }
            if controller.mode == .withChanges, let c = controller.bundle.change, !c.description.isEmpty {
                Pill(text: c.description, symbol: "arrow.left.and.right.square", color: .white)
            }
            if let msg = controller.sessionMessage {
                Pill(text: msg, symbol: "exclamationmark.triangle.fill", color: .yellow)
            }
        }
    }

    private var alignmentPill: some View {
        Menu {
            Toggle("Hold alignment", isOn: $controller.alignmentLocked)
            Button("Find the marker again") { controller.restartAlignment() }
            Toggle("Show simulated people", isOn: $controller.showPeople)
            Toggle("Show robot path", isOn: $controller.showTrail)
            if controller.occluderLoaded {
                Toggle("Show room mesh", isOn: $controller.showOccluderMesh)
            }
        } label: {
            Pill(text: controller.alignmentLocked ? "Alignment held" : (controller.markerTracked ? "Marker in view" : "Aligned"),
                 symbol: controller.alignmentLocked ? "lock.fill" : "scope",
                 color: controller.markerTracked || controller.alignmentLocked ? .white : .white.opacity(0.75))
        }
    }

    private var markerSearchOverlay: some View {
        VStack(spacing: 14) {
            ZStack {
                RoundedRectangle(cornerRadius: 18).strokeBorder(.white.opacity(0.9), style: StrokeStyle(lineWidth: 3, dash: [14, 10]))
                    .frame(width: 180, height: 180)
                Image(systemName: "qrcode").font(.system(size: 54)).foregroundStyle(.white.opacity(0.85))
            }
            Text("Point the camera at the floor marker")
                .font(.headline).foregroundStyle(.white)
            Text("The replay is placed in this room using the same marker as the scan.")
                .font(.footnote).foregroundStyle(.white.opacity(0.85)).multilineTextAlignment(.center)
        }
        .padding(24)
        .background(.black.opacity(0.45), in: RoundedRectangle(cornerRadius: 22))
        .transition(.opacity)
    }

    private func eventToast(_ e: EpisodeEvent) -> some View {
        HStack(spacing: 8) {
            Circle().fill(Theme.color(for: e.kind)).frame(width: 10, height: 10)
            Text(e.title).font(.subheadline.weight(.semibold))
            Text(String(format: "at %.1f s", e.t - controller.bundle.asIs.startTime)).font(.caption).foregroundStyle(.secondary)
        }
        .padding(.horizontal, 14).padding(.vertical, 8)
        .background(.ultraThinMaterial, in: Capsule())
        .transition(.move(edge: .top).combined(with: .opacity))
    }

    private var notesCard: some View {
        HStack(alignment: .top) {
            VStack(alignment: .leading, spacing: 4) {
                ForEach(controller.bundle.notes, id: \.self) { Text($0).font(.caption) }
            }
            Spacer()
            Button { showNotes = false } label: { Image(systemName: "xmark.circle.fill") }
                .foregroundStyle(.secondary)
        }
        .padding(10)
        .background(.ultraThinMaterial, in: RoundedRectangle(cornerRadius: 12))
    }

    private var transport: some View {
        VStack(spacing: 8) {
            timeline
            HStack(spacing: 16) {
                Button { controller.isPlaying.toggle() } label: {
                    Image(systemName: controller.isPlaying ? "pause.fill" : "play.fill")
                        .font(.title2).frame(width: 48, height: 48)
                        .background(Theme.accent, in: Circle()).foregroundStyle(.white)
                }
                Text("\(clock(controller.time)) / \(clock(controller.duration))")
                    .font(.callout.monospacedDigit()).foregroundStyle(.white)
                Spacer()
                Menu {
                    ForEach([0.5, 1.0, 2.0, 4.0], id: \.self) { r in
                        Button(r == 1 ? "Real time" : "\(r.formatted())×") { controller.rate = r }
                    }
                } label: {
                    Pill(text: controller.rate == 1 ? "Real time" : "\(controller.rate.formatted())×", symbol: "speedometer")
                }
            }
            legend
        }
        .padding(12)
        .background(.black.opacity(0.5), in: RoundedRectangle(cornerRadius: 18))
    }

    private var timeline: some View {
        ZStack(alignment: .leading) {
            GeometryReader { geo in
                ForEach(controller.eventMarks) { m in
                    Capsule().fill(Theme.color(for: m.kind))
                        .frame(width: 3, height: 14)
                        .position(x: CGFloat(10 + m.progress * Double(geo.size.width - 20)), y: geo.size.height / 2)
                }
            }
            .frame(height: 30)
            .allowsHitTesting(false)
            Slider(value: Binding(get: { scrubbing ? scrubValue : controller.progress },
                                  set: { scrubValue = $0; controller.seek(toProgress: $0) }),
                   in: 0...1,
                   onEditingChanged: { editing in
                       scrubbing = editing
                       if editing { scrubValue = controller.progress }
                   })
            .tint(Theme.accent)
        }
    }

    private var legend: some View {
        HStack(spacing: 14) {
            legendItem(Theme.clearedWithChanges, "Safety stop")
            legendItem(Theme.notCleared, "Near miss / stopped")
            legendItem(Theme.cleared, "Complete")
            Spacer()
        }
        .font(.caption2)
        .foregroundStyle(.white.opacity(0.85))
    }

    private func legendItem(_ c: Color, _ t: String) -> some View {
        HStack(spacing: 4) { Circle().fill(c).frame(width: 8, height: 8); Text(t) }
    }

    private func clock(_ t: Double) -> String {
        let s = max(0, Int(t.rounded()))
        return String(format: "%d:%02d", s / 60, s % 60)
    }
}
