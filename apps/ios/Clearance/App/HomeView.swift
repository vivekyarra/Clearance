import ARKit
import ClearanceKit
import SwiftUI

struct HomeView: View {
    @EnvironmentObject var model: AppModel
    @EnvironmentObject var uploads: UploadManager

    var body: some View {
        NavigationStack {
            List {
                Section {
                    siteCard
                }
                Section("Capture") {
                    NavigationLink {
                        CaptureView()
                    } label: {
                        Label("Scan the space", systemImage: "camera.viewfinder")
                    }
                    .disabled(model.site == nil || !ARWorldTrackingConfiguration.isSupported)
                    NavigationLink {
                        UploadsView()
                    } label: {
                        HStack {
                            Label("Captures and uploads", systemImage: "icloud.and.arrow.up")
                            Spacer()
                            if uploads.activeCount > 0 {
                                ProgressView().controlSize(.small)
                            }
                        }
                    }
                }
                Section("Replay") {
                    NavigationLink {
                        ReplaySetupView()
                    } label: {
                        Label("AR replay in this room", systemImage: "arkit")
                    }
                    .disabled(model.site == nil || !ARWorldTrackingConfiguration.isSupported)
                }
                Section {
                    NavigationLink {
                        SettingsView()
                    } label: {
                        Label("Settings", systemImage: "gearshape")
                    }
                } footer: {
                    Text(deviceFooter)
                }
            }
            .navigationTitle("Clearance")
        }
    }

    @ViewBuilder private var siteCard: some View {
        if let site = model.site {
            VStack(alignment: .leading, spacing: 6) {
                Label(site.displayName, systemImage: "building.2")
                    .font(.headline)
                Text("Uploads go to \(site.apiHostDisplay) · \(site.apiBase.absoluteString)")
                    .font(.caption.monospaced())
                    .foregroundStyle(.secondary)
                HStack {
                    NavigationLink("Join another site") { JoinView() }
                        .font(.footnote)
                    Spacer()
                    Button("Leave", role: .destructive) { model.site = nil }
                        .font(.footnote)
                        .buttonStyle(.borderless)
                }
            }
            .padding(.vertical, 4)
        } else {
            NavigationLink {
                JoinView()
            } label: {
                VStack(alignment: .leading, spacing: 4) {
                    Label("Join a site", systemImage: "qrcode.viewfinder")
                        .font(.headline)
                    Text("Scan the QR code shown in the Clearance console.")
                        .font(.footnote)
                        .foregroundStyle(.secondary)
                }
                .padding(.vertical, 4)
            }
        }
    }

    private var deviceFooter: String {
        let tier = DeviceInfo.tier == .lidar ? "LiDAR depth (T1)" : "Visual-inertial, monocular (T2)"
        let people = ARWorldTrackingConfiguration.supportsFrameSemantics(.personSegmentationWithDepth) ? "yes" : "no"
        return "\(DeviceInfo.modelIdentifier) · \(DeviceInfo.osVersion) · capture tier: \(tier) · people occlusion: \(people)"
    }
}

struct SettingsView: View {
    @AppStorage(SettingsKey.markerSizeM) private var markerSizeM = SettingsDefaults.markerSizeM
    @AppStorage(SettingsKey.maxLongSide) private var maxLongSide = SettingsDefaults.maxLongSide
    @AppStorage(SettingsKey.blurThreshold) private var blurThreshold = SettingsDefaults.blurThreshold
    @AppStorage(SettingsKey.leveledAnchoring) private var leveled = SettingsDefaults.leveledAnchoring
    @AppStorage(SettingsKey.autoUpload) private var autoUpload = SettingsDefaults.autoUpload
    @AppStorage(SettingsKey.occluderZUp) private var occluderZUp = SettingsDefaults.occluderZUp
    @AppStorage(SettingsKey.partSizeMB) private var partSizeMB = SettingsDefaults.partSizeMB

    private var markerCM: Binding<Double> {
        Binding(get: { markerSizeM * 100 }, set: { markerSizeM = $0 / 100 })
    }

    var body: some View {
        Form {
            Section {
                HStack {
                    Text("Black square edge")
                    Spacer()
                    TextField("16.0", value: markerCM, format: .number.precision(.fractionLength(1)))
                        .keyboardType(.decimalPad)
                        .multilineTextAlignment(.trailing)
                        .frame(width: 80)
                    Text("cm").foregroundStyle(.secondary)
                }
                Toggle("Level the site frame with gravity", isOn: $leveled)
            } header: {
                Text("Marker (AprilTag 36h11, id 0)")
            } footer: {
                Text("Measure the printed tag's outer black square with a ruler. The site origin is the tag centre, +X toward the tag's right edge, +Y toward its top edge, +Z up.")
            }

            Section {
                Picker("Saved image long side", selection: $maxLongSide) {
                    Text("Sensor size").tag(0)
                    Text("1920 px").tag(1920)
                    Text("1440 px").tag(1440)
                    Text("1280 px").tag(1280)
                }
                VStack(alignment: .leading) {
                    Text("Sharpness threshold: \(Int(blurThreshold))")
                    Slider(value: $blurThreshold, in: 5...200, step: 5)
                }
                Toggle("Upload when the scan ends", isOn: $autoUpload)
                Stepper("Upload part size: \(partSizeMB) MB", value: $partSizeMB, in: 4...64, step: 4)
            } header: {
                Text("Capture")
            } footer: {
                Text("Frames whose Laplacian-variance sharpness falls below the threshold are skipped. The scan screen shows the live value so you can tune it for your room and light.")
            }

            Section {
                Toggle("Occluder mesh is Z-up (site frame)", isOn: $occluderZUp)
            } header: {
                Text("AR replay")
            } footer: {
                Text("Turn off if the twin's occluder.usdz was exported Y-up; the app then rotates it into the site frame.")
            }
        }
        .navigationTitle("Settings")
    }
}
