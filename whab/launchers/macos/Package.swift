// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "WHABLauncher",
    platforms: [.macOS(.v13)],
    products: [.executable(name: "WHABLauncher", targets: ["WHABLauncher"])],
    targets: [
        .executableTarget(name: "WHABLauncher"),
        .testTarget(name: "WHABLauncherTests", dependencies: ["WHABLauncher"],
                    path: "tests/WHABLauncherTests"),
    ]
)
