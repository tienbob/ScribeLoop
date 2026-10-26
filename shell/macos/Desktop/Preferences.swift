import SwiftUI
import AppKit
import ServiceManagement

struct Preferences: View {
    @ObservedObject var core: DesktopClient
    @Environment(\.dismiss) private var dismiss
    @State private var deepgram = ""
    @State private var summaryKey = ""
    @State private var endpoint = "https://api.openai.com/v1"
    @State private var model = "gpt-5.4-mini"
    @State private var provider = "OpenAI"
    @State private var language = "auto"
    @State private var duration = 3600
    @State private var clearDeepgram = false
    @State private var clearSummary = false
    @State private var login = SMAppService.mainApp.status == .enabled
    @State private var loginStatus = ""
    private var presets: [String:String] { core.settings["summary_presets"] as? [String:String] ?? [:] }
    private var localSummary: Bool { provider == "Ollama" || provider == "LM Studio" }
    var body: some View {
        VStack(alignment:.leading,spacing:12) {
            HStack { Text("Preferences").font(.system(size:28,design:.serif)); Spacer(); Button("Done") { dismiss() }.keyboardShortcut(.cancelAction) }.padding(.horizontal,24).padding(.top,20)
            Form {
                Section("Cloud transcription · Deepgram Nova-3") {
                    Text(core.settings["deepgram_configured"] as? Bool == true ? "API key saved in Keychain. Leave the field blank to keep it." : "Add your Deepgram API key. Saving makes no provider request.").font(.caption)
                    SecureField("New API key",text:$deepgram).textContentType(.password)
                    Toggle("Remove saved transcription key",isOn:$clearDeepgram)
                }
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
        }.onDisappear { deepgram = ""; summaryKey = "" }
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
        if !deepgram.isEmpty || clearDeepgram { value["deepgram_key"] = clearDeepgram ? "" : deepgram }
        if !summaryKey.isEmpty || clearSummary { value["summary_key"] = clearSummary ? "" : summaryKey }
        core.send(["action":"save_settings","settings":value]); deepgram = ""; summaryKey = ""; clearDeepgram = false; clearSummary = false
    }
}

struct AudioView: View {
    @ObservedObject var core: DesktopClient
    @State private var stream = "mic"
    @State private var minute = 0
    @State private var rerun = false
    var audio: Object { core.detail["audio"] as? Object ?? [:] }
    var available: Bool { audio["available"] as? Bool == true }
    var body: some View {
        Form {
            Section("Encrypted original") {
                Text(available ? "Audio is available locally." : "Audio is unavailable or expired. Transcript and notes are preserved.")
                Text(audio["archived"] as? Bool == true ? "Archived · kept until you unarchive or delete it." : "Audio expires 30 days after recording unless archived.").font(.callout).foregroundStyle(.secondary)
                Picker("Track",selection:$stream) { ForEach(audio["tracks"] as? [String] ?? ["mic"],id:\.self) { value in Text(value == "mic" ? "Microphone" : "Meeting audio").tag(value) } }
                Stepper("Play from minute \(minute)",value:$minute,in:0...179)
                HStack {
                    Button(core.playing ? "Stop playback" : "Play 5-minute window") {
                        if core.playing { core.stopAudio() }
                        else if let id = core.selectedID { core.send(["action":"play_audio","session_id":id,"stream":stream,"start_ms":minute*60000]) }
                    }.disabled(!available || core.busy)
                    Button(audio["archived"] as? Bool == true ? "Unarchive audio" : "Archive audio") {
                        if let id = core.selectedID { core.send(["action":"archive_audio","session_id":id,"archived":audio["archived"] as? Bool != true]) }
                    }.disabled(!available || core.busy)
                }
                Text("Playback is decoded in memory and stays on this Mac. Only one five-minute audio window is retained.").font(.caption)
            }
            Section("Manual rerun") {
                Text("Transcribe again uploads the retained audio to Deepgram. The existing transcript stays available until the replacement finishes. Provider charges apply; the original recording preset's budget remains in force.")
                Button("Transcribe again…") { rerun = true }.disabled(!available || core.busy || !core.accepted)
            }
        }.formStyle(.grouped).onAppear { stream = (audio["tracks"] as? [String])?.first ?? "mic" }
        .onDisappear { core.stopAudio() }
        .confirmationDialog("Upload this saved audio and transcribe again?",isPresented:$rerun,titleVisibility:.visible) {
            Button("Upload and transcribe") { if let id = core.selectedID { core.send(["action":"reprocess","session_id":id,"language":core.settings["language"] ?? "auto"]) } }
        }
    }
}

struct TranscriptRow: View {
    @ObservedObject var core: DesktopClient
    let row: Record
    @State private var name = ""
    @State private var rename = false
    var body: some View {
        HStack(alignment:.top,spacing:20) {
            Text(String(format:"%d:%02d",row.int("start_ms")/60000,row.int("start_ms")/1000%60)).font(.caption).monospacedDigit().foregroundStyle(.secondary).frame(width:50,alignment:.leading)
            VStack(alignment:.leading,spacing:8) {
                HStack {
                    Text(row.text("stream") == "mic" ? "MICROPHONE" : "MEETING AUDIO").font(.caption2).tracking(1).foregroundStyle(.secondary)
                    Text(row.text("speaker_label").isEmpty ? "Speaker unknown" : row.text("speaker_label")).font(.caption).bold()
                    if !row.text("display_speaker_id").isEmpty {
                        Menu {
                            Button("Rename speaker…") { name = row.text("speaker_label"); rename = true }
                            Menu("Merge into") {
                                ForEach(speakers.filter { $0.text("display_speaker_id") != row.text("display_speaker_id") }) { target in
                                    Button(target.text("speaker_label")) { correction("merge_speaker",extra:["merge_target":target.text("display_speaker_id")]) }
                                }
                            }
                            Menu("Link to person") {
                                ForEach(core.people) { person in Button(person.text("canonical_name")) {
                                    core.send(["action":"link_person","session_id":core.selectedID ?? "","revision":core.revision,"speaker_id":row.text("display_speaker_id"),"person_id":person.id])
                                } }
                            }
                            Button("Load people") { core.send(["action":"people_page"]) }
                        } label: { Image(systemName:"pencil") }.disabled(core.busy)
                    }
                }
                Text(verbatim:row.text("text")).textSelection(.enabled).lineSpacing(5)
            }
        }.alert("Name this speaker",isPresented:$rename) {
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
    @Environment(\.dismiss) private var dismiss
    @State private var name = ""
    @State private var forget: Record?
    var body: some View {
        VStack(alignment:.leading,spacing:20) {
            HStack { Text("People").font(.title); Spacer(); Button("Done") { dismiss() } }
            Text("Link names to saved transcript speakers. Voice recognition remains unavailable until its separate calibration is validated.").font(.callout).foregroundStyle(.secondary)
            HStack { TextField("Person's name",text:$name); Button("Add person") { core.send(["action":"create_person","name":name]); name = "" }.disabled(name.trimmingCharacters(in:.whitespaces).isEmpty || core.busy) }
            List(core.people) { person in HStack { Text(person.text("canonical_name")); Spacer(); Button("Forget…",role:.destructive) { forget = person }.disabled(core.busy) } }
            HStack {
                Button("First page") { core.send(["action":"people_page"]) }
                if let offset = core.nextPeopleOffset { Button("More people") { core.send(["action":"people_page","offset":offset]) } }
            }
        }.padding(28)
        .alert("Forget this person?",isPresented:Binding(get:{forget != nil},set:{if !$0 { forget = nil }})) {
            Button("Cancel",role:.cancel) { forget = nil }
            Button("Forget",role:.destructive) { if let forget { core.send(["action":"forget_person","person_id":forget.id,"confirmed":true]) }; forget = nil }
        } message: { Text("Removes this person's links and learned data, and invalidates affected notes.") }
    }
}
