import Foundation

/// Preview-only native player regression: generated silence, never a microphone.
@MainActor enum PlaybackSmoke {
    static func run() async -> Bool {
        let playback = MeetingPlayback()
        var starts = [Int]()
        func wav(_ seconds: Double) -> Data {
            let count = Int((seconds*16000).rounded())*2
            var data = Data("RIFF".utf8)
            func number<T: FixedWidthInteger>(_ value: T) {
                var little = value.littleEndian
                withUnsafeBytes(of:&little) { data.append(contentsOf:$0) }
            }
            number(UInt32(36+count)); data.append(Data("WAVEfmt ".utf8)); number(UInt32(16))
            number(UInt16(1)); number(UInt16(1)); number(UInt32(16000)); number(UInt32(32000))
            number(UInt16(2)); number(UInt16(16)); data.append(Data("data".utf8)); number(UInt32(count))
            data.append(Data(repeating:0,count:count)); return data
        }
        playback.request = { [weak playback] command in
            let id = UUID().uuidString
            Task { @MainActor in
                try? await Task.sleep(nanoseconds:20_000_000)
                guard let playback else { return }
                if text(command["action"]) == "audio_info" {
                    playback.receive(["type":"audio_info","session_id":"test","response_to":id,
                        "tracks":["mic"],"track_durations":["mic":1200]])
                } else {
                    let start = command["start_ms"] as? Int ?? 0
                    starts.append(start)
                    let seconds = min(0.4,max(0,1.2-Double(start)/1000))
                    playback.receive(["type":"audio_window","session_id":"test","response_to":id,
                        "stream":command["stream"] ?? "conversation","start_ms":start,
                        "wav_base64":wav(seconds).base64EncodedString()])
                }
            }
            return id
        }
        func wait(_ predicate: () -> Bool) async -> Bool {
            for _ in 0..<300 { if predicate() { return true }; try? await Task.sleep(nanoseconds:20_000_000) }
            return false
        }
        defer { playback.close() }
        playback.open("test")
        guard await wait({playback.duration == 1.2}) else { return false }
        playback.rate = 2; playback.toggle()
        guard await wait({playback.position == 1.2 && !playback.active}), starts.count >= 3 else { return false }
        playback.seek(0.2,autoplay:true)
        guard await wait({playback.playing}) else { return false }
        playback.pause(); let paused = playback.position
        try? await Task.sleep(nanoseconds:100_000_000)
        guard !playback.playing, abs(playback.position-paused)<0.01 else { return false }
        playback.seek(0.7,autoplay:false); playback.seek(0.9,autoplay:false)
        guard await wait({!playback.loading && playback.position == 0.9}), !playback.playing else { return false }
        playback.chooseTrack("mic")
        guard await wait({!playback.loading && playback.track == "mic" && playback.position == 0.9}) else { return false }
        playback.close()
        guard playback.duration == 0 && !playback.active else { return false }
        // The first explicit Play loads metadata and then begins playback.
        playback.open("test",autoplay:true)
        guard await wait({playback.playing}) else { return false }
        playback.close()
        return playback.duration == 0 && !playback.active
    }
}
