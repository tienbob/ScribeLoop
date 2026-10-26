import SwiftUI
import AppKit
import ServiceManagement

struct Preferences: View {
    @ObservedObject var core: DesktopClient
    @Environment(\.dismiss) private var dismiss
    @State private var summaryKey = ""
    @State private var endpoint = "https://api.openai.com/v1"
    @State private var model = "gpt-5.4-mini"
    @State private var provider = "OpenAI"
    @State private var language = "auto"
    @State private var duration = 3600
    @State private var clearSummary = false
    @State private var login = SMAppService.mainApp.status == .enabled
    @State private var loginStatus = ""
    private var presets: [String:String] { core.settings["summary_presets"] as? [String:String] ?? [:] }
    private var localSummary: Bool { provider == "Ollama" || provider == "LM Studio" }
    var body: some View {
        VStack(alignment:.leading,spacing:12) {
            HStack { Text("Preferences").font(.system(size:28,design:.serif)); Spacer(); Button("Done") { dismiss() }.keyboardShortcut(.cancelAction) }.padding(.horizontal,24).padding(.top,20)
            Form {
                Section("Summary provider · OpenAI compatible") {
                    Picker("Provider",selection:Binding(get:{provider},set:selectProvider)) {
                        Text("OpenAI · Cloud").tag("OpenAI")
                        Text("Gemini · Cloud").tag("Gemini")
                        Text("Ollama · Local").tag("Ollama")
                        Text("LM Studio · Local").tag("LM Studio")
                        Text("Custom · OpenAI compatible").tag("Custom")
                    }
                    if provider == "Custom" { TextField("API base URL",text:$endpoint) }
                    TextField("Model ID",text:$model,prompt:Text(localSummary ? "Name of your installed model" : "Model ID from your provider"))
                    SecureField(localSummary ? "API key (optional)" : "New summary API key",text:$summaryKey).textContentType(.password)
                    Text(localSummary ? "Uses your running local server and installed model. An API key is optional unless you enabled server authentication. Transcription still uses Deepgram." : "Transcript text is sent to this provider for notes. Leave the key blank to keep it only when the destination stays the same; changing providers clears the old key.").font(.caption).foregroundStyle(.secondary)
                    Toggle("Remove saved summary key",isOn:$clearSummary)
                }
                Section("Recording preset") {
                    Picker("Maximum duration",selection:$duration) { Text("5 minutes · $0.25 audio cap").tag(300); Text("1 hour · $3 audio cap").tag(3600); Text("3 hours · $8 audio cap").tag(10800) }
                    Picker("Conversation language",selection:$language) {
                        ForEach([("auto","Auto · multilingual"),("en","English"),("vi","Tiếng Việt"),("es","Spanish"),("fr","French"),("de","German"),("hi","Hindi"),("ru","Russian"),("pt","Portuguese"),("ja","Japanese"),("it","Italian"),("nl","Dutch")],id:\.0) { code, name in Text(name).tag(code) }
                    }
                    Text("Auto follows Deepgram's supported languages; Vietnamese requires a fixed choice. No ineffective language hints are sent. Settings apply to new work after processing finishes.").font(.caption).foregroundStyle(.secondary)
                    Button("Save settings securely") { save() }.disabled(!core.connected || core.busy || model.trimmingCharacters(in:.whitespaces).isEmpty)
                }
                Section("Permissions and call detection") {
                    Text("Microphone: \(text(core.discovery["permission"])) · Detection: \(text(core.discovery["status"]))").font(.callout)
                    HStack {
                        Button("Request microphone access") { core.send(["action":"microphone_permission"]) }.disabled(!core.connected || core.busy)
                        Button("Screen Recording settings") { if let url = URL(string:"x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture") { NSWorkspace.shared.open(url) } }
                    }
                    Button(core.detection ? "Pause call detection" : "Resume call detection") { core.send(["action":"set_detection","enabled":!core.detection]) }.disabled(!core.connected || core.activeID != nil)
                    Toggle("Open ScribeLoop at login",isOn:$login).onChange(of:login) { _, enabled in
                        do { if enabled { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
                            login = SMAppService.mainApp.status == .enabled
                            loginStatus = SMAppService.mainApp.status == .requiresApproval ? "Approve ScribeLoop in Login Items settings." : "Login opens ScribeLoop; recording still requires Record."
                        } catch { loginStatus = "Login setting could not be changed. Check System Settings → Login Items."; login = SMAppService.mainApp.status == .enabled }
                    }
                    if !loginStatus.isEmpty { Text(loginStatus).font(.caption) }
                }
                Section("Encrypted workspace") {
                    Text(core.database).font(.caption).textSelection(.enabled)
                    Text("Audio expires after 30 days unless archived. Notes and transcripts remain. An existing database is adopted in place; it is never copied or relocated.").font(.caption).foregroundStyle(.secondary)
                    Button("Use an existing database…") { core.chooseDatabase() }.disabled(core.busy)
                    Button("Revoke app agreement",role:.destructive) { core.send(["action":"revoke_agreement"]) }.disabled(!core.connected || !core.accepted)
                    HStack { Button("Reconnect") { core.reconnect() }.disabled(!core.running); Button("Restart core") { core.stop { core.start() } }.disabled(core.busy) }
                }
            }.formStyle(.grouped)
        }.onAppear {
            endpoint = text(core.settings["summary_endpoint"]); model = text(core.settings["summary_model"]); provider = text(core.settings["summary_provider"])
            if endpoint.isEmpty { endpoint = "https://api.openai.com/v1" }; if model.isEmpty { model = "gpt-5.4-mini" }; if provider.isEmpty { provider = "OpenAI" }
            if presets[provider]?.trimmingCharacters(in:CharacterSet(charactersIn:"/")) != endpoint.trimmingCharacters(in:CharacterSet(charactersIn:"/")) { provider = "Custom" }
            language = text(core.settings["language"]); if language.isEmpty { language = "auto" }; duration = core.settings["duration_s"] as? Int ?? 3600
        }.onDisappear { summaryKey = "" }
    }
    private func selectProvider(_ selected: String) {
        guard selected != provider else { return }
        provider = selected
        if let preset = presets[selected] { endpoint = preset }
        summaryKey = ""; clearSummary = false
        model = selected == "OpenAI" ? "gpt-5.4-mini" : ""
    }
    private func save() {
        var value: Object = ["summary_endpoint":endpoint,"summary_model":model,"summary_provider":provider,"language":language,"duration_s":duration]
        if !summaryKey.isEmpty || clearSummary { value["summary_key"] = clearSummary ? "" : summaryKey }
        core.send(["action":"save_settings","settings":value]); summaryKey = ""; clearSummary = false
    }
}

struct AudioView: View {
    @ObservedObject var core: DesktopClient
    @State private var rerun = false
    var audio: Object { core.detail["audio"] as? Object ?? [:] }
    var available: Bool { audio["available"] as? Bool == true }
    var body: some View {
        ScrollView {
            VStack(alignment:.leading,spacing:28) {
                Text("Listen to the conversation").font(.system(size:27,design:.serif))
                if available {
                    PlaybackBar(playback:core.playback,disabled:core.activeID != nil)
                    Text(audio["archived"] as? Bool == true ? "Archived · kept until you unarchive or delete it." : "Audio is encrypted on this Mac and expires after 30 days unless archived.").font(.callout).foregroundStyle(.secondary)
                    Button(audio["archived"] as? Bool == true ? "Unarchive audio" : "Archive audio") {
                        if let id = core.selectedID { core.send(["action":"archive_audio","session_id":id,"archived":audio["archived"] as? Bool != true]) }
                    }.disabled(core.busy)
                } else { Label("Audio is unavailable or expired. Transcript and notes are preserved.",systemImage:"waveform.slash").foregroundStyle(.secondary) }
                Divider()
                Text("Transcribe again").font(.headline)
                Text("Upload the saved recording to replace its transcript. Your current transcript remains available until the new pass finishes. Provider charges apply.").foregroundStyle(.secondary)
                Button("Transcribe again…") { rerun = true }.disabled(!available || core.busy || !core.accepted)
            }.padding(28).frame(maxWidth:850,alignment:.leading)
        }.task(id:available ? core.selectedID : nil) {
            if available, let id = core.selectedID { core.playback.open(id) } else { core.stopAudio() }
        }
        .onDisappear { core.stopAudio() }
        .confirmationDialog("Upload this saved audio and transcribe again?",isPresented:$rerun,titleVisibility:.visible) {
            Button("Upload and transcribe") { core.stopAudio(); if let id = core.selectedID { core.send(["action":"reprocess","session_id":id,"language":core.settings["language"] ?? "auto"]) } }
        }
    }
}

struct PlaybackBar: View {
    @ObservedObject var playback: MeetingPlayback
    var disabled: Bool
    @State private var scrubbing = false
    @State private var cursor = 0.0
    var body: some View {
        VStack(alignment:.leading,spacing:20) {
            HStack {
                Label("Saved recording",systemImage:"waveform").font(.headline)
                Spacer()
                if playback.loading { ProgressView().controlSize(.small); Text("Loading audio…").font(.caption).foregroundStyle(.secondary) }
            }
            Picker("Listen to",selection:Binding(get:{playback.track},set:{playback.chooseTrack($0)})) {
                Text("Conversation").tag("conversation")
                ForEach(playback.tracks,id:\.self) { track in Text(track == "mic" ? "Microphone" : "Meeting audio").tag(track) }
            }.pickerStyle(.segmented).labelsHidden().disabled(disabled)
            VStack(spacing:8) {
                Slider(value:$cursor,in:0...max(0.001,playback.duration),onEditingChanged:{ editing in
                    scrubbing = editing
                    if !editing { playback.seek(cursor) }
                }).disabled(disabled || playback.duration <= 0)
                    .accessibilityLabel("Recording position")
                    .accessibilityValue("\(stamp(cursor)) of \(stamp(playback.duration))")
                HStack { Text(stamp(scrubbing ? cursor : playback.position)); Spacer(); Text(stamp(playback.duration)) }.font(.caption).monospacedDigit().foregroundStyle(.secondary)
            }
            HStack(spacing:22) {
                Button { playback.seek(playback.position-15) } label: { Image(systemName:"gobackward.15").font(.title2) }.help("Back 15 seconds")
                Button { playback.toggle() } label: { Image(systemName:playback.active ? "pause.circle.fill" : "play.circle.fill").font(.system(size:46)) }
                    .buttonStyle(.plain).accessibilityLabel(playback.active ? "Pause recording playback" : "Play recording")
                Button { playback.seek(playback.position+15) } label: { Image(systemName:"goforward.15").font(.title2) }.help("Forward 15 seconds")
                Spacer()
                Picker("Speed",selection:$playback.rate) {
                    ForEach([Float(0.5),0.75,1,1.25,1.5,2],id:\.self) { value in Text(String(format:"%g×",value)).tag(value) }
                }.frame(width:150)
            }.disabled(disabled || playback.duration <= 0)
            if let failure = playback.failure { Label(failure,systemImage:"exclamationmark.triangle").foregroundStyle(.orange).font(.callout) }
            Text("Drag the timeline to any moment. Playback continues through the whole recording; audio stays on this Mac.").font(.caption).foregroundStyle(.secondary)
        }.padding(24).background(Color(nsColor:.controlBackgroundColor)).clipShape(RoundedRectangle(cornerRadius:16))
        .onChange(of:playback.position) { _, value in if !scrubbing { cursor = value } }
    }
    private func stamp(_ seconds: Double) -> String {
        let s = max(0,Int(seconds))
        return s >= 3600 ? String(format:"%d:%02d:%02d",s/3600,s/60%60,s%60) : String(format:"%d:%02d",s/60,s%60)
    }
}

struct TranscriptRow: View {
    @ObservedObject var core: DesktopClient
    let row: Record
    @State private var name = ""
    @State private var rename = false
    @State private var naming = false
    var body: some View {
        HStack(alignment:.top,spacing:20) {
            Text(String(format:"%d:%02d",row.int("start_ms")/60000,row.int("start_ms")/1000%60)).font(.caption).monospacedDigit().foregroundStyle(.secondary).frame(width:50,alignment:.leading)
            VStack(alignment:.leading,spacing:8) {
                HStack {
                    Text(row.text("stream") == "mic" ? "MICROPHONE" : "MEETING AUDIO").font(.caption2).tracking(1).foregroundStyle(.secondary)
                    Text(row.personTitle).font(.caption).bold()
                    if !row.text("display_speaker_id").isEmpty {
                        Menu {
                            Button("Rename speaker…") { name = row.text("speaker_label"); rename = true }
                            Menu("Merge into") {
                                ForEach(speakers.filter { $0.text("display_speaker_id") != row.text("display_speaker_id") }) { target in
                                    Button(target.text("speaker_label")) { correction("merge_speaker",extra:["merge_target":target.text("display_speaker_id")]) }
                                }
                            }
                            Button("Name this person…") { core.send(["action":"people_page"]); naming = true }

                        } label: { Image(systemName:"pencil") }.disabled(core.busy)
                    }
                }
                Text(verbatim:row.text("text")).textSelection(.enabled).lineSpacing(5)
            }
        }.sheet(isPresented:$naming) { PeopleView(core:core,initialSpeaker:row.text("display_speaker_id")).frame(width:650,height:650) }
        .alert("Name this speaker",isPresented:$rename) {
            TextField("Display name",text:$name)
            Button("Cancel",role:.cancel) {}
            Button("Save") { correction("rename_speaker",extra:["label":name]) }
        } message: { Text("A manual label applies to this transcript revision. It does not identify a voice across meetings.") }
    }
    var speakers: [Record] {
        var seen = Set<String>()
        return core.rows.filter { let id = $0.text("display_speaker_id"); return !id.isEmpty && seen.insert(id).inserted }
    }
    private func correction(_ action: String,extra: Object) {
        core.send(["action":action,"session_id":core.selectedID ?? "","revision":core.revision,"speaker_id":row.text("display_speaker_id")].merging(extra) { _, new in new })
    }
}

struct PeopleView: View {
    @ObservedObject var core: DesktopClient
    var initialSpeaker: String? = nil
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var speaker = ""
    @State private var existing = ""
    @State private var forget: Record?
    @State private var pending: String?
    @State private var saved = false
    var selected: Record? { core.speakerRoster.first { $0.id == speaker } }
    var body: some View {
        VStack(alignment:.leading,spacing:22) {
            HStack { Text("Who's speaking?").font(.system(size:29,design:.serif)); Spacer(); Button("Done") { dismiss() } }
            Text("Choose the person number shown in the transcript, then give them a name.").foregroundStyle(.secondary)
            if core.speakerRoster.isEmpty { Label("No numbered speakers in this transcript yet.",systemImage:"person.crop.circle.badge.questionmark").foregroundStyle(.secondary) }
            else {
                VStack(alignment:.leading,spacing:16) {
                    Picker("Transcript person",selection:$speaker) {
                        ForEach(core.speakerRoster) { row in Text(row.personTitle).tag(row.id) }
                    }
                    if let row = selected {
                        Text("\(row.text("stream") == "mic" ? "Microphone" : "Meeting audio") · “\(row.text("sample"))”").font(.callout).foregroundStyle(.secondary).textSelection(.enabled)
                    }
                    Picker("Use",selection:$existing) {
                        Text("New name").tag("")
                        ForEach(core.people) { row in Text(row.text("canonical_name")).tag(row.id) }
                    }
                    if existing.isEmpty { TextField("Person's name",text:$name,prompt:Text("Type their name, e.g. Alice Nguyen")).textFieldStyle(.roundedBorder) }
                    Button("Save person name") { save() }.buttonStyle(.borderedProminent)
                        .disabled(core.busy || pending != nil || speaker.isEmpty || (existing.isEmpty && name.trimmingCharacters(in:.whitespaces).isEmpty))
                    if saved { Label("Name saved to this transcript.",systemImage:"checkmark.circle.fill").foregroundStyle(.green) }
                }.padding(20).background(Color(nsColor:.controlBackgroundColor)).clipShape(RoundedRectangle(cornerRadius:12))
            }
            Text("Saved people").font(.headline)
            if core.people.isEmpty { Text("No saved people yet. Add a name above to link it directly to this transcript.").font(.callout).foregroundStyle(.secondary) }
            else { List(core.people) { person in HStack { Text(person.text("canonical_name")); Spacer(); Button("Forget…",role:.destructive) { forget = person }.disabled(core.busy || pending != nil) } }.frame(minHeight:80,maxHeight:160) }
            Spacer(minLength:0)
            HStack {
                if !core.people.isEmpty { Button("First page") { core.send(["action":"people_page"]) } }
                if let offset = core.nextPeopleOffset { Button("More people") { core.send(["action":"people_page","offset":offset]) } }
                Spacer(); Text("Manual names · no voice learning").font(.caption).foregroundStyle(.secondary)
            }
        }.padding(28)
        .onAppear { speaker = initialSpeaker ?? core.speakerRoster.first?.id ?? ""; core.send(["action":"people_page"]) }
        .onChange(of:speaker) { _, _ in name = ""; existing = ""; saved = false }
        .onChange(of:core.requests[pending ?? ""]) { _, status in
            if status == "accepted" { pending = nil; name = ""; saved = true }
            else if status == "rejected" || status == "unknown" { pending = nil; saved = false }
        }
        .alert("Forget this person?",isPresented:Binding(get:{forget != nil},set:{if !$0 { forget = nil }})) {
            Button("Cancel",role:.cancel) { forget = nil }
            Button("Forget",role:.destructive) { if let forget { core.send(["action":"forget_person","person_id":forget.id,"confirmed":true]) }; forget = nil }
        } message: { Text("Removes this person's links and learned data, and invalidates affected notes.") }
    }
    private func save() {
        guard let id = core.selectedID else { return }
        var command: Object = ["action":"name_speaker","session_id":id,"revision":core.revision,"speaker_id":speaker]
        if existing.isEmpty { command["name"] = name } else { command["person_id"] = existing }
        pending = core.send(command); saved = false
    }
}
