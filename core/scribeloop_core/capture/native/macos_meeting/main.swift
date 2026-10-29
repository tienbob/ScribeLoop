// Independent mic/output tracks, one host clock. Never saves audio to disk.
import Foundation
@preconcurrency import AVFoundation
import AppKit
import ScreenCaptureKit
import CoreMedia
import Darwin

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

func microphones() -> [AVCaptureDevice] {
    if #available(macOS 14, *) {
        return AVCaptureDevice.DiscoverySession(deviceTypes: [.microphone, .external],
            mediaType: .audio, position: .unspecified).devices
    }
    return AVCaptureDevice.DiscoverySession(deviceTypes: [.builtInMicrophone, .externalUnknown],
        mediaType: .audio, position: .unspecified).devices
}

func permission() -> String {
    switch AVCaptureDevice.authorizationStatus(for: .audio) {
    case .authorized: return "authorized"
    case .denied: return "denied"
    case .restricted: return "restricted"
    case .notDetermined: return "not_determined"
    @unknown default: return "unknown"
    }
}

func hostNS(_ time: CMTime) -> UInt64 {
    let scaled = CMTimeConvertScale(time, timescale: 1_000_000_000, method: .roundHalfAwayFromZero)
    guard scaled.isNumeric, scaled.epoch == 0, scaled.value >= 0 else {
        fail("Invalid native host-clock timestamp")
    }
    return UInt64(scaled.value)
}

final class PacketWriter {
    // Caller serializes both capture delegates on one queue. Nonblocking pipe
    // writes fail visibly on overflow instead of stalling capture or dropping PCM.
    init(nonblocking: Bool = true) {
        guard !nonblocking || fcntl(STDOUT_FILENO, F_SETFL, fcntl(STDOUT_FILENO, F_GETFL) | O_NONBLOCK) != -1 else {
            fail("Cannot configure bounded audio transport")
        }
        signal(SIGPIPE, SIG_IGN)
        var packet = Data("SLC2".utf8)
        var origin = hostNS(CMClockGetTime(CMClockGetHostTimeClock())).littleEndian
        withUnsafeBytes(of: &origin) { packet.append(contentsOf: $0) }
        write(packet)
    }

    func write(_ data: Data) {
        data.withUnsafeBytes { bytes in
            var offset = 0
            while offset < bytes.count {
                let count = Darwin.write(STDOUT_FILENO, bytes.baseAddress! + offset, bytes.count - offset)
                if count < 0 && errno == EINTR { continue }
                guard count > 0 else { fail("Audio transport full or closed; capture stopped") }
                offset += count
            }
        }
    }

    func audio(_ pcm: AVAudioPCMBuffer, stream: UInt8, timestamp: UInt64) {
        // A valid one-second input plus converter-buffered samples can exceed
        // one protocol packet. Fragment the output rather than reject/drop it.
        guard pcm.frameLength > 0, pcm.frameLength <= 16064, let channel = pcm.floatChannelData?[0] else {
            fail("Invalid converted audio frame")
        }
        for offset in stride(from: 0, to: Int(pcm.frameLength), by: 16000) {
            let length = min(16000, Int(pcm.frameLength) - offset)
            var packet = Data("SLP2".utf8)
            var count = UInt32(length).littleEndian
            var ns = (timestamp + UInt64(offset) * 1_000_000_000 / 16000).littleEndian
            withUnsafeBytes(of: &count) { packet.append(contentsOf: $0) }
            packet.append(contentsOf: [stream, 0, 0, 0])
            withUnsafeBytes(of: &ns) { packet.append(contentsOf: $0) }
            packet.append(Data(bytes: channel.advanced(by: offset), count: length * 4))
            write(packet)
        }
    }

    func finish() {
        var packet = Data("SLE2".utf8)
        packet.append(contentsOf: [0, 0, 0, 0, 255, 0, 0, 0])
        // Zero timestamp is reserved for completion; it carries no audio.
        packet.append(contentsOf: [UInt8](repeating: 0, count: 8))
        write(packet)
    }
}

