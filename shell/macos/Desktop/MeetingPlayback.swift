import Foundation
import Combine
import AVFoundation

@MainActor final class MeetingPlayback: NSObject, ObservableObject, AVAudioPlayerDelegate {
    @Published private(set) var duration = 0.0
    @Published private(set) var position = 0.0
    @Published private(set) var playing = false
    @Published private(set) var loading = false
    @Published private(set) var tracks: [String] = []
    @Published private(set) var track = "conversation"
    @Published private(set) var failure: String?
    @Published var rate: Float = 1 { didSet { player?.rate = rate; nextPlayer?.rate = rate } }
    var active: Bool { playing || wantsPlay }
    var request: ((Object) -> String?)?
    private var sessionID: String?
    private var infoRequest: String?
    private var durations: [String:Double] = [:]
    private var generation = UUID()
    private var pending: [String:(UUID,Double,Bool)] = [:]
    private var player: AVAudioPlayer?, nextPlayer: AVAudioPlayer?
    private var start = 0.0, nextStart = 0.0
    private var wantsPlay = false
    private var ticker: Task<Void,Never>?

    func open(_ sid: String, autoplay: Bool = false) {
        close(); sessionID = sid; loading = true; wantsPlay = autoplay
        infoRequest = request?(["action":"audio_info","session_id":sid])
        if infoRequest == nil { loading = false; wantsPlay = false; failure = "Reconnect to load audio." }
        ticker = Task { [weak self] in
            while !Task.isCancelled {
                try? await Task.sleep(nanoseconds:100_000_000)
                guard !Task.isCancelled, let self else { return }
                self.tick()
            }
        }
    }
    func receive(_ value: Object) {
        guard text(value["session_id"]) == sessionID else { return }
        let response = text(value["response_to"])
        if text(value["type"]) == "audio_info" {
            guard response == infoRequest else { return }
            infoRequest = nil; loading = false
            tracks = value["tracks"] as? [String] ?? []
            durations = (value["track_durations"] as? [String:NSNumber] ?? [:]).mapValues { $0.doubleValue / 1000 }
            duration = durations.values.max() ?? 0; position = 0
            if wantsPlay && duration > 0 { seek(0,autoplay:true) }
            else { wantsPlay = false }
            return
        }
        guard let entry = pending.removeValue(forKey:response), entry.0 == generation,
              text(value["stream"]) == track,
              let bytes = Data(base64Encoded:text(value["wav_base64"])), bytes.count <= 2_000_100 else { return }
        do {
            let prepared = try AVAudioPlayer(data:bytes)
            prepared.enableRate = true; prepared.rate = rate; prepared.delegate = self; prepared.prepareToPlay()
            if entry.2 { nextPlayer = prepared; nextStart = entry.1 }
            else {
                player?.stop(); player = prepared; start = entry.1; position = start; loading = false
                if wantsPlay { playing = play(prepared) }
            }
        } catch { loading = false; playing = false; wantsPlay = false; failure = "This audio window could not be decoded." }
    }
    func reject(_ id: String,_ message: String) {
        guard id == infoRequest || pending[id] != nil else { return }
        infoRequest = nil; pending.removeAll(); failure = message
        loading = false; wantsPlay = false; playing = false; player?.pause()
    }
    func toggle() {
        if wantsPlay || playing { pause(); return }
        guard duration > 0 else { return }
        failure = nil; wantsPlay = true
        if position >= duration-0.01 { seek(0,autoplay:true) }
        else if let player { playing = play(player) }
        else { seek(position,autoplay:true) }
    }
    func pause() {
        if let player, playing { position = min(duration,start+player.currentTime) }
        player?.pause(); playing = false; wantsPlay = false
    }
    func seek(_ seconds: Double,autoplay: Bool? = nil) {
        let resume = autoplay ?? wantsPlay
        generation = UUID(); pending.removeAll(); player?.stop(); player = nil; nextPlayer = nil
        playing = false; loading = false; wantsPlay = resume; failure = nil
        position = min(max(0,seconds),duration)
        guard position < duration else { wantsPlay = false; return }
        load(position,prefetch:false)
    }
    func chooseTrack(_ value: String) {
        guard value != track else { return }
        let oldPosition = position, resume = wantsPlay
        track = value; duration = value == "conversation" ? durations.values.max() ?? 0 : durations[value] ?? 0
        seek(oldPosition,autoplay:resume)
    }
    private func load(_ seconds: Double,prefetch: Bool) {
        guard let sessionID else { return }
        if !prefetch { loading = true }
        let milliseconds = Int((seconds*1000).rounded())
        guard let id = request?(["action":"audio_window","session_id":sessionID,"stream":track,"start_ms":milliseconds]) else {
            if !prefetch { loading = false; wantsPlay = false; failure = "Reconnect to load audio." }; return
        }
        pending[id] = (generation,Double(milliseconds)/1000,prefetch)
    }
    private func play(_ prepared: AVAudioPlayer) -> Bool {
        if prepared.play() { return true }
        wantsPlay = false; failure = "Audio output is unavailable. Check your output device and try again."
        return false
    }
    private func tick() {
        guard playing, let player else { return }
        position = min(duration,start+player.currentTime)
        let end = start+player.duration
        if wantsPlay && nextPlayer == nil && !pending.values.contains(where: { $0.2 }) && end < duration-0.01 && player.duration-player.currentTime < Double(rate)*5 {
            load(end,prefetch:true)
        }
    }
    nonisolated func audioPlayerDidFinishPlaying(_ player: AVAudioPlayer,successfully flag: Bool) {
        let identity = ObjectIdentifier(player)
        Task { @MainActor [weak self] in self?.finished(identity,success:flag) }
    }
    private func finished(_ identity: ObjectIdentifier,success: Bool) {
        guard let player, ObjectIdentifier(player) == identity else { return }
        position = min(duration,start+player.duration); self.player = nil; playing = false
        guard success, wantsPlay else { wantsPlay = false; return }
        if position >= duration-0.01 { wantsPlay = false; position = duration; return }
        if let next = nextPlayer {
            self.player = next; start = nextStart; nextPlayer = nil; position = start
            playing = play(next)
        } else {
            // A pending prefetch belongs to the old player; load a foreground
            // window rather than treating an unavailable next window as EOF.
            generation = UUID(); pending.removeAll(); loading = false
            load(position,prefetch:false)
        }
    }
    func close() {
        ticker?.cancel(); ticker = nil; generation = UUID(); pending.removeAll(); infoRequest = nil
        player?.stop(); player = nil; nextPlayer = nil; sessionID = nil; durations = [:]
        position = 0; duration = 0; playing = false; wantsPlay = false; loading = false; failure = nil; tracks = []; track = "conversation"
    }
}
