import SwiftUI
import AppKit
import ServiceManagement

struct Preferences: View {
    @ObservedObject var core: DesktopClient
    @ObservedObject var calendar:CalendarCoordinator
    var initialPage = "Recording"
    @Environment(\.dismiss) private var dismiss
    @State private var automaticNotes = true
    @State private var summaryKey = ""
    @State private var assemblyKey = ""
    @State private var removeAssemblyKey = false
    @State private var transcriptionKey = ""
    @State private var removeTranscriptionKey = false
    private enum Field: Hashable { case endpoint, model, apiKey, instructions, transcriptionKey, localModel, cleanupEndpoint, cleanupModel, cleanupKey }
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
    @State private var transcriptionEngine = "deepgram"
    @State private var localModel = ""
    @State private var localSpeakerLabels = false
    @State private var localTranscriptCleanup = true
    @State private var cleanupModel = ""
    @State private var cleanupProvider = "Ollama"
    @State private var cleanupEndpoint = ""
    @State private var cleanupKey = ""
    @State private var cleanupValidate = true
    private var vocabularyTerms: [String] {
        vocabulary.components(separatedBy:.newlines).map { $0.trimmingCharacters(in:.whitespaces) }.filter { !$0.isEmpty }
    }
    @State private var microphoneGain = 0.0
    @State private var microphoneFilter = "standard"
    @State private var microphoneThreshold = -40.0
    @State private var stopOnMicrophoneRelease = false
    @State private var saveProgress = PreferencesSaveProgress()
    @State private var pendingSaveCommand:Object?
    @State private var pendingSave: String?
    @State private var applying = false
    @State private var saveCommands: [Object] = []
    @State private var detectionDraft = true
    @State private var initialLogin = false
    @State private var saveError: String?
    @State private var login = SMAppService.mainApp.status == .enabled
    @State private var loginStatus = ""
    @State private var page = "Recording"
    @State private var advancedRecognition = false
    @State private var advancedMicrophone = false
    private var presets: [String:String] { core.settings["summary_presets"] as? [String:String] ?? [:] }
    private var savedKeyPreviews: [String:String] { core.settings["summary_key_previews"] as? [String:String] ?? [:] }
    private var cleanupKeyPreviews: [String:String] { core.settings["local_cleanup_key_previews"] as? [String:String] ?? [:] }
    private var savedCleanupModels: [String:String] { core.settings["local_cleanup_models"] as? [String:String] ?? [:] }
    private var savedSummaryModels: [String:String] { core.settings["summary_models"] as? [String:String] ?? [:] }
    private var cleanupIsCloud: Bool { cleanupProvider != "Ollama" && cleanupProvider != "LM Studio" }
    private var localSummary: Bool { provider == "Ollama" || provider == "LM Studio" }
    private let assemblyLanguages = Set("af ar yue ca da nl en et fi fr gl de he hi it ja ko zh mr no nn fa pt ro ru es sv tr ur vi xh zu".split(separator:" ").map(String.init))
    private var languageChoices: [(String,String)] {
        let names = core.settings["supported_language_hints"] as? [String:String] ?? ["en":"English","vi":"Tiếng Việt"]
        return names.filter { transcriptionEngine != "assemblyai" || assemblyLanguages.contains($0.key) }.map { ($0.key,$0.value) }.filter {
            languageSearch.isEmpty || ($0.0 + " " + $0.1 + ($0.0 == "vi" ? " Vietnamese" : "")).localizedCaseInsensitiveContains(languageSearch)
        }.sorted {
            let left = $0.0 == "en" ? 0 : $0.0 == "vi" ? 1 : 2
            let right = $1.0 == "en" ? 0 : $1.0 == "vi" ? 1 : 2
            return left == right ? $0.1.localizedCaseInsensitiveCompare($1.1) == .orderedAscending : left < right
        }
    }
    private var valuesSignature: String { [String(automaticNotes),endpoint,model,provider,selectedLanguages.sorted().joined(separator:","),String(duration),instructions,String(Int(microphoneGain.rounded())),microphoneFilter,String(Int(microphoneThreshold.rounded())),String(stopOnMicrophoneRelease),vocabulary,asrMicrophoneSource,String(finalTranscription),transcriptionEngine,localModel,cleanupProvider,cleanupEndpoint,cleanupModel,String(localSpeakerLabels),String(localTranscriptCleanup),String(cleanupValidate)].joined(separator:"\u{001f}") }
    private var providerChanged: Bool { valuesSignature != saveProgress.initialValues || !summaryKey.isEmpty || !transcriptionKey.isEmpty || !assemblyKey.isEmpty || removeAssemblyKey || !cleanupKey.isEmpty || removeTranscriptionKey }
    private var changed: Bool { providerChanged || detectionDraft != saveProgress.initialDetection || login != initialLogin }
    private var keyPreview: String {
        guard let preview = savedKeyPreviews[provider] else { return "" }
        let trimmed = CharacterSet(charactersIn:"/")
        let savedEndpoint = text(core.settings["summary_endpoint"]).trimmingCharacters(in:trimmed)
        let current = endpoint.trimmingCharacters(in:trimmed)
        // The badge reflects what will actually be used: the key travels with its
        // provider preset, and a manually edited endpoint starts with a blank key.
        if current == savedEndpoint || current == (presets[provider] ?? "").trimmingCharacters(in:trimmed) { return preview }
        return ""
    }
    private var cleanupKeyPreview: String {
        // Mirrors the backend key resolution: the cleanup key store first,
        // then the notes key saved for the same provider.
        if let preview = cleanupKeyPreviews[cleanupProvider] { return preview }
        let destination = cleanupEndpoint.trimmingCharacters(in:.whitespacesAndNewlines).trimmingCharacters(in:CharacterSet(charactersIn:"/"))
        let effective = destination.isEmpty ? (presets[cleanupProvider] ?? "http://localhost:11434/v1").trimmingCharacters(in:CharacterSet(charactersIn:"/")) : destination
        let savedProvider = text(core.settings["summary_provider"])
        let notesEndpoint = cleanupProvider == savedProvider ? text(core.settings["summary_endpoint"]) : (presets[cleanupProvider] ?? "")
        if effective == notesEndpoint.trimmingCharacters(in:CharacterSet(charactersIn:"/")),
           let preview = savedKeyPreviews[cleanupProvider] { return preview }
        return ""
    }
    var body: some View {
        VStack(alignment:.leading,spacing:12) {
            HStack(spacing:10) {
                ScribeMark(size:32)
                Text("Settings").font(.system(size:26,design:.serif))
                Spacer()
                Button("Cancel") { cancel() }.keyboardShortcut(.cancelAction).disabled(applying)
                Button(applying ? "Saving…" : changed ? "Save changes" : "Done") { finish() }
                    .buttonStyle(.borderedProminent).tint(ScribeTheme.action).keyboardShortcut(.defaultAction)
                    .disabled(applying || !core.connected || (changed && core.busy))
            }.padding(.horizontal,24).padding(.top,20)
            if let saveError { Label(saveError,systemImage:"exclamationmark.triangle").font(.callout).foregroundStyle(ScribeTheme.warning).padding(.horizontal,24) }
            if changed && core.busy { Text("Finish current processing before saving these changes.").font(.caption).foregroundStyle(.secondary).padding(.horizontal,24) }
            Picker("Settings category",selection:$page) {
                Label("Recording",systemImage:"record.circle").tag("Recording")
                Label("Models",systemImage:"cpu").tag("Models")
                Label("Microphone",systemImage:"mic").tag("Microphone")
                Label("Calendar",systemImage:"calendar").tag("Calendar")
                Label("Experience",systemImage:"wand.and.stars").tag("Experience")
                Label("Account",systemImage:"person.crop.circle").tag("Account")
            }.pickerStyle(.segmented).labelsHidden().padding(.horizontal,24)
                .accessibilityLabel("Settings category")
                .onChange(of:page) { _ in focusedField = nil }
            Text(page == "Recording" ? "Languages, call detection and recording permissions." : page == "Models" ? "Choose how conversations are transcribed and turned into notes." : page == "Microphone" ? "Adjust your voice level and background noise filtering." : page == "Calendar" ? "Connect calendars and choose which meetings appear." : page == "Experience" ? "Experimental settings for trying features before they are finished." : "Your sign-in and purchase information.")
                .font(.caption).foregroundStyle(.secondary).padding(.horizontal,24)
            Form {
                if page == "Calendar" { CalendarSettings(calendar:calendar) }
                if page == "Account" {
                Section("Account and plan") {
                    if core.account["configured"] as? Bool != true {
                        Text("Optional sign-in is not available in this build.").foregroundStyle(.secondary)
                        Button("Sign in with Google") { core.send(["action":"account_sign_in"]) }
                            .disabled(true).help("The account-service address and verification key must be configured first.")
                        Text("No account is needed to record and use your notebook.").font(.caption).foregroundStyle(.secondary)
                    } else if text(core.account["phase"]) == "signing_in" {
                        HStack { ProgressView().controlSize(.small); Text("Finish Google sign-in in your browser.") }
                        Button("Cancel sign-in") { core.send(["action":"account_cancel"]) }.disabled(core.busy)
                    } else {
                        if let user = core.account["user"] as? Object {
                            Text(text(user["email"]))
                            Text(core.account["local_owned"] as? Bool == true ? "Local · Purchased once" : "Local · No purchase recorded")
                            Text(core.account["cloud_active"] as? Bool == true ? "Cloud · Subscription active" : "Cloud · No active subscription")
                            if text(core.account["phase"]) == "cached" { Text("Showing the last verified account state.").font(.caption).foregroundStyle(.secondary) }
                            HStack {
                                Button("Refresh plan") { core.send(["action":"account_refresh"]) }
                                Button("Sign out") { core.send(["action":"account_sign_out"]) }
                            }.disabled(core.busy)
                        } else {
                            Button("Sign in with Google") { core.send(["action":"account_sign_in"]) }.disabled(core.busy || !core.connected)
                        }
                    }
                    Text("Local: $3 one time · Cloud: $5/month. Checkout is not available in this build.")
                        .font(.caption).foregroundStyle(.secondary)
                    if !text(core.account["error"]).isEmpty {
                        Text(text(core.account["error"])).font(.caption).foregroundStyle(ScribeTheme.warning)
                    }
                }
                }
                if page == "Experience" {
                Section("Transcript cleanup") {
                    Toggle("Skip cleanup validation",isOn:Binding(get:{ !cleanupValidate },set:{ cleanupValidate = !$0 }))
                    Text("When on, changed numbers, negations and rewrites are accepted without content validation. The response still needs readable JSON with segment IDs so edits can be saved to the correct transcript. When off, unsafe edits are rejected per segment and the original wording is kept. Experimental: turn this off again for normal use.").font(.caption).foregroundStyle(.secondary)
                }
                }
                if page == "Models" {
                Section("Transcription") {
                    Picker("Engine",selection:$transcriptionEngine) {
                        Text("Deepgram · Cloud").tag("deepgram")
                        Text("AssemblyAI · Cloud").tag("assemblyai")
                        Text("Ollama · Local").tag("ollama")
                    }
                    .accessibilityLabel("Transcription engine")
                    .onChange(of:transcriptionEngine) { engine in
                        saveError = nil
                        if engine == "assemblyai" {
                            selectedLanguages = selectedLanguages.intersection(assemblyLanguages)
                            if selectedLanguages.isEmpty { selectedLanguages = ["en","vi"] }
                        }
                    }
                    if transcriptionEngine == "deepgram" {
                        VStack(alignment:.leading,spacing:8) {
                            HStack {
                                Text("Your Deepgram API key").font(.callout).foregroundStyle(.secondary)
                                Spacer()
                                if core.settings["deepgram_configured"] as? Bool == true && !removeTranscriptionKey {
                                    Label("Saved",systemImage:"checkmark.circle.fill").font(.caption).foregroundStyle(ScribeTheme.accent)
                                }
                            }
                            SecureField("Deepgram API key",text:$transcriptionKey,prompt:Text(core.settings["deepgram_configured"] as? Bool == true ? "Enter a new key to replace the saved key" : "Enter your Deepgram API key"))
                                .labelsHidden().textFieldStyle(.roundedBorder).controlSize(.large).textContentType(nil).autocorrectionDisabled(true)
                                .focused($focusedField,equals:.transcriptionKey)
                                .accessibilityLabel("Deepgram API key")
                                .disabled(removeTranscriptionKey)
                        }.padding(.vertical,4)
                        if core.settings["deepgram_configured"] as? Bool == true {
                            Toggle("Remove saved transcription key",isOn:$removeTranscriptionKey)
                        }
                        Text("Stored in Keychain. Audio is sent to Deepgram; recording and transcription reruns are billed to your API account.").font(.caption).foregroundStyle(.secondary)
                    }
                    if transcriptionEngine == "assemblyai" {
                        VStack(alignment:.leading,spacing:8) {
                            HStack {
                                Text("Your AssemblyAI API key").font(.callout).foregroundStyle(.secondary)
                                Spacer()
                                if core.settings["assemblyai_configured"] as? Bool == true && !removeAssemblyKey {
                                    Label("Saved",systemImage:"checkmark.circle.fill").font(.caption).foregroundStyle(ScribeTheme.accent)
                                }
                            }
                            SecureField("AssemblyAI API key",text:$assemblyKey,prompt:Text(core.settings["assemblyai_configured"] as? Bool == true ? "Enter a new key to replace the saved key" : "Enter your AssemblyAI API key"))
                                .labelsHidden().textFieldStyle(.roundedBorder).controlSize(.large).textContentType(nil).autocorrectionDisabled(true)
                                .focused($focusedField,equals:.transcriptionKey)
                                .accessibilityLabel("AssemblyAI API key")
                                .disabled(removeAssemblyKey)
                        }.padding(.vertical,4)
                        if core.settings["assemblyai_configured"] as? Bool == true {
                            Toggle("Remove saved transcription key",isOn:$removeAssemblyKey)
                        }
                        Text("Stored in Keychain. Audio is sent to AssemblyAI; recording and transcription reruns are billed to your API account.").font(.caption).foregroundStyle(.secondary)
                    }
                    if transcriptionEngine == "ollama" {
                        VStack(alignment:.leading,spacing:8) {
                            Text("Ollama transcription model").font(.callout).foregroundStyle(.secondary)
                            TextField("Model name",text:$localModel,prompt:Text("Installed audio model, e.g. gemma4:e2b"))
                                .labelsHidden().textFieldStyle(.roundedBorder).controlSize(.large)
                                .focused($focusedField,equals:.localModel).autocorrectionDisabled(true)
                                .simultaneousGesture(TapGesture().onEnded {
                                    NSApp.setActivationPolicy(.regular)
                                    NSApp.activate(ignoringOtherApps:true)
                                    focusedField = .localModel
                                })
                                .accessibilityLabel("Ollama transcription model")
                        }.padding(.vertical,4)
                        Toggle("Speaker labels · local diarization",isOn:$localSpeakerLabels)
                        Text("Optional anonymous Speaker 1/2 labels from a local diarization model. It needs the diarization add-on on this Mac; when it is unavailable the transcript is saved without labels.").font(.caption).foregroundStyle(.secondary)
                        HStack {
                            Button(core.checkingLocalModel ? "Checking…" : "Check Ollama") { core.checkOllama(model:localModel) }
                                .disabled(!core.connected || core.busy || core.checkingLocalModel)
                            if core.checkingLocalModel { ProgressView().controlSize(.small) }
                            if !core.localModelMessage.isEmpty {
                                Text(core.localModelMessage).font(.caption).foregroundStyle(.secondary).fixedSize(horizontal:false,vertical:true)
                            }
                        }
                        Text("Local transcription runs only after the conversation has ended — there is no live transcript while recording. ScribeLoop opens Ollama for you, downloads the named model once if it is available in the Ollama library, and keeps all audio on this Mac.").font(.caption).foregroundStyle(.secondary)
                        if !localSummary {
                            Text("Notes still use your selected cloud provider. Choose Ollama for Conversation notes to keep the whole flow on this Mac.").font(.caption).foregroundStyle(.orange)
                            Button("Use Ollama for notes too") {
                                selectProvider("Ollama")
                                model = localModel.trimmingCharacters(in:.whitespacesAndNewlines)
                            }
                        } else {
                            Text("Transcription and notes are set to local providers. Download both models before going offline; Check Ollama tests the transcription model without saving this draft.").font(.caption).foregroundStyle(.secondary)
                        }
                    }
                }
                if transcriptionEngine == "deepgram" {
                Section("Recognition accuracy") {
                    Text("Names and vocabulary · one term per line").font(.callout.weight(.medium))
                    TextEditor(text:$vocabulary).frame(height:85).font(.body).padding(6)
                        .background(ScribeTheme.canvas,in:RoundedRectangle(cornerRadius:8))
                        .overlay { RoundedRectangle(cornerRadius:8).strokeBorder(ScribeTheme.border) }
                        .accessibilityLabel("Transcription vocabulary")
                    Text("Add names, projects and technical terms you expect to hear. Up to 50 short terms; hints may improve recognition but do not force exact wording.").font(.caption).foregroundStyle(.secondary)
                    DisclosureGroup("Advanced recognition",isExpanded:$advancedRecognition) {
                    Picker("Microphone audio for transcription",selection:$asrMicrophoneSource) {
                        Text("Processed · gain and noise filter").tag("processed")
                        Text("Original · before gain and noise filter").tag("raw")
                    }
                    Text("Original sends the unprocessed microphone track to Deepgram. Playback keeps your microphone settings. Applies to new recordings and Transcribe again; older recordings need a retained original track.").font(.caption).foregroundStyle(.secondary)
                    Toggle("Refine transcript after recording",isOn:$finalTranscription)
                    Text("Sends the recording to Deepgram one more time after Stop, then generates notes. This adds processing time and provider charges within the displayed audio cap. The live revision is retained. If refinement fails, notes use the live transcript.").font(.caption).foregroundStyle(.secondary)
                    }
                }
                }
                Section("Conversation notes") {
                    Toggle("Generate notes after recording",isOn:$automaticNotes)
                    Text("Turn off for transcript-only recordings. You can generate notes later after configuring a notes provider.").font(.caption).foregroundStyle(.secondary)
                    Picker("Provider",selection:Binding(get:{provider},set:selectProvider)) {
                        Text("OpenAI · Cloud").tag("OpenAI")
                        Text("Gemini · Cloud").tag("Gemini")
                        Text("Ollama · Local").tag("Ollama")
                        Text("LM Studio · Local").tag("LM Studio")
                        Text("Custom · OpenAI compatible").tag("Custom")
                    }
                    VStack(alignment:.leading,spacing:8) {
                        Text("API base URL").font(.callout).foregroundStyle(.secondary)
                        TextField("API base URL",text:$endpoint)
                            .labelsHidden().textFieldStyle(.roundedBorder).controlSize(.large).autocorrectionDisabled(true)
                            .focused($focusedField,equals:.endpoint)
                            .accessibilityLabel("API base URL")
                    }.padding(.vertical,4)
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
                    Text(localSummary ? "Connects to your running local model server. A key is optional unless the server requires one." : "Your key is stored securely. Each provider remembers its own key and model, so switching providers restores both automatically; leave the fields unchanged to keep them or enter new values to replace them.").font(.caption).foregroundStyle(.secondary)
                    VStack(alignment:.leading,spacing:8) {
                        Text("Summary instructions").font(.callout.weight(.medium))
                        TextEditor(text:$instructions).font(.body)
                            .focused($focusedField,equals:.instructions)
                            .frame(minHeight:110,maxHeight:160).padding(6)
                            .background(ScribeTheme.canvas,in:RoundedRectangle(cornerRadius:8))
                            .overlay { RoundedRectangle(cornerRadius:8).strokeBorder(ScribeTheme.border) }
                            .accessibilityLabel("Summary instructions")
                        Text("Control the language, style and detail of new or regenerated notes. Clear to restore defaults. Citations remain required.").font(.caption).foregroundStyle(.secondary)
                        Text("\(instructions.count)/2,000 characters").font(.caption).foregroundStyle(instructions.count>2000 ? ScribeTheme.warning : .secondary)
                    }.padding(.vertical,4)
                }
                Section("Transcript cleanup") {
                    Toggle("Improve transcript wording",isOn:$localTranscriptCleanup)
                    if localTranscriptCleanup {
                        Picker("Provider",selection:$cleanupProvider) {
                            Text("OpenAI · Cloud").tag("OpenAI")
                            Text("Gemini · Cloud").tag("Gemini")
                            Text("Ollama · Local").tag("Ollama")
                            Text("LM Studio · Local").tag("LM Studio")
                            Text("Custom · OpenAI compatible").tag("Custom")
                        }
                        .onChange(of:cleanupProvider) { selected in
                            cleanupEndpoint = presets[selected] ?? cleanupEndpoint
                            cleanupModel = savedCleanupModels[selected] ?? ""
                            cleanupKey = ""
                            saveError = nil
                        }
                        VStack(alignment:.leading,spacing:8) {
                            Text("API base URL").font(.callout).foregroundStyle(.secondary)
                            TextField("API base URL",text:$cleanupEndpoint,prompt:Text(presets[cleanupProvider] ?? "http://localhost:11434/v1"))
                                .labelsHidden().textFieldStyle(.roundedBorder).controlSize(.large).autocorrectionDisabled(true)
                                .focused($focusedField,equals:.cleanupEndpoint)
                                .accessibilityLabel("Cleanup API base URL")
                        }.padding(.vertical,4)
                        if cleanupIsCloud {
                            VStack(alignment:.leading,spacing:8) {
                                HStack { Text("API key").font(.callout).foregroundStyle(.secondary)
                                    Spacer()
                                    if !cleanupKeyPreview.isEmpty { Label("Saved",systemImage:"checkmark.circle.fill").font(.caption).foregroundStyle(ScribeTheme.accent) }
                                }
                                SecureField("API key",text:$cleanupKey,prompt:Text(cleanupKeyPreview.isEmpty ? "Enter your API key" : cleanupKeyPreview))
                                    .labelsHidden().textFieldStyle(.roundedBorder).controlSize(.large).textContentType(nil).autocorrectionDisabled(true)
                                    .focused($focusedField,equals:.cleanupKey)
                                    .accessibilityLabel(cleanupKeyPreview.isEmpty ? "Cleanup API key" : "Replace saved cleanup API key")
                            }.padding(.vertical,4)
                        }
                        VStack(alignment:.leading,spacing:8) {
                            Text("Cleanup model").font(.callout).foregroundStyle(.secondary)
                            TextField("Model ID",text:$cleanupModel,prompt:Text(cleanupProvider == provider ? "Leave empty to reuse the notes model" : (cleanupProvider == "OpenAI" ? "Model ID from your provider, e.g. gpt-5.4-mini" : cleanupProvider == "Gemini" ? "Model ID from your provider, e.g. gemini-2.5-flash" : cleanupProvider == "LM Studio" ? "Loaded model name, e.g. qwen2.5-7b-instruct" : cleanupProvider == "Custom" ? "Type the model ID from your provider" : "Installed text model, e.g. gemma4:e2b")))
                                .labelsHidden().textFieldStyle(.roundedBorder).controlSize(.large)
                                .focused($focusedField,equals:.cleanupModel).autocorrectionDisabled(true)
                                .simultaneousGesture(TapGesture().onEnded {
                                    NSApp.setActivationPolicy(.regular)
                                    NSApp.activate(ignoringOtherApps:true)
                                    focusedField = .cleanupModel
                                })
                                .accessibilityLabel("Cleanup model")
                        }.padding(.vertical,4)
                        if cleanupProvider != provider && cleanupModel.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty && (savedSummaryModels[cleanupProvider] ?? "").isEmpty {
                            Text("Enter a cleanup model, or cleanup stays off.").font(.caption).foregroundStyle(.orange)
                        }
                        if cleanupIsCloud && cleanupKey.isEmpty && cleanupKeyPreview.isEmpty {
                            Text("Enter an API key for this provider, or cleanup stays off.").font(.caption).foregroundStyle(.orange)
                        }
                        Text("After transcription, the selected model uses the call topic, nearby dialogue and vocabulary to repair ASR wording and Vietnamese diacritics before notes are generated. Unclear wording is flagged for review; changed passages show their original ASR text. Content checks protect numbers, negations and excessive rewrites unless skipped in Experience. Cloud cleanup sends transcript text and context to that provider; audio is never sent by this step. On-device providers keep the text on this Mac.").font(.caption).foregroundStyle(.secondary)
                    }
                }
                }
                if page == "Microphone" {
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
                        DisclosureGroup("Advanced noise filtering",isExpanded:$advancedMicrophone) {
                        VStack(alignment:.leading,spacing:8) {
                            HStack { Text("Noise rejection threshold"); Spacer(); Text(String(format:"%.0f dBFS",microphoneThreshold)).monospacedDigit().foregroundStyle(.secondary) }
                            Slider(value:$microphoneThreshold,in:-65 ... -25,step:1)
                                .accessibilityLabel("Microphone noise rejection threshold")
                                .accessibilityValue(String(format:"%.0f decibels",microphoneThreshold))
                            Text("Raise to reject more background sound; lower to keep quieter speech.").font(.caption).foregroundStyle(.secondary)
                        }.padding(.vertical,4)
                    }
                    }
                    Text("Sensitivity boosts your voice and background sound. The filter reduces rumble and quiet background noise; it cannot isolate voices or remove every sudden sound. Strong may soften quiet speech.").font(.caption).foregroundStyle(.secondary)
                    Text("Applies to new recordings and processed-audio transcription. An encrypted original microphone track is kept when processing is enabled. Existing recordings stay as recorded.").font(.caption).foregroundStyle(.secondary)
                    Button("Reset microphone settings") { microphoneGain = 0; microphoneFilter = "standard"; microphoneThreshold = -40 }
                }
                }
                if page == "Recording" {
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
                            Text("Teams, Slack, Zoom and browser calls · Screen & System Audio Recording").font(.caption).foregroundStyle(.secondary)
                        }
                        Spacer()
                        Button(core.meetingAudioPermissionLabel) { core.requestMeetingAudioAccess() }.disabled(!core.connected || core.busy || core.meetingAudioPermissionGranted)
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
                    Text("Stops after 60 seconds without the meeting app’s microphone activity. Muted calls can trigger this too; choose Keep recording during the countdown to continue. Applies only to meeting-app capture.").font(.caption).foregroundStyle(.secondary)
                    Toggle("Open ScribeLoop at login",isOn:$login)
                    if !loginStatus.isEmpty { Text(loginStatus).font(.caption) }
                }
                Section("Recording preferences") {
                    Picker("Maximum duration",selection:$duration) { Text(transcriptionEngine == "ollama" ? "5 minutes" : "5 minutes · $0.25 audio cap").tag(300); Text(transcriptionEngine == "ollama" ? "1 hour" : "1 hour · $3 audio cap").tag(3600); Text(transcriptionEngine == "ollama" ? "3 hours" : "3 hours · $8 audio cap").tag(10800) }
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
                    Text(transcriptionEngine == "ollama" ? "These hints apply to cloud transcription. Ollama detects the spoken language using your selected local model." : "Choose the languages you expect to hear. The cloud provider uses them as hints; accuracy can vary. Keep at least one selected.").font(.caption).foregroundStyle(.secondary)
                }
                }
            }.formStyle(.grouped).id(page).disabled(applying)
        }.background(ScribeTheme.canvas).tint(ScribeTheme.accent).background(InteractiveWindowActivation())
        .background(OutsideClickFocusDismissal(dismissFocus:{ focusedField = nil },
            cancelOnOutsideModal:applying ? nil : { cancel() }).allowsHitTesting(false)).onAppear {
            guard core.connected else { dismiss(); return }
            page = initialPage
            endpoint = text(core.settings["summary_endpoint"])
            model = text(core.settings["summary_model"])
            automaticNotes = core.settings["automatic_notes"] as? Bool ?? true
            core.refreshMeetingAudioPermission()
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
            transcriptionEngine = text(core.settings["transcription_engine"])
            if !["deepgram","assemblyai","ollama"].contains(transcriptionEngine) { transcriptionEngine = "deepgram" }
            localModel = text(core.settings["local_transcription_model"])
            if localModel.isEmpty { localModel = "gemma4:e2b" }
            localSpeakerLabels = core.settings["local_speaker_labels"] as? Bool ?? false
            localTranscriptCleanup = core.settings["local_transcript_cleanup"] as? Bool ?? true
            cleanupModel = text(core.settings["local_cleanup_model"])
            let savedCleanupProvider = text(core.settings["local_cleanup_provider"])
            cleanupProvider = ["OpenAI","Gemini","Ollama","LM Studio","Custom"].contains(savedCleanupProvider) ? savedCleanupProvider : "Ollama"
            cleanupEndpoint = text(core.settings["local_cleanup_endpoint"])
            cleanupKey = ""
            cleanupValidate = core.settings["local_cleanup_validate"] as? Bool ?? true
            microphoneGain = (core.settings["microphone_gain_db"] as? NSNumber)?.doubleValue ?? 0
            microphoneFilter = text(core.settings["microphone_noise_filter"])
            if microphoneFilter.isEmpty { microphoneFilter = "standard" }
            microphoneThreshold = (core.settings["microphone_noise_threshold_db"] as? NSNumber)?.doubleValue ?? -40
            stopOnMicrophoneRelease = core.settings["stop_on_microphone_release"] as? Bool ?? false
            saveProgress = PreferencesSaveProgress()
            saveProgress.initialValues = valuesSignature
            saveProgress.initialDetection = core.detection; detectionDraft = saveProgress.initialDetection
            initialLogin = SMAppService.mainApp.status == .enabled; login = initialLogin
            summaryKey = ""; transcriptionKey = ""; assemblyKey = ""; removeAssemblyKey = false; cleanupKey = ""; removeTranscriptionKey = false; saveError = nil; pendingSave = nil; pendingSaveCommand = nil; applying = false; saveCommands = []
            loginStatus = SMAppService.mainApp.status == .requiresApproval ? "Approve ScribeLoop in Login Items settings." : ""
        }.onChange(of:core.requests[pendingSave ?? ""]) { status in
            guard pendingSave != nil else { return }
            if status == "accepted" {
                let action = text(pendingSaveCommand?["action"])
                saveProgress.accept(action:action,values:valuesSignature,detection:detectionDraft)
                if action == "save_settings" { summaryKey = ""; transcriptionKey = ""; assemblyKey = ""; removeAssemblyKey = false; cleanupKey = ""; removeTranscriptionKey = false }
                pendingSave = nil; pendingSaveCommand = nil; saveNext()
            }
            else if status == "rejected" || status == "unknown" {
                pendingSave = nil; pendingSaveCommand = nil; applying = false; saveCommands = []
                saveError = saveProgress.failure(uncertain:status == "unknown")
            }
        }.interactiveDismissDisabled(applying)
        .onChange(of:core.accepted) { accepted in if !accepted { cancel() } }
        .onReceive(NotificationCenter.default.publisher(for:NSApplication.didBecomeActiveNotification)) { _ in core.refreshMeetingAudioPermission() }
        .onDisappear { summaryKey = ""; transcriptionKey = ""; assemblyKey = ""; removeAssemblyKey = false; cleanupKey = "" }
    }
    private func selectProvider(_ selected: String) {
        guard selected != provider else { return }
        provider = selected
        if let preset = presets[selected] { endpoint = preset }
        summaryKey = ""; saveError = nil
        // Restore the model this provider remembers, like its saved key.
        model = savedSummaryModels[selected] ?? (selected == "OpenAI" ? "gpt-5.4-mini" : "")
        focusedField = .model
    }
    private func cancel() {
        guard !applying else { return }
        focusedField = nil; summaryKey = ""; transcriptionKey = ""; assemblyKey = ""; removeAssemblyKey = false; cleanupKey = ""; dismiss()
    }
    private func finish() {
        focusedField = nil
        guard !applying else { return }
        guard changed else { dismiss(); return }
        guard !model.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty else { saveError = "Enter a model ID before saving."; return }
        guard transcriptionEngine != "ollama" || !localModel.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty else {
            saveError = "Enter an Ollama transcription model name before saving."; return }
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
            value["automatic_notes"] = automaticNotes
            value["transcription_engine"] = transcriptionEngine
            value["local_transcription_model"] = localModel.trimmingCharacters(in:.whitespacesAndNewlines)
            value["local_speaker_labels"] = localSpeakerLabels
            value["local_transcript_cleanup"] = localTranscriptCleanup
            value["local_cleanup_model"] = cleanupModel.trimmingCharacters(in:.whitespacesAndNewlines)
            value["local_cleanup_provider"] = cleanupProvider
            value["local_cleanup_endpoint"] = cleanupEndpoint.trimmingCharacters(in:.whitespacesAndNewlines)
            value["local_cleanup_validate"] = cleanupValidate
            if !cleanupKey.isEmpty { value["local_cleanup_key"] = cleanupKey }
            if removeAssemblyKey { value["assemblyai_key"] = "" }
            else if !assemblyKey.isEmpty { value["assemblyai_key"] = assemblyKey }
            if removeTranscriptionKey { value["deepgram_key"] = "" }
            else if !transcriptionKey.isEmpty { value["deepgram_key"] = transcriptionKey }
            saveCommands.append(["action":"save_settings","settings":value])
        }
        if detectionDraft != saveProgress.initialDetection { saveCommands.append(["action":"set_detection","enabled":detectionDraft]) }
        applying = true; saveNext()
    }
    private func saveNext() {
        if !saveCommands.isEmpty {
            let command = saveCommands.removeFirst()
            pendingSaveCommand = command
            pendingSave = core.send(command)
            if pendingSave == nil {
                applying = false; saveCommands = []
                pendingSaveCommand = nil
                saveError = saveProgress.failure(uncertain:true)
            }
            return
        }
        if login != initialLogin {
            do {
                if login { try SMAppService.mainApp.register() } else { try SMAppService.mainApp.unregister() }
                if SMAppService.mainApp.status == .requiresApproval { core.message = "Approve ScribeLoop in Login Items settings to open it at login." }
            } catch {
                applying = false
                saveProgress.initialValues = valuesSignature; saveProgress.initialDetection = detectionDraft
                summaryKey = ""; transcriptionKey = ""; assemblyKey = ""; removeAssemblyKey = false; cleanupKey = ""; removeTranscriptionKey = false
                saveError = "Other settings were saved, but the login setting could not be changed. Check System Settings → Login Items."
                return
            }
        }
        applying = false; summaryKey = ""; transcriptionKey = ""; assemblyKey = ""; removeAssemblyKey = false; cleanupKey = ""; dismiss()
    }
}