final class Resampler {
    let input: AVAudioFormat
    let output = AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 16000,
                               channels: 1, interleaved: false)!
    let converter: AVAudioConverter
    var origin: UInt64?
    var inputSamples: UInt64 = 0
    var outputSamples: UInt64 = 0

    init(_ format: AVAudioFormat) {
        input = format
        guard format.sampleRate >= 8000, format.sampleRate <= 192000,
              format.channelCount > 0, format.channelCount <= 2,
              let converter = AVAudioConverter(from: format, to: output) else {
            fail("Unsupported native audio format")
        }
        self.converter = converter
        converter.primeMethod = .none
    }

    func convert(_ buffer: AVAudioPCMBuffer) -> AVAudioPCMBuffer {
        guard buffer.format == input, buffer.frameLength > 0,
              buffer.frameLength <= AVAudioFrameCount(input.sampleRate) else {
            fail("Capture format changed or packet oversized; restart the source")
        }
        let capacity = AVAudioFrameCount(ceil(Double(buffer.frameLength) * 16000 / input.sampleRate)) + 64
        guard let result = AVAudioPCMBuffer(pcmFormat: output, frameCapacity: capacity) else {
            fail("Cannot allocate resampling buffer")
        }
        var supplied = false
        var error: NSError?
        let status = converter.convert(to: result, error: &error) { _, state in
            if supplied { state.pointee = .noDataNow; return nil }
            supplied = true
            state.pointee = .haveData
            return buffer
        }
        guard error == nil, status != .error else { fail("Microphone resampling failed") }
        return result
    }

    func push(_ buffer: AVAudioPCMBuffer, timestamp: UInt64, writer: PacketWriter, stream: UInt8) {
        guard buffer.format == input, buffer.frameLength > 0,
              buffer.frameLength <= AVAudioFrameCount(input.sampleRate) else {
            fail("Capture format changed or packet oversized; restart the source")
        }
        if origin == nil { origin = timestamp }
        let expected = origin! + UInt64(Double(inputSamples) * 1_000_000_000 / input.sampleRate)
        guard abs(Int64(timestamp) - Int64(expected)) <= 2_000_000 else {
            fail("Audio clock discontinuity or drift exceeded 2 ms; restart the source")
        }
        inputSamples += UInt64(buffer.frameLength)
        // Bound converter input requests even when hardware delivers a full second.
        // Large buffers can exceed the converter's requested input and lose a tail.
        var offset: AVAudioFrameCount = 0
        while offset < buffer.frameLength {
            let count = min(1024, buffer.frameLength - offset)
            let chunk = AVAudioPCMBuffer(pcmFormat: input, frameCapacity: count)!
            chunk.frameLength = count
            let source = UnsafeMutableAudioBufferListPointer(buffer.mutableAudioBufferList)
            let destination = UnsafeMutableAudioBufferListPointer(chunk.mutableAudioBufferList)
            for index in 0..<source.count {
                let stride = Int(source[index].mDataByteSize) / Int(buffer.frameLength)
                guard let from = source[index].mData, let to = destination[index].mData else {
                    fail("Missing PCM channel data")
                }
                to.copyMemory(from: from.advanced(by: Int(offset) * stride),
                              byteCount: Int(count) * stride)
            }
            send(convert(chunk), writer: writer, stream: stream)
            offset += count
        }
    }

    func send(_ pcm: AVAudioPCMBuffer, writer: PacketWriter, stream: UInt8) {
        if pcm.frameLength == 0 { return }
        writer.audio(pcm, stream: stream, timestamp: origin! + outputSamples * 1_000_000_000 / 16000)
        outputSamples += UInt64(pcm.frameLength)
    }

    func finish(writer: PacketWriter, stream: UInt8) {
        let expected = UInt64(Double(inputSamples) * 16000 / input.sampleRate)
        for _ in 0..<8 where outputSamples < expected {
            let tail = AVAudioPCMBuffer(pcmFormat: output, frameCapacity: 16000)!
            var error: NSError?
            let status = converter.convert(to: tail, error: &error) { _, state in
                state.pointee = .endOfStream; return nil
            }
            guard error == nil, status != .error else { fail("Audio resampler drain failed") }
            tail.frameLength = min(tail.frameLength, AVAudioFrameCount(expected - outputSamples))
            if tail.frameLength == 0 { break }
            send(tail, writer: writer, stream: stream)
        }
        guard outputSamples == expected else { fail("Audio resampler tail incomplete") }
    }
}

