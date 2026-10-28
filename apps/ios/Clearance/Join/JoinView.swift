import AVFoundation
import ClearanceKit
import SwiftUI
import UIKit

struct JoinView: View {
    @EnvironmentObject var model: AppModel
    @Environment(\.dismiss) private var dismiss
    @State private var manual = ""
    @State private var message: String?
    @State private var cameraDenied = false

    var body: some View {
        VStack(spacing: 16) {
            ZStack {
                if cameraDenied {
                    ContentUnavailableView("Camera access is off",
                                           systemImage: "camera.fill",
                                           description: Text("Allow camera access in Settings to scan the site QR code, or paste the join link below."))
                } else {
                    QRScannerView { code in handle(code) }
                        .clipShape(RoundedRectangle(cornerRadius: 18, style: .continuous))
                    RoundedRectangle(cornerRadius: 22, style: .continuous)
                        .strokeBorder(.white.opacity(0.85), lineWidth: 3)
                        .frame(width: 220, height: 220)
                }
            }
            .frame(maxWidth: .infinity)
            .frame(height: 360)

            Text("Point the camera at the QR code in the Clearance console (Site → Join devices).")
                .font(.footnote)
                .foregroundStyle(.secondary)
                .multilineTextAlignment(.center)

            HStack {
                TextField("Join link or JSON", text: $manual)
                    .textInputAutocapitalization(.never)
                    .autocorrectionDisabled()
                    .textFieldStyle(.roundedBorder)
                Button("Join") { handle(manual) }
                    .buttonStyle(.borderedProminent)
                    .disabled(manual.trimmingCharacters(in: .whitespaces).isEmpty)
            }
            if let message {
                Text(message).font(.footnote).foregroundStyle(Theme.notCleared)
            }
            Spacer()
        }
        .padding()
        .navigationTitle("Join a site")
        .task {
            switch AVCaptureDevice.authorizationStatus(for: .video) {
            case .denied, .restricted: cameraDenied = true
            case .notDetermined: cameraDenied = !(await AVCaptureDevice.requestAccess(for: .video))
            default: cameraDenied = false
            }
        }
    }

    private func handle(_ code: String) {
        guard let join = SiteJoinParser.parse(code) else {
            message = "That code is not a Clearance site link."
            return
        }
        UINotificationFeedbackGenerator().notificationOccurred(.success)
        model.site = join
        dismiss()
    }
}

/// AVFoundation QR reader (works on every iPhone that runs ARKit).
struct QRScannerView: UIViewControllerRepresentable {
    var onCode: (String) -> Void

    func makeUIViewController(context: Context) -> QRScannerController {
        let vc = QRScannerController()
        vc.onCode = onCode
        return vc
    }

    func updateUIViewController(_ vc: QRScannerController, context: Context) {
        vc.onCode = onCode
    }
}

final class QRScannerController: UIViewController, AVCaptureMetadataOutputObjectsDelegate {
    var onCode: ((String) -> Void)?
    private let session = AVCaptureSession()
    private let sessionQueue = DispatchQueue(label: "clearance.qr.session")
    private var preview: AVCaptureVideoPreviewLayer?
    private var lastCode: String?
    private var lastCodeTime = Date.distantPast

    override func viewDidLoad() {
        super.viewDidLoad()
        view.backgroundColor = .black
        guard let device = AVCaptureDevice.default(for: .video),
              let input = try? AVCaptureDeviceInput(device: device),
              session.canAddInput(input) else { return }
        session.beginConfiguration()
        session.addInput(input)
        let output = AVCaptureMetadataOutput()
        if session.canAddOutput(output) {
            session.addOutput(output)
            output.setMetadataObjectsDelegate(self, queue: .main)
            if output.availableMetadataObjectTypes.contains(.qr) { output.metadataObjectTypes = [.qr] }
        }
        session.commitConfiguration()
        let layer = AVCaptureVideoPreviewLayer(session: session)
        layer.videoGravity = .resizeAspectFill
        view.layer.addSublayer(layer)
        preview = layer
    }

    override func viewDidLayoutSubviews() {
        super.viewDidLayoutSubviews()
        preview?.frame = view.bounds
    }

    override func viewWillAppear(_ animated: Bool) {
        super.viewWillAppear(animated)
        let s = session
        sessionQueue.async { if !s.isRunning { s.startRunning() } }
    }

    override func viewWillDisappear(_ animated: Bool) {
        super.viewWillDisappear(animated)
        let s = session
        sessionQueue.async { if s.isRunning { s.stopRunning() } }
    }

    // Delivered on the main queue (see setMetadataObjectsDelegate above).
    nonisolated func metadataOutput(_ output: AVCaptureMetadataOutput, didOutput metadataObjects: [AVMetadataObject],
                                    from connection: AVCaptureConnection) {
        guard let obj = metadataObjects.first as? AVMetadataMachineReadableCodeObject,
              obj.type == .qr, let value = obj.stringValue else { return }
        MainActor.assumeIsolated {
            if value == lastCode, Date().timeIntervalSince(lastCodeTime) < 2 { return }
            lastCode = value
            lastCodeTime = Date()
            onCode?(value)
        }
    }
}
