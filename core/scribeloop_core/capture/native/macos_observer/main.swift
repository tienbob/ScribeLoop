// Metadata only: never creates an audio device, tap, stream, or microphone input.
import Foundation
import CoreAudio

struct ObserverError: Error { let status: OSStatus }

func address(_ selector: AudioObjectPropertySelector) -> AudioObjectPropertyAddress {
    AudioObjectPropertyAddress(mSelector: selector, mScope: kAudioObjectPropertyScopeGlobal,
                               mElement: kAudioObjectPropertyElementMain)
}

func scalar(_ object: AudioObjectID, _ selector: AudioObjectPropertySelector) -> UInt32? {
    var property = address(selector)
    var value: UInt32 = 0
    var size = UInt32(MemoryLayout<UInt32>.size)
    guard AudioObjectGetPropertyData(object, &property, 0, nil, &size, &value) == noErr,
          size == MemoryLayout<UInt32>.size else { return nil }
    return value
}

func bundleID(_ object: AudioObjectID) -> String? {
    var property = address(kAudioProcessPropertyBundleID)
    var value: Unmanaged<CFString>?
    var size = UInt32(MemoryLayout<Unmanaged<CFString>?>.size)
    guard AudioObjectGetPropertyData(object, &property, 0, nil, &size, &value) == noErr,
          let value else { return nil }
    // AudioHardware.h: the caller owns the returned CFObject.
    return value.takeRetainedValue() as String
}

func snapshot() throws -> [String: Any] {
    let system = AudioObjectID(kAudioObjectSystemObject)
    var property = address(kAudioHardwarePropertyProcessObjectList)
    var size: UInt32 = 0
    var status = AudioObjectGetPropertyDataSize(system, &property, 0, nil, &size)
    guard status == noErr else { throw ObserverError(status: status) }
    guard size % UInt32(MemoryLayout<AudioObjectID>.size) == 0, size <= 1_048_576 else {
        throw ObserverError(status: kAudioHardwareUnspecifiedError)
    }
    var objects = [AudioObjectID](repeating: 0,
        count: Int(size) / MemoryLayout<AudioObjectID>.size)
    if size > 0 {
        status = objects.withUnsafeMutableBytes {
            AudioObjectGetPropertyData(system, &property, 0, nil, &size, $0.baseAddress!)
        }
        guard status == noErr else { throw ObserverError(status: status) }
    }
    var complete = true
    var processes: [[String: Any]] = []
    for object in objects.prefix(Int(size) / MemoryLayout<AudioObjectID>.size) {
        guard let pid = scalar(object, kAudioProcessPropertyPID), pid > 0,
              let bundle = bundleID(object) else {
            // Process exit/races/unknown identities are not evidence of inactivity.
            complete = false
            continue
        }
        // HAL also lists unbundled clients. A successfully read empty identity
        // cannot match our app registry and does not make other apps unknown.
        if bundle.isEmpty { continue }
        let input = scalar(object, kAudioProcessPropertyIsRunningInput)
        let output = scalar(object, kAudioProcessPropertyIsRunningOutput)
        if input == nil || output == nil { complete = false }
        processes.append([
            "pid": Int(pid), "bundle_id": bundle,
            "input_running": input.map { $0 != 0 } as Any? ?? NSNull(),
            "output_running": output.map { $0 != 0 } as Any? ?? NSNull()
        ])
    }
    return ["version": 1, "available": true, "complete": complete, "processes": processes]
}

@main
struct Observer {
    static func main() {
        let args = Array(CommandLine.arguments.dropFirst())
        if args == ["--version"] { print("scribeloop-observer 1; metadata only"); return }
        guard args.isEmpty else {
            FileHandle.standardError.write(Data("Usage: scribeloop-observer [--version]\n".utf8))
            exit(2)
        }
        guard #available(macOS 14.2, *) else {
            print("{\"version\":1,\"available\":false,\"complete\":false,\"processes\":[]}")
            exit(1)
        }
        do {
            let data = try JSONSerialization.data(withJSONObject: snapshot(), options: [.sortedKeys])
            FileHandle.standardOutput.write(data)
            FileHandle.standardOutput.write(Data("\n".utf8))
        } catch {
            print("{\"version\":1,\"available\":false,\"complete\":false,\"processes\":[]}")
            exit(1)
        }
    }
}