struct Captured: Sendable {
    let pid: pid_t
    let start: UInt64?
    var zombie = false
}

enum PidState { case alive(Captured); case gone; case unknown }

func composedStart(_ seconds: UInt64, _ microseconds: UInt64) -> UInt64 {
    seconds * 1_000_000 + microseconds
}

func decodedPid(_ pid: pid_t, bytes: Int32, error: Int32, info: proc_bsdinfo) -> PidState {
    guard bytes == Int32(MemoryLayout<proc_bsdinfo>.size) else {
        return bytes == 0 && error == ESRCH ? .gone : .unknown
    }
    return .alive(Captured(pid: pid, start: composedStart(UInt64(info.pbi_start_tvsec),
        UInt64(info.pbi_start_tvusec)), zombie: info.pbi_status == UInt32(SZOMB)))
}

func readPid(_ pid: pid_t) -> PidState {
    var info = proc_bsdinfo()
    let size = Int32(MemoryLayout<proc_bsdinfo>.size)
    errno = 0
    let bytes = proc_pidinfo(pid, Int32(PROC_PIDTBSDINFO), 0, &info, size)
    let error = errno
    return decodedPid(pid, bytes: bytes, error: error, info: info)
}

func pidGone(_ captured: Captured, _ state: PidState) -> Bool {
    switch state {
    case .gone: return true
    case .unknown: return false
    case .alive(let current):
        return current.zombie || (captured.start != nil && current.start != captured.start)
    }
}

func allGone(_ owners: [Captured], read: (pid_t) -> PidState = readPid) -> Bool {
    !owners.isEmpty && owners.allSatisfy { pidGone($0, read($0.pid)) }
}

final class StopRequest: @unchecked Sendable {
    private let lock = NSLock()
    private var value = false
    private var dead = false
    private var callbackStarted = false
    private var owners: [Captured] = []
    func request() { lock.lock(); value = true; lock.unlock() }
    func requested() -> Bool { lock.lock(); defer { lock.unlock() }; return value }
    func capture(_ pids: [pid_t]) {
        let identities = pids.map { pid -> Captured in
            if case .alive(let identity) = readPid(pid) { return identity }
            return Captured(pid: pid, start: nil)
        }
        lock.lock(); owners = identities; lock.unlock()
    }
    func capturedGone() -> Bool {
        lock.lock(); let snapshot = owners; lock.unlock()
        return allGone(snapshot)
    }
    func markStreamDead() -> Bool {
        lock.lock(); defer { lock.unlock() }
        dead = true // Always first, including late callbacks during shutdown.
        guard !value && !callbackStarted else { return false }
        callbackStarted = true
        return true
    }
    func streamDead() -> Bool { lock.lock(); defer { lock.unlock() }; return dead }
    func sourceEnded() {
        lock.lock(); defer { lock.unlock() }
        guard !value else { return }
        FileHandle.standardError.write(Data("Source ended: selected_app_exited\n".utf8))
        value = true
    }
    func failIfActive() {
        lock.lock(); defer { lock.unlock() }
        if !value { fail("Meeting output stopped; check the selected app and capture permission") }
    }
    func waitForDeadStreamOwner() async {
        for _ in 0..<15 {
            if requested() { return }
            if capturedGone() { sourceEnded(); return }
            try? await Task.sleep(nanoseconds: 100_000_000)
        }
        if capturedGone() { sourceEnded() } else { failIfActive() }
    }
}

