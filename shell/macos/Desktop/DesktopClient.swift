import Foundation
import Combine
import Security
import AppKit
import AVFoundation
import Darwin

typealias Object = [String: Any]
struct Record: Identifiable {
    let raw: Object
    var id: String { raw["id"] as? String ?? "" }
    func text(_ key: String) -> String { raw[key] as? String ?? "" }
    func int(_ key: String) -> Int { (raw[key] as? NSNumber)?.intValue ?? 0 }
    func object(_ key: String) -> Object { raw[key] as? Object ?? [:] }
    var personTitle: String {
        let number = int("person_number")
        guard number > 0 else { return "Speaker unknown" }
        let label = text("name").isEmpty ? text("speaker_label") : text("name")
        return label.isEmpty || label == "Speaker \(number)" || label == "Speaker unknown" ? "Person \(number)" : "Person \(number) · \(label)"
    }
}
func objects(_ value: Any?) -> [Record] { (value as? [Object] ?? []).map { Record(raw: $0) } }
func text(_ value: Any?) -> String { value as? String ?? "" }

@MainActor final class DesktopClient: ObservableObject {
    @Published var state = "Stopped"
    @Published var message: String?
    @Published var sessions: [Record] = []
    @Published var selectedID: String?
    @Published var detail: Object = [:]
    @Published var pageSerial = 0
    @Published var citationID: String?
    @Published var settings: Object = [:]
    @Published var summaryProvider: Object = [:]
    @Published var transcription: Object = [:]
    @Published var discovery: Object = [:]
    @Published var agreement: Object = [:]
    @Published var people: [Record] = []
    @Published var nextPeopleOffset: Int?
    @Published var activeID: String?
    @Published var processingID: String?
    @Published var processingLabel = "Transcribing saved audio…"
    @Published var summaryID: String?
    @Published var partials: [String:String] = [:]
    @Published var streamingProgress: [String:Object] = [:]
    @Published var requests: [String:String] = [:]
    @Published var playing = false
    @Published var callEnding = false
    @Published var callStatus: String?
    @Published var captureStopping = false
    @Published var captureAccessMessage: String?
    @Published var nextCursor: [Any]?
    @Published var database: String
    var onCandidate: ((Record) -> Void)?
    private var shownCandidates = Set<String>()
    private var mutationIDs = Set<String>()
    private var process: Process?
    private var input: Pipe?, output: Pipe?, diagnostics: Pipe?
    private var buffer = Data()
    private var token = "", launch = "", boot = ""
    private var port: Int?
    private var socket: URLSessionWebSocketTask?
    private var connection = UUID()
    private var reader: Task<Void,Never>?, deadline: Task<Void,Never>?, shutdown: Task<Void,Never>?
    private var refreshTask: Task<Void,Never>?
    private var stopping = false
    private var pageRequest: String?
    private var completion: (() -> Void)?
    let playback = MeetingPlayback()
    private var player: AVAudioPlayer?
    private var reconnectAttempts = 0
    private var lastSequence = 0
    var connected: Bool { state == "Connected" }
    var running: Bool { process?.isRunning == true }
    var busy: Bool { activeID != nil || processingID != nil || summaryID != nil }
    var accepted: Bool { agreement["accepted"] as? Bool == true }
    var selected: Record? { sessions.first { $0.id == selectedID } }
    var speakerRoster: [Record] { objects(detail["speakers"]) }
    var rows: [Record] { objects(detail["segments"]) }
    var revision: Int { (detail["revision"] as? NSNumber)?.intValue ?? 0 }
    var offset: Int { (detail["offset"] as? NSNumber)?.intValue ?? 0 }
    var total: Int { (detail["total"] as? NSNumber)?.intValue ?? 0 }
    var detection: Bool { discovery["enabled"] as? Bool ?? true }
    var streamingLabel: String? {
        guard let sid = activeID ?? processingID else { return nil }
        let progress = streamingProgress.values.filter { text($0["session_id"]) == sid }
        guard !progress.isEmpty else { return nil }
        let sendLag = progress.map { ($0["send_lag_ms"] as? NSNumber)?.doubleValue ?? 0 }.max() ?? 0
        let pending = progress.map { ($0["lag_ms"] as? NSNumber)?.doubleValue ?? 0 }.max() ?? 0
        if sendLag >= 1000 { return String(format:"Catching up · %.1f s of audio waiting to send",sendLag/1000) }
        if pending >= 2000 { return String(format:"Awaiting confirmed text · %.1f s of audio pending",pending/1000) }
        return "Live transcription connected"
    }
    var monitorLabel: String {
        guard connected else { return state }
        switch text(discovery["status"]) {
        case "available": return "Watching for calls"
        case "paused": return "Detection paused"
        default: return "Detection unavailable"
        }
    }

