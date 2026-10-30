import SwiftUI
import AppKit
import ServiceManagement

struct Preferences: View {
    @ObservedObject var core: DesktopClient
    @Environment(\.dismiss) private var dismiss
    @State private var summaryKey = ""
    @State private var transcriptionKey = ""
    @State private var removeTranscriptionKey = false
    private enum Field: Hashable { case endpoint, model, apiKey, instructions, transcriptionKey }
    @FocusState private var focusedField: Field?
    @State private var endpoint = "https://api.openai.com/v1"
    @State private var model = "gpt-5.4-mini"
    @State private var provider = "OpenAI"
    @State private var instructions = ""
    private let defaultInstructions = """
    Summarize the meeting in the conversation's language. Write a concise topic title and overview, followed by key points, decisions, action items, open questions and uncertainties.

    Use only facts supported by the transcript and cite the relevant sources. Keep proposals separate from approved decisions. Preserve disagreements and uncertainty. Include owners and due dates only when explicitly stated; otherwise leave them unassigned. Do not infer dates from relative phrases or invent missing details. Omit unsupported items.
    """
    @State private var selectedLanguages: Set<String> = ["en","vi"]
    @State private var languageSearch = ""
    @State private var duration = 3600
    @State private var vocabulary = ""
    @State private var asrMicrophoneSource = "processed"
    @State private var finalTranscription = false
    private var vocabularyTerms: [String] {
        vocabulary.components(separatedBy:.newlines).map { $0.trimmingCharacters(in:.whitespaces) }.filter { !$0.isEmpty }
    }
    @State private var microphoneGain = 0.0
    @State private var microphoneFilter = "standard"
    @State private var microphoneThreshold = -40.0
    @State private var stopOnMicrophoneRelease = false
    @State private var initialValues = ""
    @State private var pendingSave: String?
    @State private var applying = false
    @State private var saveCommands: [Object] = []
    @State private var detectionDraft = true
    @State private var initialDetection = true
    @State private var initialLogin = false
    @State private var saveError: String?
    @State private var login = SMAppService.mainApp.status == .enabled
    @State private var loginStatus = ""
    @State private var revokeConfirmation = false
    private var presets: [String:String] { core.settings["summary_presets"] as? [String:String] ?? [:] }
    private var localSummary: Bool { provider == "Ollama" || provider == "LM Studio" }
    private var languageChoices: [(String,String)] {
        let names = core.settings["supported_language_hints"] as? [String:String] ?? ["en":"English","vi":"Tiếng Việt"]
        return names.map { ($0.key,$0.value) }.filter {
            languageSearch.isEmpty || ($0.0 + " " + $0.1 + ($0.0 == "vi" ? " Vietnamese" : "")).localizedCaseInsensitiveContains(languageSearch)
        }.sorted {
            let left = $0.0 == "en" ? 0 : $0.0 == "vi" ? 1 : 2
            let right = $1.0 == "en" ? 0 : $1.0 == "vi" ? 1 : 2
            return left == right ? $0.1.localizedCaseInsensitiveCompare($1.1) == .orderedAscending : left < right
        }
    }
    private var valuesSignature: String { [endpoint,model,provider,selectedLanguages.sorted().joined(separator:","),String(duration),instructions,String(Int(microphoneGain.rounded())),microphoneFilter,String(Int(microphoneThreshold.rounded())),String(stopOnMicrophoneRelease),vocabulary,asrMicrophoneSource,String(finalTranscription)].joined(separator:"\u{001f}") }
    private var providerChanged: Bool { valuesSignature != initialValues || !summaryKey.isEmpty || !transcriptionKey.isEmpty || removeTranscriptionKey }
    private var changed: Bool { providerChanged || detectionDraft != initialDetection || login != initialLogin }
    private var keyPreview: String {
        guard endpoint.trimmingCharacters(in:CharacterSet(charactersIn:"/")) == text(core.settings["summary_endpoint"]).trimmingCharacters(in:CharacterSet(charactersIn:"/")) else { return "" }
        return text(core.settings["summary_key_preview"])
    }
    var body: some View {
        VStack(alignment:.leading,spacing:12) {
            HStack(spacing:10) {
                ScribeMark(size:32)
                Text("Preferences").font(.system(size:26,design:.serif))
                Spacer()
                Button("Cancel") { cancel() }.keyboardShortcut(.cancelAction).disabled(applying)
                Button(applying ? "Saving…" : "Done") { finish() }
                    .buttonStyle(.borderedProminent).tint(ScribeTheme.action).keyboardShortcut(.defaultAction)
                    .disabled(applying || !core.connected || (changed && core.busy))
            }.padding(.horizontal,24).padding(.top,20)
            if let saveError { Label(saveError,systemImage:"exclamationmark.triangle").font(.callout).foregroundStyle(ScribeTheme.warning).padding(.horizontal,24) }
            if changed && core.busy { Text("Finish current processing before saving these changes.").font(.caption).foregroundStyle(.secondary).padding(.horizontal,24) }
            Form {
                Section("Transcription · Deepgram") {
                    HStack {
                        Text("Your Deepgram API key")
                        Spacer()
                        if core.settings["transcription_configured"] as? Bool == true && !removeTranscriptionKey {
                            Label("Saved",systemImage:"checkmark.circle.fill").font(.caption).foregroundStyle(ScribeTheme.accent)
                        }
                    }
                    SecureField("Deepgram API key",text:$transcriptionKey,prompt:Text(core.settings["transcription_configured"] as? Bool == true ? "Enter a new key to replace the saved key" : "Enter your Deepgram API key"))
                        .textFieldStyle(.roundedBorder).textContentType(nil).autocorrectionDisabled(true)
                        .focused($focusedField,equals:.transcriptionKey)
                        .accessibilityLabel("Deepgram API key")
                        .disabled(removeTranscriptionKey)
                    if core.settings["transcription_configured"] as? Bool == true {
                        Toggle("Remove saved transcription key",isOn:$removeTranscriptionKey)
                    }
                    Text("Stored in this Mac's Keychain. Leave unchanged to keep it. Audio is sent to Deepgram when you record or rerun transcription; your provider bills your account directly. This build does not include transcription credits or self-hosted ASR.").font(.caption).foregroundStyle(.secondary)
                }
                Section("Recognition accuracy") {
                    Picker("Microphone audio for transcription",selection:$asrMicrophoneSource) {
                        Text("Processed · gain and noise filter").tag("processed")
                        Text("Original · before gain and noise filter").tag("raw")
                    }
                    Text("Original sends the unprocessed microphone track to Deepgram. Playback keeps your microphone settings. Applies to new recordings and Transcribe again; older recordings need a retained original track.").font(.caption).foregroundStyle(.secondary)
                    Text("Names and vocabulary · one term per line").font(.callout.weight(.medium))
                    TextEditor(text:$vocabulary).frame(height:85).font(.body)
                        .accessibilityLabel("Transcription vocabulary")
                        .overlay { RoundedRectangle(cornerRadius:6).strokeBorder(ScribeTheme.border) }
                    Text("Add names, projects and technical terms you expect to hear. Up to 50 short terms; hints may improve recognition but do not force exact wording.").font(.caption).foregroundStyle(.secondary)
                    Toggle("Refine transcript after recording",isOn:$finalTranscription)
                    Text("Sends the recording to Deepgram one more time after Stop, then generates notes. This adds processing time and provider charges within the displayed audio cap. The live revision is retained. If refinement fails, notes use the live transcript.").font(.caption).foregroundStyle(.secondary)
                }
                Section("Conversation notes") {
                    Picker("Provider",selection:Binding(get:{provider},set:selectProvider)) {
                        Text("OpenAI · Cloud").tag("OpenAI")
                        Text("Gemini · Cloud").tag("Gemini")
                        Text("Ollama · Local").tag("Ollama")
                        Text("LM Studio · Local").tag("LM Studio")
                        Text("Custom · OpenAI compatible").tag("Custom")
                    }
                    if provider == "Custom" { TextField("API base URL",text:$endpoint).focused($focusedField,equals:.endpoint) }
                    VStack(alignment:.leading,spacing:8) {
                        Text("Model ID").font(.callout).foregroundStyle(.secondary).onTapGesture { focusedField = .model }
                        TextField("Model ID",text:$model,prompt:Text(localSummary ? "Type your installed model name" : "Type the model ID from your provider"))
                            .labelsHidden().textFieldStyle(.roundedBorder).controlSize(.large)
                            .focused($focusedField,equals:.model).autocorrectionDisabled(true)
                            .simultaneousGesture(TapGesture().onEnded {
                                NSApp.setActivationPolicy(.regular)
                                NSApp.activate(ignoringOtherApps:true)
                                focusedField = .model
                            })
                            .accessibilityLabel("Model ID").frame(maxWidth:.infinity)
                    }.padding(.vertical,4)
                    VStack(alignment:.leading,spacing:8) {
                        HStack { Text(localSummary ? "API key (optional)" : "API key").font(.callout).foregroundStyle(.secondary)
                            Spacer()
                            if !keyPreview.isEmpty { Label("Saved",systemImage:"checkmark.circle.fill").font(.caption).foregroundStyle(ScribeTheme.accent) }
                        }
                        SecureField("API key",text:$summaryKey,prompt:Text(keyPreview.isEmpty ? "Enter your API key" : keyPreview))
                            .labelsHidden().textFieldStyle(.roundedBorder).controlSize(.large).textContentType(nil).autocorrectionDisabled(true)
                            .focused($focusedField,equals:.apiKey)
                            .accessibilityLabel(keyPreview.isEmpty ? "API key" : "Replace saved API key")
                    }.padding(.vertical,4)
                    Text(localSummary ? "Connects to your running local model server. A key is optional unless the server requires one." : "Your key is stored securely. Leave this field unchanged to keep it, or enter a new key to replace it.").font(.caption).foregroundStyle(.secondary)
                    VStack(alignment:.leading,spacing:8) {
                        Text("Summary instructions").font(.callout.weight(.medium))
                        TextEditor(text:$instructions).font(.body)
                            .focused($focusedField,equals:.instructions)
                            .frame(minHeight:110,maxHeight:160).padding(6)
                            .background(ScribeTheme.canvas,in:RoundedRectangle(cornerRadius:8))
                            .overlay { RoundedRectangle(cornerRadius:8).strokeBorder(ScribeTheme.border) }
                            .accessibilityLabel("Summary instructions")
                        Text("Edit this guidance to change the focus, style or detail of your notes. Clearing it restores default guidance. Applies when notes are generated again; citations and factual checks stay required.").font(.caption).foregroundStyle(.secondary)
                        Text("\(instructions.count)/2,000 characters").font(.caption).foregroundStyle(instructions.count>2000 ? ScribeTheme.warning : .secondary)
                    }.padding(.vertical,4)
                }
                Section("Microphone") {
                    VStack(alignment:.leading,spacing:8) {
                        HStack {
                            Text("Input sensitivity")
                            Spacer()
                            Text(String(format:"%+.0f dB",microphoneGain)).monospacedDigit().foregroundStyle(.secondary)
                        }
                        Slider(value:$microphoneGain,in:-12...24,step:1)
                            .accessibilityLabel("Microphone input sensitivity")
                            .accessibilityValue(String(format:"%+.0f decibels",microphoneGain))
                        HStack { Text("Lower"); Spacer(); Text("Higher") }.font(.caption).foregroundStyle(.secondary)
                    }.padding(.vertical,4)
                    Picker("Background noise filter",selection:$microphoneFilter) {
                        Text("Off").tag("off")
                        Text("Standard").tag("standard")
                        Text("Strong").tag("strong")
                    }
                    if microphoneFilter != "off" {
                        VStack(alignment:.leading,spacing:8) {
                            HStack { Text("Noise rejection threshold"); Spacer(); Text(String(format:"%.0f dBFS",microphoneThreshold)).monospacedDigit().foregroundStyle(.secondary) }
                            Slider(value:$microphoneThreshold,in:-65 ... -25,step:1)
                                .accessibilityLabel("Microphone noise rejection threshold")
                                .accessibilityValue(String(format:"%.0f decibels",microphoneThreshold))
                            Text("Raise to reject more background sound; lower to keep quieter speech.").font(.caption).foregroundStyle(.secondary)
                        }.padding(.vertical,4)
                    }
                    Text("Sensitivity boosts your voice and background sound. The filter reduces rumble and quiet background noise; it cannot isolate voices or remove every sudden sound. Strong may soften quiet speech.").font(.caption).foregroundStyle(.secondary)
                    Text("Applies to new recordings and processed-audio transcription. An encrypted original microphone track is kept when processing is enabled. Existing recordings stay as recorded.").font(.caption).foregroundStyle(.secondary)
                    Button("Reset microphone settings") { microphoneGain = 0; microphoneFilter = "standard"; microphoneThreshold = -40 }
                }
                Section("Recording preset") {
                    Picker("Maximum duration",selection:$duration) { Text("5 minutes · $0.25 audio cap").tag(300); Text("1 hour · $3 audio cap").tag(3600); Text("3 hours · $8 audio cap").tag(10800) }
                    VStack(alignment:.leading,spacing:10) {
                        HStack {
                            Text("Conversation languages").font(.callout.weight(.medium))
                            Spacer()
                            Text("\(selectedLanguages.count) selected").font(.caption).foregroundStyle(.secondary)
                        }
                        TextField("Search languages",text:$languageSearch).textFieldStyle(.roundedBorder)
                            .accessibilityLabel("Search conversation languages")
                        ScrollView {
                            LazyVGrid(columns:[GridItem(.flexible(),alignment:.leading),GridItem(.flexible(),alignment:.leading)],alignment:.leading,spacing:10) {
                                ForEach(languageChoices,id:\.0) { code, name in
                                    Toggle(name,isOn:Binding(
                                        get:{ selectedLanguages.contains(code) },
                                        set:{ selected in
                                            if selected { selectedLanguages.insert(code) }
                                            else if selectedLanguages.count > 1 { selectedLanguages.remove(code) }
                                        }))
                                        .toggleStyle(.checkbox)
                                        .disabled(selectedLanguages.count == 1 && selectedLanguages.contains(code))
                                }
                            }.padding(.vertical,4)
                        }.frame(height:210)
                        if languageChoices.isEmpty { Text("No matching languages").font(.caption).foregroundStyle(.secondary) }
                        Button("Reset to English and Vietnamese") { selectedLanguages = ["en","vi"]; languageSearch = "" }
                    }
                    Text("Select the languages you expect to hear. Keep at least one selected. Deepgram uses your selection as hints and supports switching languages. Hints guide recognition; accuracy can vary. Applies to new recordings and manual transcription reruns.").font(.caption).foregroundStyle(.secondary)
                }
                Section("Permissions and call detection") {
                    HStack(spacing:12) {
                        Image(systemName:"mic").font(.title3).foregroundStyle(ScribeTheme.accent).frame(width:24)
                        VStack(alignment:.leading,spacing:4) {
                            Text("Microphone").font(.callout.weight(.medium))
                            Text(text(core.discovery["permission"]) == "authorized" ? "Ready to record your voice" : "Allow access to record your voice").font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button(text(core.discovery["permission"]) == "authorized" ? "Allowed" : "Allow access") { core.send(["action":"microphone_permission"]) }
                            .disabled(!core.connected || core.busy || text(core.discovery["permission"]) == "authorized")
                    }.padding(.vertical,5)
                    HStack(spacing:12) {
                        Image(systemName:"speaker.wave.2").font(.title3).foregroundStyle(ScribeTheme.accent).frame(width:24)
                        VStack(alignment:.leading,spacing:4) {
                            Text("Meeting audio").font(.callout.weight(.medium))
                            Text("Capture sound from call apps and browsers").font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button("Open settings") { if let url = URL(string:"x-apple.systempreferences:com.apple.preference.security?Privacy_ScreenCapture") { NSWorkspace.shared.open(url) } }
                    }.padding(.vertical,5)
                    Button("Check capture access without recording") {
                        core.captureAccessMessage = "Checking access without recording…"
                        if core.send(["action":"check_capture_access"]) == nil { core.captureAccessMessage = core.message }
                    }
                        .disabled(!core.connected || core.busy)
                    if let result = core.captureAccessMessage { Text(result).font(.caption).foregroundStyle(.secondary) }
                    Toggle(isOn:$detectionDraft) {
                        Label("Detect calls automatically",systemImage:"phone.badge.waveform")
                    }.toggleStyle(.switch).disabled(!core.connected || core.busy)
                    Toggle("Stop after meeting app releases microphone",isOn:$stopOnMicrophoneRelease)
                    Text("After 60 seconds without the meeting app's microphone activity, stop even if browser audio continues. The countdown offers Keep recording. A muted or receive-only call can also release its microphone; leave off to wait until all selected-app audio is inactive. Applies to meeting-app recordings, not microphone-only or all-system recordings.").font(.caption).foregroundStyle(.secondary)
                    Toggle("Open ScribeLoop at login",isOn:$login)
                    if !loginStatus.isEmpty { Text(loginStatus).font(.caption) }
                }
                if core.accepted {
                    Section("Cloud processing") {
                        Text("Stop recording and processing, and require the first-use agreement again. Saved conversations remain on this Mac.").font(.caption).foregroundStyle(.secondary)
                        Button("Stop cloud processing…",role:.destructive) { revokeConfirmation = true }
                            .disabled(!core.connected)
                    }
                }
            }.formStyle(.grouped).disabled(applying)
        }.background(ScribeTheme.canvas).tint(ScribeTheme.accent).background(InteractiveWindowActivation())
        .background(OutsideClickFocusDismissal(dismissFocus:{ focusedField = nil },
            cancelOnOutsideModal:applying ? nil : { cancel() }).allowsHitTesting(false)).onAppear {
            guard core.connected else { dismiss(); return }
            endpoint = text(core.settings["summary_endpoint"])
            model = text(core.settings["summary_model"])
            provider = text(core.settings["summary_provider"])
            instructions = text(core.settings["summary_instructions"])
            if instructions.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty { instructions = defaultInstructions }
            if endpoint.isEmpty { endpoint = "https://api.openai.com/v1" }
            if model.isEmpty { model = "gpt-5.4-mini" }
            if provider.isEmpty { provider = "OpenAI" }
            if presets[provider]?.trimmingCharacters(in:CharacterSet(charactersIn:"/")) != endpoint.trimmingCharacters(in:CharacterSet(charactersIn:"/")) { provider = "Custom" }
            selectedLanguages = Set(core.settings["language_hints"] as? [String] ?? ["en","vi"])
            if selectedLanguages.isEmpty { selectedLanguages = ["en","vi"] }
            languageSearch = ""
            duration = core.settings["duration_s"] as? Int ?? 3600
            vocabulary = (core.settings["vocabulary"] as? [String] ?? []).joined(separator:"\n")
            asrMicrophoneSource = core.settings["asr_microphone_source"] as? String ?? "processed"
            finalTranscription = core.settings["final_transcription"] as? Bool ?? false
            microphoneGain = (core.settings["microphone_gain_db"] as? NSNumber)?.doubleValue ?? 0
            microphoneFilter = text(core.settings["microphone_noise_filter"])
            if microphoneFilter.isEmpty { microphoneFilter = "standard" }
            microphoneThreshold = (core.settings["microphone_noise_threshold_db"] as? NSNumber)?.doubleValue ?? -40
            stopOnMicrophoneRelease = core.settings["stop_on_microphone_release"] as? Bool ?? false
            initialValues = valuesSignature
            initialDetection = core.detection; detectionDraft = initialDetection
            initialLogin = SMAppService.mainApp.status == .enabled; login = initialLogin
            summaryKey = ""; transcriptionKey = ""; removeTranscriptionKey = false; saveError = nil; pendingSave = nil; applying = false; saveCommands = []
            loginStatus = SMAppService.mainApp.status == .requiresApproval ? "Approve ScribeLoop in Login Items settings." : ""
        }.onChange(of:core.requests[pendingSave ?? ""]) { status in
            guard pendingSave != nil else { return }
            if status == "accepted" { pendingSave = nil; saveNext() }
            else if status == "rejected" || status == "unknown" {
                pendingSave = nil; applying = false; saveCommands = []
                saveError = status == "unknown" ? "Save status is uncertain. Reconnect and check your settings before retrying." : "Settings could not be saved. Check the model and endpoint, then try Done again."
            }
        }.interactiveDismissDisabled(applying)
        .alert("Stop cloud processing?",isPresented:$revokeConfirmation) {
            Button("Cancel",role:.cancel) {}
            Button("Stop processing",role:.destructive) { core.send(["action":"revoke_agreement"]) }
        } message: { Text("Recording and pending notes will stop. Data already sent to providers cannot be recalled. Unsaved settings will be discarded; saved conversations remain.") }
        .onChange(of:core.accepted) { accepted in if !accepted { cancel() } }
        .onDisappear { summaryKey = ""; transcriptionKey = "" }
    }
    private func selectProvider(_ selected: String) {
        guard selected != provider else { return }
        provider = selected
        if let preset = presets[selected] { endpoint = preset }
        summaryKey = ""; saveError = nil
        model = selected == "OpenAI" ? "gpt-5.4-mini" : ""
        focusedField = .model
    }
    private func cancel() {
        guard !applying else { return }
        focusedField = nil; summaryKey = ""; transcriptionKey = ""; dismiss()
    }
    private func finish() {
        focusedField = nil
        guard !applying else { return }
        guard changed else { dismiss(); return }
        guard !model.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty else { saveError = "Enter a model ID before saving."; return }
        guard vocabularyTerms.count <= 50, vocabularyTerms.allSatisfy({ $0.count <= 80 }), vocabularyTerms.reduce(0,{ $0 + $1.utf8.count }) <= 500 else {
            saveError = "Keep vocabulary to a shorter list of important names and terms."; return
        }
        guard instructions.count <= 2000 else { saveError = "Keep summary instructions within 2,000 characters."; return }
        guard core.connected, !core.busy else { saveError = "Reconnect and wait for processing to finish before saving."; return }
        saveError = nil; saveCommands = []
        if providerChanged {
            var value: Object = ["summary_endpoint":endpoint,"summary_model":model,"summary_provider":provider,"summary_instructions":instructions,"language":"auto","language_hints":selectedLanguages.sorted(),"duration_s":duration,"microphone_gain_db":microphoneGain,"microphone_noise_filter":microphoneFilter,"microphone_noise_threshold_db":microphoneThreshold]
            if !summaryKey.isEmpty { value["summary_key"] = summaryKey }
            value["stop_on_microphone_release"] = stopOnMicrophoneRelease
            value["vocabulary"] = vocabularyTerms
            value["asr_microphone_source"] = asrMicrophoneSource
            value["final_transcription"] = finalTranscription
            if removeTranscriptionKey { value["deepgram_key"] = "" }
            else if !transcriptionKey.isEmpty { value["deepgram_key"] = transcriptionKey }
            saveCommands.append(["action":"save_settings","settings":value])
        }
        if detectionDraft != initialDetection { saveCommands.append(["action":"set_detection","enabled":detectionDraft]) }
        applying = true; saveNext()
    }
    private func saveNext() {
        if !saveCommands.isEmpty {
            pendingSave = core.send(saveCommands.removeFirst())
            if pendingSave == nil {
                applying = false; saveCommands = []
                saveError = "Settings could not be sent. Your changes are still here; reconnect and try Done again."
            }
            return
        }
        if login != initialLogin {
            do {
                if login { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
                if SMAppService.mainApp.status == .requiresApproval { core.message = "Approve ScribeLoop in Login Items settings to open it at login." }
            } catch {
                applying = false
                initialValues = valuesSignature; initialDetection = detectionDraft
                summaryKey = ""; transcriptionKey = ""; removeTranscriptionKey = false
                saveError = "Other settings were saved, but the login setting could not be changed. Check System Settings → Login Items."
                return
            }
        }
        applying = false; summaryKey = ""; transcriptionKey = ""; dismiss()
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
                HStack { Label("SAVED AUDIO",systemImage:"waveform").font(.system(size:10,weight:.semibold)).tracking(1.1); Spacer(); Label("Encrypted on this Mac",systemImage:"lock").font(.caption) }.foregroundStyle(.secondary)
                if available {
                    PlaybackBar(playback:core.playback,disabled:core.activeID != nil) {
                        if core.playback.duration > 0 { core.playback.toggle() }
                        else if let id = core.selectedID { core.playback.open(id,autoplay:true) }
                    }
                    Text(audio["archived"] as? Bool == true ? "Archived · kept until you unarchive or delete it." : "Audio is encrypted on this Mac and expires after 30 days unless archived.").font(.callout).foregroundStyle(.secondary)
                    Button(audio["archived"] as? Bool == true ? "Unarchive audio" : "Archive audio") {
                        if let id = core.selectedID { core.send(["action":"archive_audio","session_id":id,"archived":audio["archived"] as? Bool != true]) }
                    }.disabled(core.busy)
                } else { Label("Audio is unavailable or expired. Transcript and notes are preserved.",systemImage:"waveform.slash").foregroundStyle(.secondary) }
                VStack(alignment:.leading,spacing:10) {
                    Label("Transcribe again",systemImage:"arrow.clockwise").font(.headline)
                    Text("Process the complete saved recording in one upload. Your current transcript stays available until the new version is ready. Provider charges apply.").font(.callout).foregroundStyle(.secondary)
                    Button("Transcribe again…") { rerun = true }.disabled(!available || core.busy || !core.accepted)
                }.notebookCard()
            }.padding(28).frame(maxWidth:850,alignment:.leading)
        }
        .onDisappear { core.stopAudio() }
        .confirmationDialog("Upload this saved audio and transcribe again?",isPresented:$rerun,titleVisibility:.visible) {
            Button("Upload and transcribe") { core.stopAudio(); if let id = core.selectedID { core.send(["action":"reprocess","session_id":id,"language":core.settings["language"] ?? "auto","language_hints":core.settings["language_hints"] ?? ["en","vi"]]) } }
        }
    }
}

struct PlaybackBar: View {
    @ObservedObject var playback: MeetingPlayback
    var disabled: Bool
    var onPlay: () -> Void
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
                HStack { Text(stamp(scrubbing ? cursor : playback.position)); Spacer(); Text(playback.duration > 0 ? stamp(playback.duration) : "—:—") }.font(.caption).monospacedDigit().foregroundStyle(.secondary)
            }
            HStack(spacing:22) {
                Button { playback.seek(playback.position-15) } label: { Image(systemName:"gobackward.15").font(.title2) }.help("Back 15 seconds").disabled(playback.duration <= 0)
                Button { onPlay() } label: { Image(systemName:playback.active ? "pause.circle.fill" : "play.circle.fill").font(.system(size:46)) }
                    .buttonStyle(.plain).accessibilityLabel(playback.active ? "Pause recording playback" : "Play recording")
                    .disabled(playback.loading)
                Button { playback.seek(playback.position+15) } label: { Image(systemName:"goforward.15").font(.title2) }.help("Forward 15 seconds").disabled(playback.duration <= 0)
                Spacer()
                Picker("Speed",selection:$playback.rate) {
                    ForEach([Float(0.5),0.75,1,1.25,1.5,2],id:\.self) { value in Text(String(format:"%g×",value)).tag(value) }
                }.frame(width:150)
            }.disabled(disabled)
            if let failure = playback.failure { Label(failure,systemImage:"exclamationmark.triangle").foregroundStyle(.orange).font(.callout) }
            Text(playback.duration > 0 ? "Drag the timeline to any moment. Playback continues through the whole recording; audio stays on this Mac." : "Press Play to load this recording. Browsing your meetings does not unlock audio.").font(.caption).foregroundStyle(.secondary)
        }.notebookCard()
        .onChange(of:playback.position) { value in if !scrubbing { cursor = value } }
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
            Text(String(format:"%d:%02d",row.int("start_ms")/60000,row.int("start_ms")/1000%60)).font(.system(size:11,weight:.medium)).monospacedDigit().foregroundStyle(.secondary).padding(.vertical,5).frame(width:50,alignment:.leading)
            VStack(alignment:.leading,spacing:8) {
                HStack {
                    Text(row.personTitle).font(.system(size:11,weight:.semibold)).foregroundStyle(ScribeTheme.accent).padding(.horizontal,8).padding(.vertical,5).background(ScribeTheme.subtle,in:Capsule())
                    Text(row.text("stream") == "mic" ? "Microphone" : "Meeting audio").font(.system(size:10)).foregroundStyle(.secondary)
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
        }.padding(28).background(InteractiveWindowActivation())
        .onAppear { speaker = initialSpeaker ?? core.speakerRoster.first?.id ?? ""; core.send(["action":"people_page"]) }
        .onChange(of:speaker) { _ in name = ""; existing = ""; saved = false }
        .onChange(of:core.requests[pending ?? ""]) { status in
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

struct RenameConversation: View {
    @ObservedObject var core: DesktopClient
    let conversation: Record
    @Environment(\.dismiss) private var dismiss
    @FocusState private var focused: Bool
    @State private var title = ""
    @State private var pending: String?
    @State private var failure: String?
    var body: some View {
        VStack(alignment:.leading,spacing:20) {
            Text("Rename conversation").font(.system(size:27,design:.serif))
            TextField("Conversation name",text:$title).textFieldStyle(.roundedBorder)
                .controlSize(.large).focused($focused).onSubmit { save() }
            HStack {
                Text("Custom titles are kept when notes are regenerated.")
                Spacer(); Text("\(title.count)/160").monospacedDigit()
            }.font(.caption).foregroundStyle(.secondary)
            if let failure { Label(failure,systemImage:"exclamationmark.triangle").font(.callout).foregroundStyle(ScribeTheme.warning) }
            HStack {
                Button("Cancel") { dismiss() }.keyboardShortcut(.cancelAction).disabled(pending != nil)
                Spacer()
                Button(pending == nil ? "Save name" : "Saving…") { save() }
                    .buttonStyle(.borderedProminent).tint(ScribeTheme.action).keyboardShortcut(.defaultAction)
                    .disabled(!valid || core.busy || !core.connected || pending != nil)
            }
        }.padding(28).frame(width:520).background(ScribeTheme.canvas)
        .background(InteractiveWindowActivation()).interactiveDismissDisabled(pending != nil)
        .onAppear { title = conversation.text("title"); focused = true }
        .onChange(of:core.requests[pending ?? ""]) { status in
            guard pending != nil else { return }
            if status == "accepted" { pending = nil; dismiss() }
            else if status == "rejected" || status == "unknown" {
                pending = nil; failure = "The name could not be confirmed. Reopen the conversation to check its current name before retrying."
            }
        }
    }
    private var valid: Bool {
        !title.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty && title.count<=160 &&
        !title.unicodeScalars.contains { $0.value<32 || $0.value==127 }
    }
    private func save() {
        guard valid, !core.busy, core.connected, pending == nil else { return }
        if title == conversation.text("title") { dismiss(); return }
        failure = nil
        pending = core.send(["action":"rename_conversation","session_id":conversation.id,"title":title])
        if pending == nil { failure = "Reconnect before saving. Your name is still here." }
    }
}
