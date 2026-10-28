import ARKit
import ClearanceKit
import CoreVideo
import Foundation
import ImageIO
import RealityKit
import UIKit

enum DeviceInfo {
    /// Hardware identifier such as "iPhone14,5".
    static var modelIdentifier: String {
        var info = utsname()
        uname(&info)
        let mirror = Mirror(reflecting: info.machine)
        let id = mirror.children.reduce(into: "") { acc, el in
            if let v = el.value as? Int8, v != 0 { acc.append(Character(UnicodeScalar(UInt8(bitPattern: v)))) }
        }
        return id.isEmpty ? UIDevice.current.model : id
    }

    static var osVersion: String { "iOS \(UIDevice.current.systemVersion)" }

    /// T1 when ARKit offers LiDAR scene depth, otherwise T2 (PLAN 7.1 tiers).
    static var tier: CaptureTier {
        ARWorldTrackingConfiguration.supportsFrameSemantics(.sceneDepth) ? .lidar : .vioMono
    }

    static var appVersion: String {
        let v = Bundle.main.object(forInfoDictionaryKey: "CFBundleShortVersionString") as? String ?? "0"
        let b = Bundle.main.object(forInfoDictionaryKey: "CFBundleVersion") as? String ?? "0"
        return "\(v) (\(b))"
    }
}

enum MarkerReference {
    static let name = "clearance_marker_tag36h11_id0"
    static let resource = "tag36h11_id0"

    /// The bundled tag image (cropped to the black square) as an ARKit reference image whose
    /// physical width is the measured edge of the printed black square.
    static func make(physicalWidth: Double) -> ARReferenceImage? {
        guard let url = Bundle.main.url(forResource: resource, withExtension: "png"),
              let src = CGImageSourceCreateWithURL(url as CFURL, nil),
              let cg = CGImageSourceCreateImageAtIndex(src, 0, nil) else { return nil }
        let ref = ARReferenceImage(cg, orientation: .up, physicalWidth: CGFloat(physicalWidth))
        ref.name = name
        return ref
    }

    static func validate(_ ref: ARReferenceImage, completion: @escaping @MainActor (String?) -> Void) {
        ref.validate { error in
            let text = error.map { "Marker image check: \($0.localizedDescription)" }
            Task { @MainActor in completion(text) }
        }
    }
}

enum PixelBufferTools {
    /// Laplacian-variance sharpness of the luma plane of ARKit's capturedImage (420f / 420v).
    static func sharpness(_ pb: CVPixelBuffer, targetWidth: Int = 480) -> Double {
        CVPixelBufferLockBaseAddress(pb, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(pb, .readOnly) }
        let planar = CVPixelBufferIsPlanar(pb)
        guard let base = planar ? CVPixelBufferGetBaseAddressOfPlane(pb, 0) : CVPixelBufferGetBaseAddress(pb) else { return 0 }
        let w = planar ? CVPixelBufferGetWidthOfPlane(pb, 0) : CVPixelBufferGetWidth(pb)
        let h = planar ? CVPixelBufferGetHeightOfPlane(pb, 0) : CVPixelBufferGetHeight(pb)
        let bpr = planar ? CVPixelBufferGetBytesPerRowOfPlane(pb, 0) : CVPixelBufferGetBytesPerRow(pb)
        let small = BlurMetric.downsample(base: UnsafeRawPointer(base), width: w, height: h, bytesPerRow: bpr,
                                          factor: BlurMetric.factor(width: w, targetWidth: targetWidth))
        return BlurMetric.laplacianVariance(small.pixels, width: small.width, height: small.height)
    }

    /// Copies a single-plane buffer of `T` (Float32 depth or UInt8 confidence) into an array.
    static func copy<T>(_ pb: CVPixelBuffer, as type: T.Type) -> (values: [T], width: Int, height: Int)? {
        CVPixelBufferLockBaseAddress(pb, .readOnly)
        defer { CVPixelBufferUnlockBaseAddress(pb, .readOnly) }
        guard let base = CVPixelBufferGetBaseAddress(pb) else { return nil }
        let w = CVPixelBufferGetWidth(pb), h = CVPixelBufferGetHeight(pb), bpr = CVPixelBufferGetBytesPerRow(pb)
        guard bpr >= w * MemoryLayout<T>.stride else { return nil }
        var out: [T] = []
        out.reserveCapacity(w * h)
        for y in 0..<h {
            let row = (base + y * bpr).assumingMemoryBound(to: T.self)
            out.append(contentsOf: UnsafeBufferPointer(start: row, count: w))
        }
        return (out, w, h)
    }
}

/// Conversions between ClearanceKit mesh arrays and RealityKit (RealityKit types are main-actor bound).
@MainActor
enum MeshFactory {
    static func resource(_ m: MeshData, name: String = "mesh") -> MeshResource? {
        guard !m.indices.isEmpty else { return nil }
        var d = MeshDescriptor(name: name)
        d.positions = MeshBuffers.Positions(m.positions)
        d.normals = MeshBuffers.Normals(m.normals)
        d.primitives = .triangles(m.indices)
        return try? MeshResource.generate(from: [d])
    }

    static func entity(_ m: MeshData, material: RealityKit.Material, name: String = "mesh") -> ModelEntity {
        guard let r = resource(m, name: name) else { return ModelEntity() }
        let e = ModelEntity(mesh: r, materials: [material])
        e.name = name
        return e
    }

    static func unlit(_ color: UIColor, opacity: Float = 1) -> UnlitMaterial {
        var m = UnlitMaterial(color: color)
        if opacity < 0.999 { m.blending = .transparent(opacity: .init(scale: opacity, texture: nil)) }
        return m
    }

    static func solid(_ color: UIColor, roughness: Float = 0.6) -> SimpleMaterial {
        SimpleMaterial(color: color, roughness: .float(roughness), isMetallic: false)
    }
}

extension UprightOrientation {
    var cg: CGImagePropertyOrientation {
        switch self {
        case .up: return .up
        case .down: return .down
        case .left: return .left
        case .right: return .right
        }
    }
}