struct AudioView: View {
    @ObservedObject var core: DesktopClient
    @State private var rerun = false
    @State private var rerunCommand:Object?
    var audio: Object { core.detail["audio"] as? Object ?? [:] }
    var available: Bool { audio["available"] as? Bool == true }
    private var cloudProvider:String { text(core.settings["transcription_engine"]) == "assemblyai" ? "AssemblyAI" : "Deepgram" }
    private var reviewedCloudProvider:String { text(rerunCommand?["model"]) == "assemblyai" ? "AssemblyAI" : "Deepgram" }
    private var localTranscription:Bool { text(core.settings["transcription_engine"]) == "ollama" }
    private var reviewedLocalTranscription:Bool { text(rerunCommand?["model"]) == "ollama" }
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
                    Text(localTranscription ? "Transcribe the saved recording on this Mac with Ollama. Your current transcript stays available until the new version is ready." : "Send the saved recording to \(cloudProvider). Your current transcript stays available until the new version is ready. Your provider bills your API account.").font(.callout).foregroundStyle(.secondary)
                    Button("Transcribe again…") {
                        guard let id = core.selectedID, let command = core.savedTranscriptionCommand(action:"reprocess",sessionID:id) else {
                            core.message = "Choose a transcription engine in Preferences first."; return
                        }
                        rerunCommand = command; rerun = true
                    }.disabled(!available || core.busy || !core.accepted)
                }.notebookCard()
            }.padding(28).frame(maxWidth:850,alignment:.leading)
        }
        .onDisappear { core.stopAudio() }
        .confirmationDialog(reviewedLocalTranscription ? "Transcribe again on this Mac with Ollama?" : "Send this saved audio to \(reviewedCloudProvider)?",isPresented:$rerun,titleVisibility:.visible) {
            Button(reviewedLocalTranscription ? "Transcribe locally" : "Upload to \(reviewedCloudProvider)") {
                core.stopAudio()
                if let command = rerunCommand { core.send(command) }
            }
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
    let core: DesktopClient
    let busy:Bool
    let citationID:String?
    let turn: TranscriptTurn
    private var row:Record { turn.rows[0] }
    private var personColor:Color {
        let colors:[Color] = [ScribeTheme.accent,.blue,.purple,.orange,.pink,.teal]
        return colors[max(0,row.int("person_number")-1) % colors.count]
    }
    @State private var name = ""
    @State private var rename = false
    @State private var naming = false
    var body: some View {
        HStack(alignment:.top,spacing:12) {
            if turn.alignsRight { Spacer(minLength:64) }
            else { avatar }
            VStack(alignment:.leading,spacing:8) {
                HStack {
                    Text(row.personTitle).font(.system(size:11,weight:.semibold)).foregroundStyle(personColor).padding(.horizontal,8).padding(.vertical,5).background(ScribeTheme.subtle,in:Capsule())
                    Spacer(minLength:8)
                    Text(String(format:"%d:%02d",row.int("start_ms")/60000,row.int("start_ms")/1000%60))
                        .font(.system(size:10)).monospacedDigit().foregroundStyle(.secondary)
                    if !row.text("display_speaker_id").isEmpty {
                        Menu {
                            Button("Rename speaker…") { name = row.text("speaker_label"); rename = true }
                            Menu("Merge into") {
                                ForEach(speakers.filter { $0.text("display_speaker_id") != row.text("display_speaker_id") }) { target in
                                    Button(target.text("speaker_label")) { correction("merge_speaker",extra:["merge_target":target.text("display_speaker_id")]) }
                                }
                            }
                            Button("Name this person…") { core.send(["action":"people_page"]); naming = true }

                        } label: { Image(systemName:"pencil") }.disabled(busy)
                    }
                }
                ForEach(turn.rows) { segment in
                    Text(verbatim:segment.text("text")).textSelection(.enabled).lineSpacing(5)
                        .frame(maxWidth:.infinity,alignment:.leading)
                        .padding(segment.id == citationID ? 6 : 0)
                        .background(segment.id == citationID ? personColor.opacity(0.2) : Color.clear,in:RoundedRectangle(cornerRadius:6))
                        .help("\(segment.int("start_ms")/60000):\(String(format:"%02d",segment.int("start_ms")/1000%60)) · \(segment.text("stream") == "mic" ? "Microphone" : "Meeting audio")")
                        .id(segment.id)
                    if !segment.text("cleanup_original_text").isEmpty {
                        DisclosureGroup(segment.raw["cleanup_original_truncated"] as? Bool == true ? "Original ASR · excerpt" : "Original ASR") {
                            Text(verbatim:segment.text("cleanup_original_text")).textSelection(.enabled)
                                .font(.callout).foregroundStyle(.secondary).frame(maxWidth:.infinity,alignment:.leading)
                        }.font(.caption).foregroundStyle(ScribeTheme.accent)
                    }
                    if segment.raw["cleanup_needs_review"] as? Bool == true {
                        Label("Unclear wording · review original audio",systemImage:"exclamationmark.bubble")
                            .font(.caption).foregroundStyle(ScribeTheme.warning)
                    }
                }
            }.padding(16)
                .frame(maxWidth:640,alignment:.leading)
                .background(personColor.opacity(0.10),
                            in:RoundedRectangle(cornerRadius:18))
                .overlay(RoundedRectangle(cornerRadius:18).stroke(personColor.opacity(0.22),lineWidth:1))
            if turn.alignsRight { avatar }
            else { Spacer(minLength:64) }
        }.sheet(isPresented:$naming) { PeopleView(core:core,initialSpeaker:row.text("display_speaker_id")).frame(width:650,height:650) }
        .alert("Name this speaker",isPresented:$rename) {
            TextField("Display name",text:$name)
            Button("Cancel",role:.cancel) {}
            Button("Save") { correction("rename_speaker",extra:["label":name]) }
        } message: { Text("A manual label applies to this transcript revision. It does not identify a voice across meetings.") }
    }
    private var avatar:some View {
        Text(row.int("person_number") > 0 ? "\(row.int("person_number"))" : "?")
            .font(.system(size:12,weight:.semibold)).foregroundStyle(personColor)
            .frame(width:32,height:32).background(personColor.opacity(0.14),in:Circle())
            .accessibilityLabel(row.personTitle)
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