final class CaptureOutput: NSObject, SCStreamOutput, SCStreamDelegate, AVCaptureAudioDataOutputSampleBufferDelegate {
    let writer: PacketWriter
    let micSession: AVCaptureSession
    let stop: StopRequest
    var converters: [UInt8: Resampler] = [:]

    init(writer: PacketWriter, micSession: AVCaptureSession, stop: StopRequest) {
        self.writer = writer
        self.micSession = micSession
        self.stop = stop
    }

    func emit(_ sample: CMSampleBuffer, stream: UInt8, clock: CMClock?) {
        guard sample.isValid, CMSampleBufferDataIsReady(sample),
              let description = sample.formatDescription, let clock else {
            fail("Missing audio buffer or synchronization clock")
        }
        let frames = CMSampleBufferGetNumSamples(sample)
        guard let basic = CMAudioFormatDescriptionGetStreamBasicDescription(description),
              basic.pointee.mFormatID == kAudioFormatLinearPCM,
              let format = AVAudioFormat(streamDescription: basic) else {
            fail("Unsupported native PCM format; mono/stereo input is required")
        }
        guard format.sampleRate >= 8000, format.sampleRate <= 192000,
              format.channelCount <= 2, frames > 0, frames <= Int(format.sampleRate),
              let pcm = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(frames)) else {
            fail("Unsupported native PCM buffer")
        }
        pcm.frameLength = AVAudioFrameCount(frames)
        guard CMSampleBufferCopyPCMDataIntoAudioBufferList(sample, at: 0,
            frameCount: Int32(frames), into: pcm.mutableAudioBufferList) == noErr else {
            fail("Cannot copy native PCM buffer")
        }
        let converter = converters[stream] ?? Resampler(format)
        converters[stream] = converter
        let host = CMSyncConvertTime(sample.presentationTimeStamp, from: clock,
                                     to: CMClockGetHostTimeClock())
        converter.push(pcm, timestamp: hostNS(host), writer: writer, stream: stream)
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sample: CMSampleBuffer,
                of type: SCStreamOutputType) {
        if type == .audio { emit(sample, stream: 0, clock: stream.synchronizationClock) }
    }

    func captureOutput(_ output: AVCaptureOutput, didOutput sample: CMSampleBuffer,
                       from connection: AVCaptureConnection) {
        emit(sample, stream: 1, clock: micSession.synchronizationClock)
    }

    func stream(_ stream: SCStream, didStopWithError error: Error) {
        guard stop.markStreamDead() else { return }
        let state = stop
        Task.detached { await state.waitForDeadStreamOwner() }
    }
}

