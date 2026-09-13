// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "FinalCutBridge",
    platforms: [.macOS(.v13)],
    targets: [
        .executableTarget(name: "FinalCutBridge"),
        .testTarget(name: "FinalCutBridgeTests", dependencies: ["FinalCutBridge"]),
    ]
)
