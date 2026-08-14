// swift-tools-version: 6.0
import PackageDescription

let package = Package(
    name: "SovereignNodeClient",
    platforms: [
        .iOS(.v17),
        .macOS(.v14),
    ],
    products: [
        .library(name: "SovereignNodeClient", targets: ["SovereignNodeClient"]),
    ],
    targets: [
        .target(name: "SovereignNodeClient"),
        .testTarget(name: "SovereignNodeClientTests", dependencies: ["SovereignNodeClient"]),
    ]
)