@main
struct MeetingCapture {
    static func main() async {
        let args = Array(CommandLine.arguments.dropFirst())
        if args == ["--version"] { print("scribeloop-meeting 1; SLC2/SLP2; host ns; 16000 Hz mono Float32LE"); return }
        if args == ["--check-capture-access"] {
            // Read-only permission/source check: no SCStream, AVCaptureSession,
            // PacketWriter, microphone request or audio device is opened.
            do {
                let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
                let result: [String:Any] = ["microphone":permission(),"output_access":true,"display_available":!content.displays.isEmpty]
                print(String(data:try JSONSerialization.data(withJSONObject:result),encoding:.utf8)!)
            } catch {
                let native = error as NSError
                let domain = native.domain == SCStreamErrorDomain ? "screen" : "other"
                fail("Native capture startup failed: output_enumeration:\(domain):\(native.code)")
            }
            return
        }
        if args == ["--devices"] {
            let defaultID = AVCaptureDevice.default(for: .audio)?.uniqueID
            let data: [String: Any] = ["version": 1, "permission": permission(),
                "microphones": microphones().map {
                    ["id": $0.uniqueID, "name": $0.localizedName, "is_default": $0.uniqueID == defaultID] as [String: Any]
                }, "running_bundle_ids": Array(Set(NSWorkspace.shared.runningApplications.compactMap { $0.bundleIdentifier })).sorted()]
            do { print(String(data: try JSONSerialization.data(withJSONObject: data, options: [.sortedKeys]), encoding: .utf8)!) }
            catch { fail("Device enumeration failed") }
            return
        }
        if args == ["--request-microphone-access"] {
            if permission() == "not_determined" {
                _ = await withCheckedContinuation { (continuation: CheckedContinuation<Bool, Never>) in
                    AVCaptureDevice.requestAccess(for: .audio) { granted in
                        continuation.resume(returning: granted)
                    }
                }
            }
            print(permission())
            return
        }
        if args == ["--self-test"] { selfTest(); return }
        if (args.count == 2 || (args.count == 3 && args[2] == "--stream-dead")) &&
                args[0] == "--self-test-liveness-pid", let pid = Int32(args[1]), pid > 0 {
            let stop = StopRequest(); stop.capture([pid])
            guard !stop.capturedGone() else { fail("Liveness fixture must start alive") }
            var goneTicks = 0
            for _ in 0..<100 {
                try? await Task.sleep(nanoseconds: 100_000_000)
                goneTicks = stop.capturedGone() ? goneTicks + 1 : 0
                if args.count == 3 && goneTicks == 1 && stop.markStreamDead() {
                    Task.detached { await stop.waitForDeadStreamOwner() }
                }
                if stop.requested() { break }
                if goneTicks >= 20 { stop.sourceEnded(); break }
            }
            guard stop.requested() else { fail("Liveness fixture timed out") }
            let writer = PacketWriter(nonblocking: false); writer.finish()
            return
        }
        if args.count == 3 && args[0] == "--self-test" && args[1] == "--seconds" {
            guard let seconds = Int(args[2]), seconds >= 30 && seconds <= 10800 else {
                fail("Generated duration must be 30 to 10800 seconds")
            }
            selfTest(seconds: seconds); return
        }
        guard args.contains("--consent-confirmed") else { fail("Explicit capture consent is required") }
        var microphone: String?
        var bundles: [String] = []
        var scope: String?
        var micOnly = false
        var i = 0
        while i < args.count {
            switch args[i] {
            case "--consent-confirmed": i += 1
            case "--microphone-only": micOnly = true; i += 1
            case "--microphone-id", "--bundle-id", "--scope":
                guard i + 1 < args.count, !args[i+1].isEmpty, !args[i+1].hasPrefix("--") else { fail("Missing capture selection") }
                if args[i] == "--microphone-id" {
                    guard microphone == nil else { fail("Duplicate microphone selection") }
                    microphone = args[i+1]
                } else if args[i] == "--scope" {
                    guard scope == nil else { fail("Duplicate capture scope") }
                    scope = args[i+1]
                } else { bundles.append(args[i+1]) }
                i += 2
            default: fail("Unknown meeting-capture argument")
            }
        }
        guard let microphone else { fail("Select a microphone explicitly") }
        guard (micOnly && scope == nil && bundles.isEmpty) ||
              (!micOnly && ((scope == "application" && !bundles.isEmpty) ||
                           (scope == "system" && bundles.isEmpty))) else { fail("Explicit output scope required") }
        // Enumeration/selection never requests access. First-run permission UX is M4.
        guard permission() == "authorized" else { fail("Microphone access is not authorized; recording was not started") }
        let devices = microphones()
        guard let device = microphone == "default" ? AVCaptureDevice.default(for: .audio) :
                devices.first(where: { $0.uniqueID == microphone }) else { fail("Selected microphone is unavailable") }
        var captureStage = "microphone_setup"
        do {
            let session = AVCaptureSession()
            session.beginConfiguration()
            let input = try AVCaptureDeviceInput(device: device)
            let audio = AVCaptureAudioDataOutput()
            guard session.canAddInput(input), session.canAddOutput(audio) else { fail("Cannot configure microphone") }
            session.addInput(input)
            session.addOutput(audio)
            session.commitConfiguration()
            let writer = PacketWriter()
            let stop = StopRequest()
            let output = CaptureOutput(writer: writer, micSession: session, stop: stop)
            let queue = DispatchQueue(label: "scribeloop.meeting-audio")
            DispatchQueue.global().async {
                var command = Data()
                while command.count < 5 {
                    guard let part = try? FileHandle.standardInput.read(upToCount: 5 - command.count),
                          !part.isEmpty else { fail("Capture controller disconnected") }
                    command.append(part)
                }
                guard command == Data("STOP\n".utf8) else { fail("Invalid capture stop command") }
                stop.request()
            }
            audio.setSampleBufferDelegate(output, queue: queue)
            let runtime = NotificationCenter.default.addObserver(forName: AVCaptureSession.runtimeErrorNotification,
                object: session, queue: nil) { _ in fail("Microphone capture failed; restart the source") }
            let interruption = NotificationCenter.default.addObserver(forName: AVCaptureSession.wasInterruptedNotification,
                object: session, queue: nil) { _ in fail("Microphone capture interrupted") }
            var loopback: SCStream?
            if !micOnly {
                captureStage = "output_enumeration"
                let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
                guard let display = content.displays.first else { fail("No display for output capture") }
                let filter: SCContentFilter
                if scope == "application" {
                    let apps = content.applications.filter { bundles.contains($0.bundleIdentifier) }
                    guard bundles.allSatisfy({ id in apps.contains(where: { $0.bundleIdentifier == id }) }) else {
                        fail("A selected app is not running; scope was not broadened")
                    }
                    stop.capture(apps.map { $0.processID })
                    filter = SCContentFilter(display: display, including: apps, exceptingWindows: [])
                } else { filter = SCContentFilter(display: display, excludingWindows: []) }
                let config = SCStreamConfiguration()
                config.capturesAudio = true; config.excludesCurrentProcessAudio = true
                config.sampleRate = 16000; config.channelCount = 1
                config.width = 2; config.height = 2
                config.minimumFrameInterval = CMTime(seconds: 1, preferredTimescale: 1)
                let stream = SCStream(filter: filter, configuration: config, delegate: output)
                try stream.addStreamOutput(output, type: .audio, sampleHandlerQueue: queue)
                captureStage = "output_start"
                try await stream.startCapture()
                loopback = stream
            }
            // Keep blocking microphone startup away from the main actor.
            captureStage = "microphone_start"
            await withCheckedContinuation { continuation in
                DispatchQueue.global().async { session.startRunning(); continuation.resume() }
            }
            var goneTicks = 0
            while !stop.requested() {
                try await Task.sleep(nanoseconds: 100_000_000)
                guard device.isConnected else { fail("Selected microphone disconnected") }
                if microphone == "default" && AVCaptureDevice.default(for: .audio)?.uniqueID != device.uniqueID {
                    fail("Default microphone changed; select and restart the source")
                }
                if scope == "application" {
                    goneTicks = stop.capturedGone() ? goneTicks + 1 : 0
                    if goneTicks >= 20 { stop.sourceEnded() }
                }
                withExtendedLifetime((session, output, loopback, runtime, interruption)) {}
            }
            if let loopback, !stop.streamDead() { try? await loopback.stopCapture() }
            await withCheckedContinuation { continuation in
                DispatchQueue.global().async { session.stopRunning(); continuation.resume() }
            }
            queue.sync {
                for (track, converter) in output.converters { converter.finish(writer: writer, stream: track) }
                writer.finish()
            }
        } catch {
            let native = error as NSError
            let domain = native.domain == SCStreamErrorDomain ? "screen" : native.domain == AVFoundationErrorDomain ? "microphone" : "other"
            // Fixed domains/stages and numeric codes only; no paths, app content
            // or arbitrary localized OS/provider error strings cross the pipe.
            fail("Native capture startup failed: \(captureStage):\(domain):\(native.code)")
        }
    }

