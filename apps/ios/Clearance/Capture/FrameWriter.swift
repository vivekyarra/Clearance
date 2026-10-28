import ClearanceKit
import CoreImage
import CoreVideo
import Foundation
import ImageIO
import Vision

/// Everything the writer needs for one keyframe; built on the main thread from an ARFrame.
struct KeyframeJob {
    let index: Int
    let pixelBuffer: CVPixelBuffer
    let orientation: UprightOrientation   // makes the sensor image upright (for face detection)
    let outWidth: Int
    let outHeight: Int
    let pose: PoseLine
    let featureLine: Data
    let depth: (values: [Float], width: Int, height: Int)?
    let confidence: (values: [UInt8], width: Int, height: Int)?
}

struct KeyframeResult {
    let index: Int
    let faces: Int
    let bytes: Int
}

struct WriterSummary {
    let keyframes: Int
    let faceBoxes: Int
    let errors: [String]
}

/// Serial background writer: face blur (Vision + CIGaussianBlur), JPEG q0.85 in the sensor
/// orientation (matching K and T_world_cam), depth/confidence PNGs, then the poses.jsonl and
/// feature_points.jsonl lines, so every pose line refers to an image already on disk.
final class FrameWriter {
    let dir: URL
    private let queue = DispatchQueue(label: "clearance.capture.writer", qos: .userInitiated)
    private let context = CIContext(options: [.cacheIntermediates: false])
    private let colorSpace = CGColorSpace(name: CGColorSpace.sRGB)!
    private var poses: FileHandle?
    private var features: FileHandle?
    private var sightings: FileHandle?
    private var faceBoxes = 0
    private var written = 0
    private var errors: [String] = []
    static let jpegQuality: CGFloat = 0.85

    init(dir: URL) throws {
        self.dir = dir
        poses = try FrameWriter.openAppend(dir.appendingPathComponent("poses.jsonl"))
        features = try FrameWriter.openAppend(dir.appendingPathComponent("feature_points.jsonl"))
        sightings = try FrameWriter.openAppend(dir.appendingPathComponent("marker_sightings.jsonl"))
    }

    private static func openAppend(_ url: URL) throws -> FileHandle {
        if !FileManager.default.fileExists(atPath: url.path) {
            FileManager.default.createFile(atPath: url.path, contents: nil)
        }
        let h = try FileHandle(forWritingTo: url)
        try h.seekToEnd()
        return h
    }

    func enqueue(_ job: KeyframeJob, completion: @escaping @MainActor (Result<KeyframeResult, Error>) -> Void) {
        queue.async {
            let result = Result { try self.process(job) }
            Task { @MainActor in completion(result) }
        }
    }

    func appendSighting(_ line: Data) {
        queue.async {
            do { try self.sightings?.write(contentsOf: line) } catch { self.errors.append("sightings: \(error)") }
        }
    }

    func finish(completion: @escaping @MainActor (WriterSummary) -> Void) {
        queue.async {
            for h in [self.poses, self.features, self.sightings] {
                do {
                    try h?.synchronize()
                    try h?.close()
                } catch {
                    self.errors.append("close: \(error)")
                }
            }
            self.poses = nil
            self.features = nil
            self.sightings = nil
            let s = WriterSummary(keyframes: self.written, faceBoxes: self.faceBoxes, errors: self.errors)
            Task { @MainActor in completion(s) }
        }
    }

    // MARK: per-frame work (writer queue)

    private func process(_ job: KeyframeJob) throws -> KeyframeResult {
        let name = CaptureNaming.frameName(job.index)
        let mapName = CaptureNaming.mapName(job.index)
        let (jpeg, faces) = try renderBlurredJPEG(job)
        try jpeg.write(to: dir.appendingPathComponent("frames").appendingPathComponent(name), options: .atomic)
        var bytes = jpeg.count

        if let d = job.depth {
            let png = PNGWriter.gray16(width: d.width, height: d.height, pixels: DepthEncoding.millimetres(fromMetres: d.values))
            try png.write(to: dir.appendingPathComponent("depth").appendingPathComponent(mapName), options: .atomic)
            bytes += png.count
        }
        if let c = job.confidence {
            let png = PNGWriter.gray8(width: c.width, height: c.height, pixels: c.values)
            try png.write(to: dir.appendingPathComponent("confidence").appendingPathComponent(mapName), options: .atomic)
            bytes += png.count
        }
        try poses?.write(contentsOf: JSONLines.line(job.pose))
        try features?.write(contentsOf: job.featureLine)
        written += 1
        faceBoxes += faces
        return KeyframeResult(index: job.index, faces: faces, bytes: bytes)
    }

    /// Moves a CIImage so its extent starts at (0, 0).
    private func atOrigin(_ img: CIImage) -> CIImage {
        let e = img.extent
        return img.transformed(by: CGAffineTransform(translationX: -e.origin.x, y: -e.origin.y))
    }

    private func renderBlurredJPEG(_ job: KeyframeJob) throws -> (Data, Int) {
        var image = CIImage(cvPixelBuffer: job.pixelBuffer)
        let srcW = image.extent.width, srcH = image.extent.height
        let outRect = CGRect(x: 0, y: 0, width: job.outWidth, height: job.outHeight)
        if Int(srcW) != job.outWidth || Int(srcH) != job.outHeight {
            let scale = CGFloat(job.outHeight) / srcH
            let aspect = (CGFloat(job.outWidth) / srcW) / scale
            image = atOrigin(image.applyingFilter("CILanczosScaleTransform",
                                                  parameters: [kCIInputScaleKey: scale, kCIInputAspectRatioKey: aspect]))
        }
        image = image.cropped(to: outRect)

        // Face detection runs on the upright image; blur boxes are drawn there, then the result is
        // rotated back to the sensor orientation.
        let upright = atOrigin(image.oriented(job.orientation.cg))
        let w = Int(upright.extent.width.rounded()), h = Int(upright.extent.height.rounded())
        let request = VNDetectFaceRectanglesRequest()
        let handler = VNImageRequestHandler(ciImage: upright, orientation: .up, options: [:])
        try handler.perform([request])
        let boxes = (request.results ?? []).map { VNImageRectForNormalizedRect($0.boundingBox, w, h) }

        var composed = upright
        for box in boxes {
            let grown = box.insetBy(dx: -box.width * 0.25, dy: -box.height * 0.35).intersection(upright.extent)
            guard !grown.isNull, grown.width > 2, grown.height > 2 else { continue }
            let sigma = max(12.0, Double(max(grown.width, grown.height)) / 4)
            let blurred = upright.clampedToExtent().applyingGaussianBlur(sigma: sigma).cropped(to: grown)
            composed = blurred.composited(over: composed)
        }
        let back = boxes.isEmpty ? image : atOrigin(composed.oriented(job.orientation.inverse.cg)).cropped(to: outRect)

        let options: [CIImageRepresentationOption: Any] = [
            CIImageRepresentationOption(rawValue: kCGImageDestinationLossyCompressionQuality as String): FrameWriter.jpegQuality,
        ]
        guard let data = context.jpegRepresentation(of: back, colorSpace: colorSpace, options: options) else {
            throw NSError(domain: "Clearance.FrameWriter", code: 1,
                          userInfo: [NSLocalizedDescriptionKey: "JPEG encoding returned no data"])
        }
        return (data, boxes.count)
    }
}
