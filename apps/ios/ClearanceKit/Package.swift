// swift-tools-version:5.9
// Pure-Swift core of the Clearance iOS app (Foundation, simd, Compression, CryptoKit only;
// no ARKit/RealityKit/UIKit). Unit-tested with `swift test` on a Mac and linked into the app
// target through XcodeGen.
import PackageDescription

let package = Package(
    name: "ClearanceKit",
    platforms: [.iOS(.v17), .macOS(.v14)],
    products: [
        .library(name: "ClearanceKit", targets: ["ClearanceKit"]),
    ],
    targets: [
        .target(name: "ClearanceKit"),
        .testTarget(name: "ClearanceKitTests", dependencies: ["ClearanceKit"]),
    ]
)
