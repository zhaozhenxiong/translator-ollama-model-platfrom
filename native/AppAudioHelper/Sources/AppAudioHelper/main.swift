import AVFoundation
import AppKit
import CoreGraphics
import CoreMedia
import Darwin
import Foundation
import ScreenCaptureKit

private let outputSampleRate = 16_000

private enum HelperError: LocalizedError {
    case invalidArguments(String)
    case permissionDenied
    case applicationNotFound(String)
    case ambiguousApplication(String)
    case displayUnavailable

    var errorDescription: String? {
        switch self {
        case .invalidArguments(let message):
            return message
        case .permissionDenied:
            return "Screen Recording permission is required. Grant it in System Settings > Privacy & Security > Screen & System Audio Recording."
        case .applicationNotFound(let bundleID):
            return "The application is no longer running: \(bundleID)"
        case .ambiguousApplication(let bundleID):
            return "More than one process matches \(bundleID); pass --pid."
        case .displayUnavailable:
            return "No capturable display is available."
        }
    }
}

private struct ApplicationRecord: Encodable {
    let name: String
    let bundle_id: String
    let process_id: Int32
}

private struct PermissionRecord: Encodable {
    let screen_recording_granted: Bool
    let requested: Bool
}

private func writeStandardError(_ message: String) {
    guard let data = "\(message)\n".data(using: .utf8) else { return }
    try? FileHandle.standardError.write(contentsOf: data)
}

private func writeJSON<T: Encodable>(_ value: T) throws {
    let encoder = JSONEncoder()
    encoder.outputFormatting = [.sortedKeys]
    var data = try encoder.encode(value)
    data.append(0x0A)
    try FileHandle.standardOutput.write(contentsOf: data)
}

private func shareableContent() async throws -> SCShareableContent {
    try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
}

private func listApplications() async throws {
    guard CGPreflightScreenCaptureAccess() else {
        throw HelperError.permissionDenied
    }
    let content = try await shareableContent()
    let applications = content.applications.compactMap { application -> ApplicationRecord? in
        let name = application.applicationName.trimmingCharacters(in: .whitespacesAndNewlines)
        guard !name.isEmpty, !application.bundleIdentifier.isEmpty else { return nil }
        return ApplicationRecord(
            name: name,
            bundle_id: application.bundleIdentifier,
            process_id: application.processID
        )
    }
    .sorted {
        $0.name.localizedCaseInsensitiveCompare($1.name) == .orderedAscending
            || ($0.name == $1.name && $0.process_id < $1.process_id)
    }
    try writeJSON(applications)
}

private func checkPermissions(request: Bool) throws {
    let granted = request ? CGRequestScreenCaptureAccess() : CGPreflightScreenCaptureAccess()
    try writeJSON(PermissionRecord(screen_recording_granted: granted, requested: request))
}

private struct CaptureOptions {
    let bundleID: String
    let processID: Int32?

    static func parse(_ arguments: [String]) throws -> CaptureOptions {
        var bundleID: String?
        var processID: Int32?
        var index = 0
        while index < arguments.count {
            switch arguments[index] {
            case "--bundle-id":
                guard index + 1 < arguments.count else {
                    throw HelperError.invalidArguments("--bundle-id requires a value")
                }
                bundleID = arguments[index + 1]
                index += 2
            case "--pid":
                guard index + 1 < arguments.count, let value = Int32(arguments[index + 1]) else {
                    throw HelperError.invalidArguments("--pid requires a valid process ID")
                }
                processID = value
                index += 2
            default:
                throw HelperError.invalidArguments("Unknown capture argument: \(arguments[index])")
            }
        }
        guard let bundleID, !bundleID.isEmpty else {
            throw HelperError.invalidArguments("capture requires --bundle-id")
        }
        return CaptureOptions(bundleID: bundleID, processID: processID)
    }
}

private final class CaptureHandler: NSObject, SCStreamDelegate, SCStreamOutput {
    private var receivedFirstAudioBuffer = false

    func stream(
        _ stream: SCStream,
        didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
        of type: SCStreamOutputType
    ) {
        guard type == .audio else { return }
        guard sampleBuffer.isValid, sampleBuffer.numSamples > 0 else { return }
        guard let description = CMSampleBufferGetFormatDescription(sampleBuffer),
              let asbd = CMAudioFormatDescriptionGetStreamBasicDescription(description) else {
            fail("Audio buffer has no stream description")
            return
        }
        let flags = asbd.pointee.mFormatFlags
        guard asbd.pointee.mFormatID == kAudioFormatLinearPCM,
              flags & kAudioFormatFlagIsFloat != 0,
              flags & kAudioFormatFlagIsBigEndian == 0,
              asbd.pointee.mBitsPerChannel == 32,
              asbd.pointee.mChannelsPerFrame == 1,
              asbd.pointee.mSampleRate == Double(outputSampleRate) else {
            fail("Unsupported ScreenCaptureKit audio format")
            return
        }

        var audioBufferList = AudioBufferList()
        var blockBuffer: CMBlockBuffer?
        let status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer,
            bufferListSizeNeededOut: nil,
            bufferListOut: &audioBufferList,
            bufferListSize: MemoryLayout<AudioBufferList>.size,
            blockBufferAllocator: nil,
            blockBufferMemoryAllocator: nil,
            flags: kCMSampleBufferFlag_AudioBufferList_Assure16ByteAlignment,
            blockBufferOut: &blockBuffer
        )
        guard status == noErr, let baseAddress = audioBufferList.mBuffers.mData else {
            fail("Could not read audio buffer (status \(status))")
            return
        }

