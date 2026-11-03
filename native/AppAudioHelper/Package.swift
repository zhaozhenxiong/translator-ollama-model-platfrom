// swift-tools-version: 5.9
import PackageDescription

let package = Package(
    name: "AppAudioHelper",
    platforms: [.macOS(.v13)],
    products: [
        .executable(name: "app-audio-helper", targets: ["AppAudioHelper"])
    ],
    targets: [
        .executableTarget(
            name: "AppAudioHelper",
            swiftSettings: [.unsafeFlags(["-parse-as-library"])]
        )
    ]
)
