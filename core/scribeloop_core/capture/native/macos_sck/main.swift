// Audio-only ScreenCaptureKit fallback. No video buffers or microphone capture.
// stdout protocol: SLP1 + UInt32 sample count + Float64 PTS seconds + mono Float32LE.
import Foundation
import ScreenCaptureKit
import CoreMedia
import AudioToolbox

func fail(_ message: String) -> Never {
    FileHandle.standardError.write(Data((message + "\n").utf8))
    exit(1)
}

final class AudioOutput: NSObject, SCStreamOutput, SCStreamDelegate {
    func stream(_ stream: SCStream, didStopWithError error: Error) {
        fail("Capture stopped: \(error.localizedDescription)")
    }

    func stream(_ stream: SCStream, didOutputSampleBuffer sampleBuffer: CMSampleBuffer,
                of type: SCStreamOutputType) {
        guard type == .audio, sampleBuffer.isValid, CMSampleBufferDataIsReady(sampleBuffer),
              let format = sampleBuffer.formatDescription,
              let description = CMAudioFormatDescriptionGetStreamBasicDescription(format) else { return }
        let asbd = description.pointee
        guard asbd.mFormatID == kAudioFormatLinearPCM, asbd.mSampleRate == 16000,
              asbd.mChannelsPerFrame == 1, asbd.mBitsPerChannel == 32,
              asbd.mFormatFlags & kAudioFormatFlagIsFloat != 0,
              asbd.mFormatFlags & kAudioFormatFlagIsBigEndian == 0 else {
            fail("Unexpected capture format; expected 16 kHz mono Float32LE")
        }
        let count = CMSampleBufferGetNumSamples(sampleBuffer)
        guard count > 0, count <= 16000 else { fail("Invalid audio packet size") }
        var retained: CMBlockBuffer?
        var buffers = AudioBufferList()
        let status = CMSampleBufferGetAudioBufferListWithRetainedBlockBuffer(
            sampleBuffer, bufferListSizeNeededOut: nil, bufferListOut: &buffers,
            bufferListSize: MemoryLayout<AudioBufferList>.size, blockBufferAllocator: nil,
            blockBufferMemoryAllocator: nil, flags: 0, blockBufferOut: &retained)
        guard status == noErr, buffers.mNumberBuffers == 1,
              let samples = buffers.mBuffers.mData,
              Int(buffers.mBuffers.mDataByteSize) == count * 4 else {
            fail("Cannot read captured audio buffer")
        }
        var size = UInt32(count).littleEndian
        var timestamp = sampleBuffer.presentationTimeStamp.seconds.bitPattern.littleEndian
        guard sampleBuffer.presentationTimeStamp.seconds.isFinite else { fail("Invalid audio timestamp") }
        var packet = Data("SLP1".utf8)
        withUnsafeBytes(of: &size) { packet.append(contentsOf: $0) }
        withUnsafeBytes(of: &timestamp) { packet.append(contentsOf: $0) }
        withExtendedLifetime(retained) {
            packet.append(Data(bytes: samples, count: count * 4))
        }
        do { try FileHandle.standardOutput.write(contentsOf: packet) }
        catch { fail("Audio pipe closed: \(error.localizedDescription)") }
    }
}

@main
struct Capture {
    static func main() async {
        let args = Array(CommandLine.arguments.dropFirst())
        if args == ["--version"] { print("scribeloop-sck 1; SLP1; 16000 Hz mono Float32LE"); return }
        guard args.count % 2 == 0,
              stride(from: 0, to: args.count, by: 2).allSatisfy({ args[$0] == "--bundle-id" }) else {
            fail("Usage: scribeloop-sck [--bundle-id application.bundle.id]...")
        }
        let requested = stride(from: 1, to: args.count, by: 2).map { args[$0] }
        do {
            let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: true)
            guard let display = content.displays.first else { fail("No display available for system-audio capture") }
            let filter: SCContentFilter
            if requested.isEmpty {
                filter = SCContentFilter(display: display, excludingWindows: [])
            } else {
                let applications = content.applications.filter { requested.contains($0.bundleIdentifier) }
                let found = Set(applications.map { $0.bundleIdentifier })
                guard requested.allSatisfy({ found.contains($0) }) else { fail("A selected application is not running") }
                filter = SCContentFilter(display: display, including: applications, exceptingWindows: [])
            }
            let config = SCStreamConfiguration()
            config.capturesAudio = true
            config.excludesCurrentProcessAudio = true
            config.sampleRate = 16000
            config.channelCount = 1
            config.width = 2
            config.height = 2
            config.minimumFrameInterval = CMTime(seconds: 1, preferredTimescale: 1)
            let output = AudioOutput()
            let stream = SCStream(filter: filter, configuration: config, delegate: output)
            try stream.addStreamOutput(output, type: .audio,
                sampleHandlerQueue: DispatchQueue(label: "scribeloop.audio-pipe"))
            try await stream.startCapture()
            // Keep stream and delegate alive until parent terminates the helper.
            while true {
                try await Task.sleep(nanoseconds: 1_000_000_000)
                withExtendedLifetime(stream) {}
                withExtendedLifetime(output) {}
            }
        } catch {
            fail("System-audio capture failed: \(error.localizedDescription). Check Screen Recording permission and selected source.")
        }
    }
}