    static func selfTest(seconds: Int = 300) {
        let original = Captured(pid: 42, start: composedStart(123, 456))
        precondition(original.start == 123_000_456)
        precondition(!pidGone(original, .alive(original)))
        precondition(!pidGone(original, .unknown))
        precondition(pidGone(original, decodedPid(42, bytes: 0, error: ESRCH, info: proc_bsdinfo())))
        for (bytes, error) in [(Int32(0), EPERM), (Int32(1), ESRCH), (Int32(-1), EINVAL)] {
            precondition(!pidGone(original, decodedPid(42, bytes: bytes, error: error, info: proc_bsdinfo())))
        }
        var info = proc_bsdinfo(); info.pbi_start_tvsec = 123; info.pbi_start_tvusec = 456
        info.pbi_status = UInt32(SSTOP) // Status is an enum, not a bit mask.
        precondition(!pidGone(original, decodedPid(42, bytes: Int32(MemoryLayout<proc_bsdinfo>.size), error: 0, info: info)))
        precondition(pidGone(original, .gone))
        precondition(pidGone(original, .alive(Captured(pid: 42, start: 123_000_457))))
        precondition(pidGone(original, .alive(Captured(pid: 42, start: original.start, zombie: true))))
        precondition(!allGone([]))
        precondition(!allGone([original], read: { _ in .unknown }))
        precondition(!allGone([original, Captured(pid: 43, start: 2)], read: { $0 == 42 ? .gone : .unknown }))
        precondition(allGone([original], read: { _ in .gone }))
        // A replacement process cannot restore the original SCK capture filter.
        precondition(allGone([original], read: { _ in .alive(Captured(pid: 42, start: 999)) }))
        let dead = StopRequest()
        precondition(!dead.streamDead()); precondition(dead.markStreamDead())
        precondition(dead.streamDead()); precondition(!dead.markStreamDead())
        let manual = StopRequest(); manual.request()
        precondition(!manual.markStreamDead()); precondition(manual.streamDead())
        // Bounded accelerated generated PCM, no device/capture/permission use.
        let writer = PacketWriter(nonblocking: false)
        let base = hostNS(CMClockGetTime(CMClockGetHostTimeClock())) + 100_000_000
        let formats = [AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 48000,
                                     channels: 2, interleaved: false)!,
                       AVAudioFormat(commonFormat: .pcmFormatFloat32, sampleRate: 44100,
                                     channels: 1, interleaved: false)!]
        let converters = formats.map { Resampler($0) }
        var positions: [Int] = [0, 0]
        // Ten full-second input buffers plus irregular small chunks for the remainder.
        for packet in 0..<(10 + (seconds - 10) * 10) {
            for track in 0..<2 {
                let format = formats[track]
                let count = Int(format.sampleRate) / (packet < 10 ? 1 : 10)
                // Exercise irregular hardware-like chunks, not only exact 100 ms blocks.
                var start = 0
                while start < count {
                    let length = packet < 10 ? count - start : min(track == 0 ? 4096 : 1024, count - start)
                    let pcm = AVAudioPCMBuffer(pcmFormat: format, frameCapacity: AVAudioFrameCount(length))!
                    pcm.frameLength = AVAudioFrameCount(length)
                    for channel in 0..<Int(format.channelCount) {
                        for sample in 0..<length {
                            pcm.floatChannelData![channel][sample] = Float(0.1 * sin(2 * Double.pi * 440 * Double(positions[track] + start + sample) / format.sampleRate))
                        }
                    }
                    let timestamp = base + UInt64(Double(positions[track] + start) * 1_000_000_000 / format.sampleRate)
                                    + UInt64(track) * 150_000_000
                    converters[track].push(pcm, timestamp: timestamp, writer: writer, stream: UInt8(track))
                    start += length
                }
                positions[track] += count
            }
        }
        for track in 0..<2 { converters[track].finish(writer: writer, stream: UInt8(track)) }
        writer.finish()
    }
}