    init(database: String? = nil) {
        self.database = database ?? UserDefaults.standard.string(forKey: "databasePath") ??
            FileManager.default.urls(for: .applicationSupportDirectory, in: .userDomainMask)[0]
            .appendingPathComponent("ScribeLoop/meetings.sqlcipher").path
        playback.request = { [weak self] command in self?.send(command) }
    }
    private func random(_ count: Int) throws -> String {
        var bytes = [UInt8](repeating: 0,count: count)
        guard SecRandomCopyBytes(kSecRandomDefault,count,&bytes) == errSecSuccess else { throw CocoaError(.coderInvalidValue) }
        return bytes.map { String(format:"%02x",$0) }.joined()
    }
    func start() {
        guard !running else { return }
        clearConnection(); port = nil; buffer = Data(); boot = ""; stopping = false
        sessions = []; detail = [:]; partials = [:]; streamingProgress = [:]; activeID = nil; processingID = nil; summaryID = nil; selectedID = nil
        for id in mutationIDs { requests[id] = "unknown" }
        agreement = [:]; settings = [:]; discovery = [:]; state = "Starting"; message = nil
        do {
            token = try random(32); launch = try random(16)
            let generation = launch
            let exe = Bundle.main.bundleURL.appendingPathComponent("Contents/Resources/Core/scribeloop-core")
            guard FileManager.default.isExecutableFile(atPath: exe.path) else { throw CocoaError(.fileNoSuchFile) }
            let child = Process(), stdin = Pipe(), stdout = Pipe(), stderr = Pipe()
            child.executableURL = exe; child.currentDirectoryURL = URL(fileURLWithPath: "/")
            child.environment = ["PATH":"/usr/bin:/bin:/usr/sbin:/sbin", "HOME":NSHomeDirectory(),
                "TMPDIR":NSTemporaryDirectory(), "LC_ALL":"en_US.UTF-8", "PYTHONNOUSERSITE":"1"]
            child.standardInput = stdin; child.standardOutput = stdout; child.standardError = stderr
            stdout.fileHandleForReading.readabilityHandler = { [weak self] handle in
                let bytes = handle.availableData
                Task { @MainActor in self?.control(bytes,generation: generation) }
            }
            stderr.fileHandleForReading.readabilityHandler = { handle in _ = handle.availableData }
            child.terminationHandler = { [weak self] _ in Task { @MainActor in
                guard let self, self.launch == generation else { return }
                let expected = self.stopping
                self.finishStop()
                if !expected { self.state = "Needs attention"; self.message = "Core stopped. Saved meetings remain encrypted. Check Keychain access, storage location and whether the demo server is using this database, then retry. Interrupted work is never restarted automatically." }
            } }
            process = child; input = stdin; output = stdout; diagnostics = stderr
            try child.run()
            var request = try JSONSerialization.data(withJSONObject: ["protocol":2,"token":token,
                "launch_id":launch,"database":database] as Object); request.append(10)
            try stdin.fileHandleForWriting.write(contentsOf: request)
            deadline = Task { [weak self] in
                try? await Task.sleep(nanoseconds: 35_000_000_000)
                guard !Task.isCancelled, let self, self.launch == generation, !self.connected else { return }
                self.message = "Core startup timed out. Check Keychain access and the database lock."; self.stop()
            }
        } catch { message = "Could not launch the bundled core. Check the app installation."; stop() }
    }
    private func control(_ bytes: Data,generation: String) {
        guard generation == launch, !stopping, !bytes.isEmpty else { return }
        buffer.append(bytes)
        guard buffer.count <= 8192 else { message = "Invalid core handshake."; stop(); return }
        while let end = buffer.firstIndex(of:10) {
            let data = Data(buffer[..<end]); buffer.removeSubrange(...end)
            guard let value = try? JSONSerialization.jsonObject(with:data) as? Object,
                text(value["type"]) == "ready", (value["protocol"] as? Int) == 2,
                (value["pid"] as? Int32) == process?.processIdentifier,
                text(value["launch_id"]) == launch, text(value["host"]) == "127.0.0.1",
                let actualPort = value["port"] as? Int, (1...65535).contains(actualPort), port == nil,
                text(value["boot_id"]).count == 32 else {
                message = "Core could not open storage or settings. Check Keychain access and whether another app is using this database."; stop(); return
            }
            port = actualPort; boot = text(value["boot_id"]); reconnectAttempts = 0; connect()
        }
    }
    func reconnect() { guard running, !stopping else { return }; reconnectAttempts = 0; connect() }
    private func connect() {
        clearConnection()
        guard let port else { return }
        state = "Connecting"; lastSequence = 0
        let generation = UUID(); connection = generation
        let configuration = URLSessionConfiguration.ephemeral
        configuration.urlCache = nil; configuration.httpCookieStorage = nil; configuration.connectionProxyDictionary = [:]
        configuration.timeoutIntervalForRequest = 15
        let session = URLSession(configuration:configuration)
        let ws = session.webSocketTask(with:URL(string:"ws://127.0.0.1:\(port)/ws")!)
        ws.maximumMessageSize = 16_000_000; socket = ws; ws.resume()
        let secret = token
        reader = Task { [weak self] in
            defer { session.invalidateAndCancel() }
            do {
                try await ws.send(.string(secret))
                while !Task.isCancelled {
                    let frame = try await ws.receive()
                    let data: Data
                    switch frame { case .string(let s): data = Data(s.utf8); case .data(let d): data = d; @unknown default: throw CocoaError(.coderInvalidValue) }
                    guard let self, generation == self.connection, data.count <= 16_000_000,
                        let value = try JSONSerialization.jsonObject(with:data) as? Object,
                        value["protocol"] as? Int == 2, text(value["boot_id"]) == self.boot,
                        let sequence = value["sequence"] as? Int, sequence > self.lastSequence else { throw CocoaError(.coderInvalidValue) }
                    self.lastSequence = sequence
                    self.receive(value)
                }
            } catch {
                guard let self, generation == self.connection, !self.stopping, !Task.isCancelled else { return }
                self.playback.close()
                self.state = "Disconnected"; self.message = "Connection interrupted. Pending actions will be checked, never replayed."
                if self.reconnectAttempts < 3 && self.running {
                    self.reconnectAttempts += 1
                    try? await Task.sleep(nanoseconds:UInt64(self.reconnectAttempts)*1_000_000_000)
                    guard generation == self.connection, !Task.isCancelled else { return }
                    self.connect()
                }
            }
        }
    }
    @discardableResult func send(_ command: Object) -> String? {
        guard connected, let ws = socket else { message = "Core is disconnected. Reconnect before choosing an action."; return nil }
        let action = text(command["action"])
        if ["delete","reprocess","recover","resume"].contains(action) { stopAudio() }
        let reads = ["desktop_snapshot","history_page","meeting_page","request_status","people_page","audio_info","audio_window","check_capture_access"]
        if !reads.contains(action) && !["stop","stop_recording","revoke_agreement"].contains(action) && mutationIDs.contains(where: { requests[$0] == "unknown" || requests[$0] == "dispatching" || requests[$0] == "pending" }) {
            message = "An earlier action is uncertain. Reconnect and review current state before making another change."; return nil
        }
        if requests.count >= 256 { requests = requests.filter { !["accepted","rejected"].contains($0.value) } }
        guard requests.count < 256 else { message = "Too many pending actions. Reconnect to check their status."; return nil }
        let id = UUID().uuidString.replacingOccurrences(of:"-",with:"").lowercased()
        do {
            let envelope: Object = ["protocol":2,"request_id":id,"command":command]
            let bytes = try JSONSerialization.data(withJSONObject:envelope)
            guard bytes.count <= 12288 else { throw CocoaError(.coderInvalidValue) }
            requests[id] = "pending"
            if !reads.contains(action) { mutationIDs.insert(id) }
            if requests.count > 256, let old = requests.first(where: { $0.value == "accepted" || $0.value == "rejected" })?.key { requests.removeValue(forKey:old) }
            Task { [weak self] in
                do { try await ws.send(.string(String(decoding:bytes,as:UTF8.self))) }
                catch { self?.requests[id] = "unknown"; self?.message = "Action delivery is uncertain. Reconnect to check status before trying again." }
            }
            return id
        } catch { message = "Action is invalid or too large."; return nil }
    }
    private func receive(_ v: Object) {
        let kind = text(v["type"])
        switch kind {
        case "snapshot":
            state = "Connected"; message = nil; deadline?.cancel(); reconnectAttempts = 0
            if let location = v["database"] as? String { database = location }
            settings = v["settings"] as? Object ?? [:]; summaryProvider = v["summary_provider"] as? Object ?? [:]
            transcription = v["transcription"] as? Object ?? [:]; discovery = v["discovery"] as? Object ?? [:]
            agreement = v["agreement"] as? Object ?? [:]; sessions = objects(v["sessions"]); nextCursor = v["next_cursor"] as? [Any]
            activeID = v["active_id"] as? String; summaryID = v["summary_active_id"] as? String
            captureStopping = false; callStatus = nil; callEnding = false
            processingID = v["processing_id"] as? String
            partials = [:]; detail = [:]; streamingProgress = [:]
            suggestCall()
            let uncertain = requests.filter { ["pending","dispatching","unknown"].contains($0.value) }.map(\.key)
            for id in uncertain { send(["action":"request_status","request_id":id]) }
            if let activeID { select(activeID) }
            else if let selectedID { select(selectedID) }
            else if let first = sessions.first { select(first.id) }
        case "receipt":
            let id = text(v["request_id"]), status = text(v["status"])
            requests[id] = status
            if status == "accepted" || status == "rejected" { mutationIDs.remove(id) }
            if status == "rejected" { playback.reject(id,"Audio is unavailable. Check its retention status or recover interrupted audio.") }
            if status == "unknown" { message = "A previous action has no retained receipt. Review the current meeting state before repeating it." }
        case "history_page":
            let page = objects(v["sessions"])
            for row in page { if !sessions.contains(where: { $0.id == row.id }) { sessions.append(row) } }
            if sessions.count > 120 { sessions = Array(sessions.suffix(120)) }
            nextCursor = v["next_cursor"] as? [Any]
        case "meeting_page":
            guard text((v["session"] as? Object)?["id"]) == selectedID,
                text(v["response_to"]) == pageRequest else { return }
            detail = v; pageSerial += 1; upsert(v["session"] as? Object ?? [:])
        case "settings":
            settings = v["settings"] as? Object ?? [:]; summaryProvider = v["summary_provider"] as? Object ?? [:]
            transcription = v["transcription"] as? Object ?? [:]; message = "Settings saved securely. New work will use these settings."
        case "agreement": agreement = v["agreement"] as? Object ?? [:]; suggestCall()
        case "discovery":
            discovery = v["discovery"] as? Object ?? [:]
            suggestCall()
        case "session":
            let row = v["session"] as? Object ?? [:]; upsert(row)
            let status = text(row["status"]), sid = text(row["id"])
            let wasRecording = activeID == sid
            if ["preparing","recording","stopping"].contains(status) { stopAudio(); activeID = sid; captureStopping = status == "stopping"; if selectedID != sid { select(sid) } }
            else if activeID == sid { activeID = nil; partials = [:]; callEnding = false; callStatus = nil; captureStopping = false }
            if status == "processing" { processingID = sid; processingLabel = wasRecording ? "Finishing live transcription…" : "Transcribing saved audio…" }
            else if processingID == sid { processingID = nil }
            reloadSoon()
        case "transcription_progress":
            if text(v["mode"]) == "streaming" {
                streamingProgress[text(v["stream"])] = v
                if processingID == text(v["session_id"]) { processingLabel = "Finishing live transcription…" }
            }
            if text(v["mode"]) == "batch_upload" { processingLabel = "Uploading saved audio…" }
            else if text(v["mode"]) == "batch_wait" { processingLabel = "Waiting for transcription…" }
        case "session_deleted":
            let sid = text(v["session_id"])
            let index = sessions.firstIndex { $0.id == sid } ?? 0
            sessions.removeAll { $0.id == sid }
            if selectedID == sid {
                stopAudio(); detail = [:]; partials = [:]
                selectedID = sessions.isEmpty ? nil : sessions[min(index,sessions.count-1)].id
            }
        case "conversation_renamed":
            upsert(v["session"] as? Object ?? [:]); reloadSoon()
        case "partial":
            if text(v["session_id"]) == selectedID { partials[text(v["stream"])] = text((v["segment"] as? Object)?["text"]) }
        case "segment":
            if text(v["session_id"]) == selectedID { partials[text((v["segment"] as? Object)?["stream"])] = nil; reloadSoon() }
        case "transcript_replaced":
            if text(v["session_id"]) == selectedID { detail = [:]; partials = [:]; reloadSoon() }
        case "summary_started": summaryID = v["session_id"] as? String
        case "summary_ready", "summary_error": summaryID = nil; reloadSoon(); if kind == "summary_error" { message = text(v["message"]) }
        case "markdown_export": saveExport(text(v["content"]))
        case "audio_info", "audio_window": playback.receive(v)
        case "audio_playback":
            guard text(v["session_id"]) == selectedID,
                let data = Data(base64Encoded:text(v["wav_base64"])), data.count <= 10_000_000 else { return }
            do { player = try AVAudioPlayer(data:data); player?.prepareToPlay(); player?.play(); playing = true }
            catch { message = "This audio window could not be played." }
        case "audio_state":
            if text(v["session_id"]) == selectedID, let audio = v["audio"] as? Object, audio["available"] as? Bool != true { stopAudio() }
            reloadSoon()
        case "people": people = objects(v["people"]); nextPeopleOffset = v["next_offset"] as? Int
        case "auto_call_status": applyCallStatus(v)
        case "capture_access":
            if v["ok"] as? Bool == true {
                captureAccessMessage = "Meeting-audio access: \(v["output_access"] as? Bool == true ? "allowed" : "unavailable"). Microphone: \(text(v["microphone"])). Display: \(v["display_available"] as? Bool == true ? "available" : "unavailable"). No audio was recorded."
            } else {
                captureAccessMessage = text((v["diagnostic"] as? Object)?["message"])
            }
        case "recording_state":
            activeID = v["active_id"] as? String; processingID = v["processing_id"] as? String
            captureStopping = v["stopping"] as? Bool ?? false
            callEnding = false; callStatus = nil
            if activeID == nil { partials = [:] }
        case "error", "recording_error", "transcription_error", "rolling_notes_error", "audio_cleanup_error":
            if kind == "transcription_error" { streamingProgress = [:] }
            message = text(v["message"]); reloadSoon()
        case "reconnect_required": reconnect()
        case "invalidate": detail = [:]; partials = [:]; reloadSoon(); send(["action":"people_page"])
        default: if ["audio_state","notes","rolling_notes","recovery_complete","speakers_corrected"].contains(kind) { reloadSoon() }
        }
    }
    private func suggestCall() {
        guard accepted, !busy, let row = objects(discovery["candidates"]).first(where: {
            $0.raw["selectable"] as? Bool == true && !shownCandidates.contains($0.id)
        }) else { return }
        if shownCandidates.count >= 256 {
            let current = Set(objects(discovery["candidates"]).map(\.id))
            shownCandidates.formIntersection(current)
        }
        shownCandidates.insert(row.id); onCandidate?(row)
    }
    private func upsert(_ value: Object) {
        let row = Record(raw:value); guard !row.id.isEmpty else { return }
        if let index = sessions.firstIndex(where: { $0.id == row.id }) { sessions[index] = row }
        else { sessions.insert(row,at:0); if sessions.count > 120 { sessions.removeLast() } }
    }
    func select(_ id: String) { selectedID = id; detail = [:]; partials = [:]; stopAudio(); loadPage(-1) }
    func loadPage(_ offset: Int, anchor: String? = nil) {
        guard let selectedID else { return }
        var command: Object = ["action":"meeting_page","session_id":selectedID,"offset":offset]
        if revision > 0 { command["revision"] = revision }
        if let anchor { command["anchor"] = anchor }
        citationID = anchor
        pageRequest = send(command)
    }
    private func reloadSoon() {
        guard refreshTask == nil else { return }
        refreshTask = Task { [weak self] in
            try? await Task.sleep(nanoseconds:300_000_000)
            guard let self, !Task.isCancelled else { return }; self.refreshTask = nil
            self.loadPage(self.activeID == self.selectedID ? -1 : self.offset)
        }
    }
    func acknowledgeUncertainActions() {
        for id in mutationIDs where ["unknown","dispatching"].contains(requests[id] ?? "") { requests[id] = "reviewed" }
        mutationIDs = mutationIDs.filter { requests[$0] != "reviewed" }
        message = "Uncertain actions acknowledged. No command was replayed."
    }
    var uncertainActions: Bool { mutationIDs.contains { ["unknown","dispatching"].contains(requests[$0] ?? "") } }
    func applyCallStatus(_ value: Object) {
        guard text(value["session_id"]) == activeID else { return }
        switch text(value["state"]) {
        case "ending":
            callEnding = true
            let reason = text(value["reason"]) == "microphone_released" ? "Meeting app released its microphone; the call may have ended or been muted" : "Call audio activity ended"
            callStatus = "\(reason) · stopping in \(value["remaining_s"] as? Int ?? 60) seconds."
        case "unknown":
            callEnding = false
            callStatus = "Call-end detection is unavailable. Press Stop recording when finished."
        default:
            callEnding = false; callStatus = nil
        }
    }
    func stopRecording() { send(["action":"stop_recording"]) }
    var transcriptionLanguageLabel: String {
        let names = settings["supported_language_hints"] as? [String:String] ?? ["en":"English","vi":"Tiếng Việt"]
        let codes = settings["language_hints"] as? [String] ?? ["en","vi"]
        let selected = codes.map { names[$0] ?? $0 }
        let label = selected.prefix(3).joined(separator:", ")
        return selected.count > 3 ? "\(label) + \(selected.count - 3) more" : label
    }
    @discardableResult func record(scope: String, app: String?, microphone: String, candidate: Record? = nil) -> String? {
        var command: Object
        if let candidate {
            command = ["action":"accept_call","candidate_id":candidate.id,"app_id":candidate.text("app_id"),
                "automatic_summary":summaryProvider["configured"] as? Bool == true,
                "summary_provider":text(summaryProvider["provider"]),"summary_model":text(summaryProvider["model"]),
                "summary_endpoint":text(summaryProvider["endpoint"])]
        } else {
            command = ["action":"record","request_id":1,"scope":scope,"title":"Meeting conversation"]
            if let app, scope == "application" { command["app_id"] = app }
        }
        command["microphone_id"] = microphone; command["model"] = "deepgram"
        command["language"] = settings["language"] ?? "auto"; command["duration_s"] = settings["duration_s"] ?? 3600
        command["language_hints"] = settings["language_hints"] ?? ["en","vi"]
        command["consent_confirmed"] = true; return send(command)
    }
    func stopAudio() { playback.close(); player?.stop(); player = nil; playing = false }
    func saveExport(_ content: String) {
        let panel = NSSavePanel(); panel.nameFieldStringValue = "Meeting notes.md"
        panel.message = "This export is readable plaintext outside encrypted app storage."
        if panel.runModal() == .OK, let url = panel.url { do { try content.write(to:url,atomically:true,encoding:.utf8) } catch { message = "Export could not be saved." } }
    }
    func chooseDatabase() {
        guard !busy else { return }
        let panel = NSOpenPanel(); panel.canChooseFiles = true; panel.canChooseDirectories = false; panel.allowsMultipleSelection = false
        panel.message = "Select the existing .sqlcipher database in place. Stop the demo server first. Its adjacent recordings directory and Keychain keys stay at their original locations."
        guard panel.runModal() == .OK, let url = panel.url, url.pathExtension == "sqlcipher" else { return }
        stop { [weak self] in
            guard let self else { return }; self.database = url.path; UserDefaults.standard.set(url.path,forKey:"databasePath"); self.start()
        }
    }
    func stop(completion: (() -> Void)? = nil) {
        self.completion = completion ?? self.completion
        stopAudio(); refreshTask?.cancel(); refreshTask = nil
        guard let child = process, child.isRunning else { finishStop(); return }
        guard !stopping else { return }
        stopping = true; state = "Stopping"; clearConnection(); deadline?.cancel()
        try? input?.fileHandleForWriting.write(contentsOf:Data("STOP\n".utf8)); try? input?.fileHandleForWriting.close()
        let generation = launch
        shutdown = Task { [weak self,weak child] in
            try? await Task.sleep(nanoseconds:8_000_000_000)
            guard !Task.isCancelled, let self, self.launch == generation, let child, child.isRunning else { return }
            self.message = "Shutdown exceeded its deadline. Interrupted audio may need recovery."; child.terminate()
            try? await Task.sleep(nanoseconds:2_000_000_000)
            if !Task.isCancelled && child.isRunning { Darwin.kill(child.processIdentifier,SIGKILL) }
        }
    }
    private func clearConnection() { connection = UUID(); reader?.cancel(); reader = nil; socket?.cancel(with:.goingAway,reason:nil); socket = nil }
    private func finishStop() {
        shutdown?.cancel(); deadline?.cancel(); clearConnection(); port = nil; token = ""
        output?.fileHandleForReading.readabilityHandler = nil; diagnostics?.fileHandleForReading.readabilityHandler = nil
        try? output?.fileHandleForReading.close(); try? diagnostics?.fileHandleForReading.close(); try? input?.fileHandleForWriting.close()
        input = nil; output = nil; diagnostics = nil; process = nil; state = "Stopped"; stopping = false
        activeID = nil; processingID = nil; summaryID = nil; streamingProgress = [:]
        let callback = completion; completion = nil; callback?()
    }
}