        let sampleCount = Int(audioBufferList.mBuffers.mDataByteSize) / MemoryLayout<Float>.size
        let input = baseAddress.assumingMemoryBound(to: Float.self)
        let output = LittleEndianPCMConverter.convertFloat32ToInt16(
            UnsafeBufferPointer(start: input, count: sampleCount)
        )

        do {
            try FileHandle.standardOutput.write(contentsOf: output)
        } catch {
            fail("Could not write PCM output: \(error.localizedDescription)")
            return
        }
        if !receivedFirstAudioBuffer {
            receivedFirstAudioBuffer = true
            writeStandardError("First audio buffer received: \(outputSampleRate) Hz, mono, s16le")
        }
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        fail("ScreenCaptureKit stopped: \(error.localizedDescription)")
    }

    private func fail(_ message: String) {
        writeStandardError(message)
        exit(2)
    }
}

private final class CaptureProcess: @unchecked Sendable {
    private let lock = NSLock()
    private var stream: SCStream?
    private var handler: CaptureHandler?
    private var signalSources: [DispatchSourceSignal] = []
    private var stopping = false

    func retain(stream: SCStream, handler: CaptureHandler) {
        self.stream = stream
        self.handler = handler
    }

    func installSignalHandlers() {
        for signalNumber in [SIGTERM, SIGINT] {
            signal(signalNumber, SIG_IGN)
            let source = DispatchSource.makeSignalSource(signal: signalNumber, queue: .main)
            source.setEventHandler { [weak self] in
                self?.stop()
            }
            source.resume()
            signalSources.append(source)
        }
    }

    func targetIsRunning(_ processID: Int32) -> Bool {
        guard let application = NSRunningApplication(processIdentifier: processID) else {
            return false
        }
        return !application.isTerminated
    }

    func stop() {
        lock.lock()
        guard !stopping else {
            lock.unlock()
            return
        }
        stopping = true
        let stream = self.stream
        lock.unlock()
        Task {
            if let stream {
                try? await stream.stopCapture()
            }
            exit(0)
        }
    }
}

private func runCapture(options: CaptureOptions) async throws {
    guard CGPreflightScreenCaptureAccess() else {
        throw HelperError.permissionDenied
    }
    let content = try await shareableContent()
    let matches = content.applications.filter {
        $0.bundleIdentifier == options.bundleID
            && (options.processID == nil || $0.processID == options.processID)
    }
    guard let application = matches.first else {
        throw HelperError.applicationNotFound(options.bundleID)
    }
    guard matches.count == 1 else {
        throw HelperError.ambiguousApplication(options.bundleID)
    }
    guard let display = content.displays.first else {
        throw HelperError.displayUnavailable
    }

    let filter = SCContentFilter(
        display: display,
        including: [application],
        exceptingWindows: []
    )
    let configuration = SCStreamConfiguration()
    configuration.capturesAudio = true
    configuration.sampleRate = outputSampleRate
    configuration.channelCount = 1
    configuration.excludesCurrentProcessAudio = true
    // ScreenCaptureKit still needs a video configuration for audio-only capture.
    configuration.width = 2
    configuration.height = 2
    configuration.minimumFrameInterval = CMTime(value: 1, timescale: 1)
    configuration.queueDepth = 5
    configuration.showsCursor = false

    let handler = CaptureHandler()
    let stream = SCStream(filter: filter, configuration: configuration, delegate: handler)
    let process = CaptureProcess()
    process.retain(stream: stream, handler: handler)
    try stream.addStreamOutput(
        handler,
        type: .audio,
        sampleHandlerQueue: DispatchQueue(label: "local-live-translator.audio", qos: .userInteractive)
    )
    try stream.addStreamOutput(
        handler,
        type: .screen,
        sampleHandlerQueue: DispatchQueue(label: "local-live-translator.discarded-video", qos: .background)
    )
    process.installSignalHandlers()
    try await stream.startCapture()
    writeStandardError("Capture started for \(application.applicationName) (\(application.bundleIdentifier))")

    while process.targetIsRunning(application.processID) {
        try await Task.sleep(nanoseconds: 1_000_000_000)
    }
    throw HelperError.applicationNotFound(options.bundleID)
}

@main
private struct AppAudioHelper {
    static func main() async {
        do {
            let arguments = Array(CommandLine.arguments.dropFirst())
            guard let command = arguments.first else {
                throw HelperError.invalidArguments(
                    "Usage: app-audio-helper self-test [--json] | list-apps [--json] | permissions [--json] [--request] | capture --bundle-id <id> [--pid <pid>]"
                )
            }
            switch command {
            case "self-test":
                guard arguments.dropFirst().allSatisfy({ $0 == "--json" }) else {
                    throw HelperError.invalidArguments("self-test accepts only --json")
                }
                try writeJSON(runPCMConverterSelfTests())
            case "list-apps":
                guard arguments.dropFirst().allSatisfy({ $0 == "--json" }) else {
                    throw HelperError.invalidArguments("list-apps accepts only --json")
                }
                try await listApplications()
            case "permissions":
                let rest = Array(arguments.dropFirst())
                guard rest.allSatisfy({ $0 == "--request" || $0 == "--json" }) else {
                    throw HelperError.invalidArguments("permissions accepts only --request and --json")
                }
                try checkPermissions(request: rest.contains("--request"))
            case "capture":
                let options = try CaptureOptions.parse(Array(arguments.dropFirst()))
                try await runCapture(options: options)
            default:
                throw HelperError.invalidArguments("Unknown command: \(command)")
            }
        } catch {
            writeStandardError(error.localizedDescription)
            exit(1)
        }
    }
}
