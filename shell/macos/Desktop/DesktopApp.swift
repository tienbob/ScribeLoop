import SwiftUI
import AppKit
import ServiceManagement

/// User-opened interactive windows must own keyboard input, even when the
/// menu-bar app was previously inactive. Never install on automatic call panels.
struct InteractiveWindowActivation: NSViewRepresentable {
    final class Anchor: NSView {
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            guard let window else { return }
            DispatchQueue.main.async { [weak self, weak window] in
                guard let self, let window, self.window === window else { return }
                NSApp.setActivationPolicy(.regular)
                NSApp.activate(ignoringOtherApps:true)
                window.makeKeyAndOrderFront(nil)
                if window.canBecomeMain { window.makeMain() }
            }
        }
    }
    func makeNSView(context:Context) -> Anchor { Anchor() }
    func updateNSView(_ view:Anchor,context:Context) {}
}

/// End field editing on outside clicks without consuming the clicked control.
/// This observes only mouse-down events delivered to this Preferences window.
struct OutsideClickFocusDismissal: NSViewRepresentable {
    var dismissFocus: () -> Void
    var cancelOnOutsideModal: (() -> Void)? = nil
    final class Anchor: NSView {
        var dismissFocus: () -> Void = {}
        var cancelOnOutsideModal: (() -> Void)?
        private var monitor: Any?
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            stop()
            guard window != nil else { return }
            monitor = NSEvent.addLocalMonitorForEvents(matching:.leftMouseDown) { [weak self] event in
                guard let self, let window = self.window else { return event }
                if let parent = window.sheetParent, event.window === parent,
                   let cancel = self.cancelOnOutsideModal {
                    cancel()
                    return nil // Dismiss only; never activate a control behind the sheet.
                }
                guard event.window === window,
                      let editor = window.firstResponder as? NSTextView, editor.isEditable else { return event }
                let fieldBounds = editor.convert(editor.visibleRect,to:nil).insetBy(dx:-3,dy:-3)
                guard !fieldBounds.contains(event.locationInWindow) else { return event }
                self.dismissFocus()
                window.makeFirstResponder(nil)
                return event
            }
        }
        func stop() { if let monitor { NSEvent.removeMonitor(monitor) }; monitor = nil }
    }
    func makeNSView(context:Context) -> Anchor {
        let view = Anchor(); view.dismissFocus = dismissFocus
        view.cancelOnOutsideModal = cancelOnOutsideModal; return view
    }
    func updateNSView(_ view:Anchor,context:Context) {
        view.dismissFocus = dismissFocus; view.cancelOnOutsideModal = cancelOnOutsideModal
    }
    static func dismantleNSView(_ view:Anchor,coordinator:()) { view.stop() }
}

@MainActor final class CallReviewPanel: NSPanel, NSWindowDelegate {
    var onUserClose: (() -> Void)?
    func windowShouldClose(_ sender:NSWindow) -> Bool { onUserClose?(); return true }
}

@MainActor final class DesktopDelegate: NSObject, NSApplicationDelegate {
    weak var core: DesktopClient?
    private var callPanel: NSPanel?
    func applicationDidFinishLaunching(_ notification: Notification) {
        NSApp.setActivationPolicy(.regular)
        NSApp.activate(ignoringOtherApps:true)
    }
    func applicationShouldHandleReopen(_ sender:NSApplication,hasVisibleWindows:Bool) -> Bool {
        sender.activate(ignoringOtherApps:true)
        if let window = sender.windows.first(where:{ $0.identifier?.rawValue == "notebook" || $0.title == "ScribeLoop" }) {
            (window.attachedSheet ?? window).makeKeyAndOrderFront(nil)
        }
        return true
    }
    func showCall(_ candidate: Record) {
        guard let core else { return }
        callPanel?.close()
        let panel = CallReviewPanel(contentRect:NSRect(x:0,y:0,width:640,height:600),
            styleMask:[.titled,.closable,.nonactivatingPanel],backing:.buffered,defer:false)
        panel.delegate = panel
        panel.onUserClose = { [weak core] in core?.send(["action":"dismiss_candidate","candidate_id":candidate.id]) }
        panel.title = "ScribeLoop · Possible call"
        panel.isReleasedWhenClosed = false
        panel.level = .floating; panel.collectionBehavior = [.canJoinAllSpaces,.fullScreenAuxiliary]
        panel.contentView = NSHostingView(rootView:RecordingReview(core:core,candidate:candidate,onClose:{ [weak panel] in panel?.close() }))
        panel.center(); panel.orderFrontRegardless(); callPanel = panel
    }
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let core, core.running else { return .terminateNow }
        if core.busy {
            let alert = NSAlert(); alert.messageText = "Stop and quit ScribeLoop?"
            alert.informativeText = "Recording will stop and pending notes will be cancelled. Saved content stays encrypted."
            alert.addButton(withTitle:"Stop & Quit"); alert.addButton(withTitle:"Cancel")
            guard alert.runModal() == .alertFirstButtonReturn else { return .terminateCancel }
        }
        core.stop { sender.reply(toApplicationShouldTerminate:true) }; return .terminateLater
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
}

struct ScribeLoopApp: App {
    @NSApplicationDelegateAdaptor(DesktopDelegate.self) private var delegate
    @StateObject private var core = DesktopClient()
    @Environment(\.openWindow) private var openWindow
    var body: some Scene {
        Window("ScribeLoop",id:"notebook") {
            Notebook(core:core).onAppear { delegate.core = core; core.onCandidate = { [weak controller = delegate] row in controller?.showCall(row) }; if !core.running { core.start() } }
        }.defaultSize(width:1160,height:780)
        MenuBarExtra {
            Text(core.activeID == nil ? (core.summaryID == nil ? core.monitorLabel : "Preparing notes") : "Recording · microphone and selected source")
            if core.activeID != nil { Button("Stop recording",role:.destructive) { core.stopRecording() } }
            if !objects(core.discovery["candidates"]).isEmpty { Text("Possible call detected · open ScribeLoop to review") }
            Button("Open ScribeLoop") { openWindow(id:"notebook"); NSApp.activate(ignoringOtherApps:true) }
            Button(core.detection ? "Pause call detection" : "Resume call detection") {
                core.send(["action":"set_detection","enabled":!core.detection])
            }.disabled(core.activeID != nil || !core.connected)
            Divider()
            Button("Quit ScribeLoop") { NSApp.terminate(nil) }
        } label: { Image(systemName:core.activeID == nil ? "waveform" : "record.circle.fill").accessibilityLabel(core.activeID == nil ? "ScribeLoop" : "ScribeLoop recording") }
        Settings { Preferences(core:core).frame(width:620,height:690) }
    }
}

struct Notebook: View {
    @ObservedObject var core: DesktopClient
    @State private var recording = false
    @State private var candidate: Record?
    @State private var retryCapture: Object?
    @State private var showPreferences = false
    @State private var showPeople = false
    @State private var tab = "Notes"
    @State private var confirmed: String?
    @State private var renaming: Record?
    private let paper = ScribeTheme.canvas
    var candidates: [Record] { objects(core.discovery["candidates"]).filter { $0.raw["selectable"] as? Bool == true } }
    var body: some View {
        NavigationSplitView {
            VStack(alignment:.leading,spacing:16) {
                HStack(spacing:10) {
                    ScribeMark()
                    VStack(alignment:.leading,spacing:3) {
                        Text("ScribeLoop").font(.system(size:25,weight:.semibold,design:.serif))
                        Text("Your meeting notebook").font(.caption).foregroundStyle(.secondary)
                    }
                    Spacer(minLength:0)
                }.padding(.bottom,6)
                Button { candidate = nil; retryCapture = nil; recording = true } label: { Label("New recording",systemImage:"plus").frame(maxWidth:.infinity).padding(.vertical,5) }.buttonStyle(.borderedProminent).tint(ScribeTheme.action)
                    .disabled(!core.accepted || !core.connected || core.busy)
                HStack { Text("CONVERSATIONS").tracking(1.3); Spacer(); Text("\(core.sessions.count)").monospacedDigit() }.font(.system(size:10,weight:.semibold)).foregroundStyle(.secondary).padding(.top,12)
                List(selection:$core.selectedID) {
                    ForEach(core.sessions) { row in
                        VStack(alignment:.leading,spacing:5) {
                            Text(row.text("title")).font(.system(size:13,weight:.semibold)).lineLimit(2)
                            Text(Date(timeIntervalSince1970:Double(row.int("started_at"))/1000),style:.date).font(.system(size:11)).foregroundStyle(.secondary)
                            HStack(spacing:4) {
                                MeetingStatus(status:row.text("status"))
                                Spacer(minLength:4)
                                Button { renaming = row } label: {
                                    Image(systemName:"pencil").frame(width:28,height:28)
                                }.help("Rename conversation").accessibilityLabel("Rename \(row.text("title"))")
                                Button(role:.destructive) { confirmed = row.id } label: {
                                    Image(systemName:"trash").frame(width:28,height:28)
                                }.help("Delete conversation").accessibilityLabel("Delete \(row.text("title"))")
                            }.buttonStyle(.borderless)
                                .disabled(core.busy || !core.connected)
                                .padding(.top,3)
                        }.padding(.vertical,8).padding(.horizontal,6)
                            .frame(maxWidth:.infinity,alignment:.leading)
                            .foregroundStyle(.primary)
                            .background(core.selectedID == row.id ? ScribeTheme.subtle : Color.clear,in:RoundedRectangle(cornerRadius:10))
                            .overlay { RoundedRectangle(cornerRadius:10).strokeBorder(core.selectedID == row.id ? ScribeTheme.accent.opacity(0.35) : Color.clear,lineWidth:1) }
                            .background(NotebookListSelection().allowsHitTesting(false))
                            .accessibilityAction(named:Text("Rename conversation")) {
                                if core.connected && !core.busy { renaming = row }
                            }
                            .accessibilityAction(named:Text("Delete conversation")) {
                                if core.connected && !core.busy { confirmed = row.id }
                            }
                            .tag(row.id)
                        .contextMenu {
                            Button("Rename conversation…",systemImage:"pencil") { renaming = row }.disabled(core.busy || !core.connected)
                            Divider()
                            Button("Delete conversation…",systemImage:"trash",role:.destructive) { confirmed = row.id }.disabled(core.busy || !core.connected)
                        }
                    }
                }.listStyle(.sidebar).scrollContentBackground(.hidden)
                    .tint(ScribeTheme.action).accentColor(ScribeTheme.action)
                if let cursor = core.nextCursor { Button("Older meetings") { core.send(["action":"history_page","cursor":cursor]) } }
                HStack {
                    HStack(spacing:7) {
                        Circle().fill(core.connected ? ScribeTheme.accent : Color.orange).frame(width:6,height:6)
                        Text(core.monitorLabel).font(.system(size:11)).foregroundStyle(.secondary)
                    }
                    Spacer()
                    Button { NSApp.activate(ignoringOtherApps:true); showPreferences = true } label: { Image(systemName:"gearshape").padding(4) }.buttonStyle(.borderless).help("Preferences")
                }.padding(10).background(ScribeTheme.panel,in:RoundedRectangle(cornerRadius:12))
            }.padding(18).background(ScribeTheme.sidebar)
            .navigationSplitViewColumnWidth(min:260,ideal:290,max:380)
        } detail: {
            VStack(spacing:0) {
                if let message = core.message, !message.isEmpty {
                    HStack(alignment:.top) { Image(systemName:"info.circle"); Text(message).textSelection(.enabled); Spacer(); Button("Dismiss") { core.message = nil }; if core.uncertainActions { Button("I reviewed the current state") { core.acknowledgeUncertainActions() } } }.font(.callout).padding(14).background(Color.orange.opacity(0.09))
                }
                if !core.accepted && core.connected { agreementView }
                else if let current = candidates.first, core.activeID == nil {
                    HStack { Label("Possible call in \(current.text("name"))",systemImage:"phone.arrow.up.right"); Spacer()
                        Button("Not now") { core.send(["action":"dismiss_candidate","candidate_id":current.id]) }
                        Button("Review recording") { candidate = current; retryCapture = nil; recording = true }.buttonStyle(.borderedProminent).disabled(core.busy)
                    }.padding(18).background(Color.green.opacity(0.08))
                }
                if let activeID = core.activeID {
                    HStack { Image(systemName:"record.circle.fill").foregroundStyle(.red); Text(core.captureStopping ? "Stopping capture…" : "Recording · Cloud / Deepgram")
                        if let row = core.sessions.first(where: { $0.id == activeID }) { TimelineView(.periodic(from:.now,by:1)) { _ in Text(Date(timeIntervalSince1970:Double(row.int("started_at"))/1000),style:.timer).monospacedDigit() } }
                        Spacer(); Button("Stop recording",role:.destructive) { core.stopRecording() }.keyboardShortcut(".",modifiers:.command)
                    }.padding(18)
                }
                if let streamingLabel = core.streamingLabel {
                    HStack { Image(systemName:"text.bubble"); Text(streamingLabel); Spacer() }
                        .font(.caption).foregroundStyle(.secondary).padding(.horizontal,18).padding(.bottom,10)
                }
                if let callStatus = core.callStatus, let activeID = core.activeID {
                    HStack { Text(callStatus); if core.callEnding { Button("Keep recording") { core.send(["action":"keep_recording","session_id":activeID]); core.callEnding = false; core.callStatus = nil } } }.padding()
                }
                if core.processingID != nil {
                    HStack { ProgressView().controlSize(.small); Text(core.processingLabel); Spacer(); Text("Your saved transcript stays available.").foregroundStyle(.secondary) }.font(.callout).padding(18).background(ScribeTheme.subtle)
                }
                if let selected = core.selected {
                    VStack(alignment:.leading,spacing:18) {
                        HStack(alignment:.top,spacing:20) {
                            VStack(alignment:.leading,spacing:12) {
                                Text(selected.text("title")).font(.system(size:30,weight:.medium,design:.serif)).lineLimit(2).textSelection(.enabled)
                                HStack(spacing:10) {
                                    MeetingStatus(status:selected.text("status"))
                                    Text(Date(timeIntervalSince1970:Double(selected.int("started_at"))/1000),style:.date)
                                    Text("· \(core.total) \(core.total == 1 ? "segment" : "segments")")
                                }.font(.caption).foregroundStyle(.secondary)
                                if let failure = selected.raw["failure"] as? Object,
                                   !text(failure["message"]).isEmpty {
                                    Label(text(failure["message"]),systemImage:"exclamationmark.triangle")
                                        .font(.callout).foregroundStyle(ScribeTheme.warning)
                                    Button("Review and retry recording") {
                                        candidate = nil; retryCapture = selected.raw["capture"] as? Object; recording = true
                                    }.disabled(core.busy || !core.connected)
                                }
                            }
                            Spacer(minLength:0)
                            HStack(spacing:8) {
                            Button("Name people",systemImage:"person.crop.circle.badge.plus") { core.send(["action":"people_page"]); showPeople = true }.disabled(!core.connected || core.busy || core.revision == 0)
                            Menu("Meeting") { meetingActions(selected) }.disabled(!core.connected)
                            }.controlSize(.regular).font(.callout)
                        }
                        Picker("Meeting view",selection:$tab) { Label("Notes",systemImage:"note.text").tag("Notes"); Label("Transcript",systemImage:"text.bubble").tag("Transcript"); Label("Audio",systemImage:"waveform").tag("Audio") }.pickerStyle(.segmented).frame(maxWidth:390)
                    }.padding(28).id(selected.id+selected.text("title")+selected.text("status"))
                    Divider()
                    if tab == "Transcript" { transcript }
                    else if tab == "Audio" { AudioView(core:core) }
                    else { notes }
                } else {
                    Spacer()
                    ScribeMark(size:86).padding(.bottom,10)
                    Text("Be present. Keep the conversation.").font(.system(size:30,design:.serif)).padding(.top,12)
                    Text(core.connected ? "ScribeLoop watches for likely calls. You decide when recording starts.\nOr choose New recording for a microphone or meeting app." : "Connecting to your encrypted workspace…")
                        .multilineTextAlignment(.center).foregroundStyle(.secondary).padding()
                    if !core.running { Button("Retry core") { core.start() } }
                    else if !core.connected { Button("Reconnect") { core.reconnect() }; Button("Cancel startup") { core.stop() } }
                    Spacer()
                }
            }.frame(maxWidth:.infinity,maxHeight:.infinity).background(paper)
        }.navigationSplitViewStyle(.balanced).frame(minWidth:980,minHeight:680)
        .foregroundStyle(.primary)
        .tint(ScribeTheme.accent)
        .background(InteractiveWindowActivation())
        .onChange(of:core.selectedID) { value in if let value { core.select(value) } }
        .sheet(isPresented:$recording) { RecordingReview(core:core,candidate:candidate,initialCapture:retryCapture) }
        .sheet(isPresented:$showPreferences) { Preferences(core:core).frame(width:650,height:720) }
        .sheet(isPresented:$showPeople) { PeopleView(core:core).frame(width:650,height:680) }
        .sheet(item:$renaming) { row in RenameConversation(core:core,conversation:row) }
        .alert("Delete this conversation permanently?",isPresented:Binding(get:{confirmed != nil},set:{ if !$0 { confirmed = nil } })) {
            Button("Cancel",role:.cancel) { confirmed = nil }
            Button("Delete conversation",role:.destructive) { if let id = confirmed { core.send(["action":"delete","session_id":id,"confirmed":true]); confirmed = nil } }
        } message: { Text("\(core.sessions.first(where: { $0.id == confirmed })?.text("title") ?? "This conversation") and its audio, transcript and notes will be permanently deleted.") }
    }
    private var agreementView: some View {
        VStack(alignment:.leading,spacing:12) {
            Text("Before your first recording").font(.title2)
            Text("Record only with permission from everyone captured. You choose Record / Not now for each call. Browser recording captures all tabs in that browser.")
            Text("Audio streams to Deepgram; transcript text and speaker labels go to your configured summary endpoint (OpenAI by default). Provider charges apply. Longer meetings use section notes and a final summary, limited to 128 requests and 8 MB of request text per meeting across retries, with up to 4,000 output tokens per request. Recording presets cap estimated audio spending.")
            Text("Saved audio, transcripts and notes are encrypted locally. Unarchived audio expires after 30 days while the app is running and idle. Archive to keep it. Manual reruns upload retained audio again. Exports are readable plaintext. Naming a speaker never enables voice recognition. Revoke this agreement in Preferences to stop processing; saved meetings remain.")
            Button("Agree and continue") { core.send(["action":"accept_agreement","version":text(core.agreement["version"])]) }.buttonStyle(.borderedProminent)
        }.font(.callout).padding(24).background(Color.green.opacity(0.06))
    }
    @ViewBuilder private func meetingActions(_ row: Record) -> some View {
        Button("Rename conversation…",systemImage:"pencil") { renaming = row }.disabled(core.busy)
        Divider()
        Button("Generate summary") { summarize(row) }.disabled(core.busy || core.revision == 0 || !core.accepted || core.summaryProvider["configured"] as? Bool != true)
        Button("Export Markdown…") { core.send(["action":"export_markdown","session_id":row.id,"revision":core.revision]) }.disabled(core.revision == 0)
        Button("People…") { core.send(["action":"people_page"]); showPeople = true }
        Divider()
        Button("Recover interrupted audio") { core.send(["action":"recover","session_id":row.id]) }.disabled(core.busy || !["failed","interrupted"].contains(row.text("status")))
        Button("Resume transcription") { core.send(["action":"resume","session_id":row.id,"language":core.settings["language"] ?? "auto"]) }.disabled(core.busy || !core.accepted || !["failed","interrupted"].contains(row.text("status")))
        Button("Delete conversation…",systemImage:"trash",role:.destructive) { confirmed = row.id }.disabled(core.busy)
    }
    private func summarize(_ row: Record) {
        core.send(["action":"summarize","session_id":row.id,"revision":core.revision,
            "provider":text(core.summaryProvider["provider"]),"model":text(core.summaryProvider["model"])])
    }
    private var transcript: some View {
        VStack(spacing:0) {
            HStack {
                Button("Earlier") { core.loadPage(max(0,core.offset-100)) }.disabled(core.offset == 0)
                Text("\(core.total == 0 ? 0 : core.offset+1)–\(min(core.offset+100,core.total)) of \(core.total)").font(.caption).monospacedDigit()
                Button("Later") { core.loadPage(core.offset+100) }.disabled(core.offset+100 >= core.total)
                Spacer(); Button("Latest") { core.loadPage(-1) }
            }.padding(.horizontal,28).padding(.vertical,12)
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment:.leading,spacing:22) {
                        ForEach(core.rows) { row in TranscriptRow(core:core,row:row).padding(10).background(row.id == core.citationID ? Color.accentColor.opacity(0.13) : Color.clear).clipShape(RoundedRectangle(cornerRadius:8)).id(row.id) }
                        ForEach(core.partials.keys.sorted(),id:\.self) { stream in
                            if let value = core.partials[stream], !value.isEmpty { Text("\(stream == "mic" ? "Microphone" : "Meeting audio") · \(value)").italic().foregroundStyle(.secondary) }
                        }
                        if core.rows.isEmpty { Text("Confirmed speech appears here while recording. Speaker labels are anonymous until you name them.").foregroundStyle(.secondary) }
                    }.padding(28)
                }
                .onChange(of:core.pageSerial) { _ in if let citation = core.citationID { proxy.scrollTo(citation,anchor:.center) } else if let first = core.rows.first { proxy.scrollTo(first.id,anchor:.top) } }
            }
        }
    }
    private var notes: some View {
        ScrollView {
            VStack(alignment:.leading,spacing:22) {
                if core.summaryID == core.selectedID { Label("Preparing cited notes…",systemImage:"sparkles").foregroundStyle(.secondary); ProgressView() }
                if let saved = core.detail["summary"] as? Object, let summary = saved["summary"] as? Object {
                    HStack { Label("CONVERSATION NOTES",systemImage:"text.alignleft").font(.system(size:10,weight:.semibold)).tracking(1.1); Spacer(); Text("\(text(saved["provider"])) · Revision \((saved["revision"] as? Int) ?? 0)").font(.caption) }.foregroundStyle(.secondary)
                    if saved["stale"] as? Bool == true { Text("These notes are outdated. Generate a summary for the current transcript.").foregroundStyle(.orange) }
                    CitedNotes(summary:summary) { anchor in tab = "Transcript"; core.loadPage(0,anchor:anchor) }
                } else if let rolling = core.detail["rolling"] as? Object, let summary = rolling["summary"] as? Object {
                    Text("Section notes · provisional").font(.headline)
                    CitedNotes(summary:summary) { anchor in tab = "Transcript"; core.loadPage(0,anchor:anchor) }
                } else {
                    Text("Room for the important things.").font(.system(size:27,design:.serif))
                    Text(core.busy ? "Your transcript is saved as words arrive. Notes follow when the recording ends." : "Notes appear after a recorded conversation. Configure your summary provider in Preferences, or generate notes from a saved transcript.").foregroundStyle(.secondary)
                    if !text(core.detail["notes"]).isEmpty { Text(verbatim:text(core.detail["notes"])).textSelection(.enabled) }
                }
                Label("Review notes against their sources. Speaker names are manual labels.",systemImage:"checkmark.shield").font(.caption).foregroundStyle(.secondary).padding(.top,8)
            }.padding(28).frame(maxWidth:850,alignment:.leading)
        }
    }
}

struct RecordingReview: View {
    @ObservedObject var core: DesktopClient
    var candidate: Record?
    var initialCapture: Object? = nil
    var onClose: (() -> Void)? = nil
    @Environment(\.dismiss) private var dismiss
    @State private var scope = "microphone"
    @State private var app = ""
    @State private var microphone = "default"
    @State private var pendingRecord: String?
    @State private var recordError: String?
    var body: some View {
        VStack(alignment:.leading,spacing:20) {
            Text(candidate == nil ? "A new conversation" : "Take notes for this call?").font(.system(size:30,design:.serif))
            if let candidate { Label(candidate.text("name"),systemImage:"phone").font(.title3) }
            else {
                Picker("Audio source",selection:$scope) { Text("Microphone only").tag("microphone"); Text("Meeting app + microphone").tag("application"); Text("All system audio + microphone").tag("system") }
                if scope == "application" { Picker("Application",selection:$app) { Text("Choose an application").tag(""); ForEach(objects(core.discovery["apps"])) { row in Text(row.text("name")).tag(row.id) } } }
            }
            Picker("Microphone",selection:$microphone) { ForEach(objects(core.discovery["microphones"])) { row in Text(row.text("name")).tag(row.id) } }
            Text("Browser capture includes every tab in the selected browser. Check that everyone captured has agreed to recording.").font(.callout).foregroundStyle(.secondary)
            VStack(alignment:.leading,spacing:8) {
                Label("Deepgram · Cloud transcription with speaker separation",systemImage:"waveform")
                Label("\(text(core.summaryProvider["provider"])) · \(text(core.summaryProvider["model"])) notes",systemImage:"note.text")
            }.font(.callout).notebookCard()
            let duration = core.settings["duration_s"] as? Int ?? 3600
            Text("Limit: \(duration/60) minutes · estimated audio cap $\(duration == 300 ? "0.25" : duration == 3600 ? "3" : "8"). Provider charges apply. Language: \(text(core.settings["language"]))").font(.callout)
            Text(candidate == nil && scope != "application" ? "Press Stop to end this recording. Notes follow automatically." : (core.settings["stop_on_microphone_release"] as? Bool == true ? "Recording stops after 60 seconds of known app inactivity or after the app releases its microphone. Muted calls may also trigger the countdown; choose Keep recording to continue. Stop is always available." : "Recording stops after 60 seconds of known app inactivity. Other browser tabs can keep audio active; press Stop if the call has ended. Unknown activity keeps recording.")).font(.caption).foregroundStyle(.secondary)
            if core.transcription["configured"] as? Bool != true { Label("Add your Deepgram API key in Preferences before recording.",systemImage:"key").foregroundStyle(.orange) }
            if let recordError { Label(recordError,systemImage:"exclamationmark.triangle").font(.callout).foregroundStyle(ScribeTheme.warning) }
            HStack {
                Button("Not now") { if let candidate { core.send(["action":"dismiss_candidate","candidate_id":candidate.id]) }; closeReview() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button(pendingRecord == nil ? "Record & take notes" : "Starting…") {
                    recordError = nil
                    pendingRecord = core.record(scope:scope,app:app.isEmpty ? nil : app,microphone:microphone,candidate:candidate)
                    if pendingRecord == nil { recordError = core.message ?? "Recording could not be requested. Reconnect and try again." }
                }
                    .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(pendingRecord != nil || !core.accepted || !core.connected || core.busy || core.transcription["configured"] as? Bool != true || (candidate == nil && scope == "application" && app.isEmpty))
            }
        }.padding(32).frame(width:580)
        .onAppear {
            if let initialCapture {
                scope = text(initialCapture["scope"])
                if scope.isEmpty { scope = "microphone" }
                app = text(initialCapture["app_id"])
                microphone = text(initialCapture["microphone_id"])
                if microphone.isEmpty { microphone = "default" }
            }
        }
        .onChange(of:core.requests[pendingRecord ?? ""]) { status in
            guard pendingRecord != nil else { return }
            if status == "accepted" { pendingRecord = nil; closeReview() }
            else if status == "rejected" || status == "unknown" {
                pendingRecord = nil
                recordError = "Recording did not start. Review the source and current app error, then retry or choose New recording."
            }
        }
    }
    private func closeReview() { onClose?(); dismiss() }
}

struct CitedNotes: View {
    let summary: Object
    let citation: (String) -> Void
    var body: some View {
        VStack(alignment:.leading,spacing:22) {
            if let overview = summary["overview"] as? Object {
                VStack(alignment:.leading,spacing:12) {
                    Label("At a glance",systemImage:"quote.bubble").font(.system(size:12,weight:.semibold)).foregroundStyle(ScribeTheme.accent)
                    item(overview)
                }.notebookCard()
            }
            ForEach(["key_points","decisions","action_items","open_questions","uncertainties"],id:\.self) { key in
                let rows = summary[key] as? [Object] ?? []
                if !rows.isEmpty {
                    Text(key.replacingOccurrences(of:"_",with:" ").uppercased()).font(.system(size:11,weight:.semibold)).tracking(1.2).foregroundStyle(.secondary).padding(.top,8)
                    ForEach(Array(rows.enumerated()),id:\.offset) { _, row in item(row) }
                }
            }
        }
    }
    private func item(_ row: Object) -> some View {
        VStack(alignment:.leading,spacing:8) {
            Text(verbatim:text(row["text"]).isEmpty ? text(row["task"]) : text(row["text"])).font(.system(size:14)).lineSpacing(5).textSelection(.enabled)
            if row["task"] != nil { Text("Owner: \(text(row["owner"]).isEmpty ? "Not stated" : text(row["owner"])) · Due: \(text(row["due"]).isEmpty ? "Not stated" : text(row["due"]))").font(.caption).foregroundStyle(.secondary) }
            ViewThatFits(in:.horizontal) {
                HStack(spacing:6) { sources(row) }
                VStack(alignment:.leading,spacing:6) { sources(row) }
            }
        }
    }
    @ViewBuilder private func sources(_ row:Object) -> some View {
        ForEach(Array((row["segment_ids"] as? [String] ?? []).enumerated()),id:\.offset) { index, id in
            Button { citation(id) } label: { Label("Source \(index+1)",systemImage:"arrow.up.right").font(.system(size:10,weight:.medium)).padding(.horizontal,7).padding(.vertical,4) }
                .buttonStyle(.borderless).foregroundStyle(ScribeTheme.accent)
                .background(ScribeTheme.subtle,in:Capsule()).accessibilityLabel("Source \(index+1)")
        }
    }
}


@main enum DesktopEntry {
    @MainActor static func main() {
        if CommandLine.arguments.contains("--desktop-smoke") {
            Task { await smoke() }; RunLoop.main.run()
        } else { ScribeLoopApp.main() }
    }
    @MainActor private static func smoke() async {
        // Run only against the separately assembled preview fixture bundle.
        guard Bundle.main.bundleIdentifier == "ai.scribeloop.desktop.preview" else {
            print("{\"ok\":false,\"reason\":\"preview bundle required\"}"); exit(1)
        }
        let core = DesktopClient(database:"/tmp/unused-preview.sqlcipher")
        func wait(_ predicate: () -> Bool) async -> Bool {
            for _ in 0..<700 {
                if predicate() { return true }; try? await Task.sleep(nanoseconds:50_000_000)
            }; return false
        }
        func fail(_ stage: String) async {
            core.stop(); print("{\"ok\":false,\"stage\":\"\(stage)\"}")
            try? await Task.sleep(nanoseconds:3_000_000_000); exit(1)
        }
        core.start()
        guard await wait({core.connected && core.rows.count > 0}), core.total == 230 else { await fail("startup"); return }
        let sid = core.selectedID
        core.loadPage(0)
        guard await wait({core.offset == 0 && core.rows.count == 100}) else { await fail("paging"); return }
        guard let agreementID = core.send(["action":"accept_agreement","version":text(core.agreement["version"])]),
            await wait({core.accepted && core.requests[agreementID] == "accepted"}) else { await fail("agreement"); return }
        guard let settingsID = core.send(["action":"save_settings","settings":["language":"vi"]]),
            await wait({text(core.settings["language"]) == "vi" && core.requests[settingsID] == "accepted"}) else { await fail("settings"); return }
        guard core.speakerRoster.count == 3, let first = core.speakerRoster.first, let sid else { await fail("roster"); return }
        guard let nameID = core.send(["action":"name_speaker","session_id":sid,"revision":core.revision,"speaker_id":first.id,"name":"Alice"]),
            await wait({core.speakerRoster.first?.text("name") == "Alice" && core.requests[nameID] == "accepted"}) else { await fail("name person"); return }
        core.playback.open(sid)
        guard await wait({core.playback.duration == 140}) else { await fail("audio duration"); return }
        core.playback.seek(90,autoplay:false)
        guard await wait({!core.playback.loading && core.playback.position == 90}) else { await fail("audio seek"); return }
        core.playback.rate = 1.5
        core.stopAudio()
        core.reconnect()
        guard await wait({core.connected && core.selectedID == sid && core.rows.count > 0}) else { await fail("reconnect"); return }
        guard let pauseID = core.send(["action":"set_detection","enabled":false]),
            await wait({!core.detection && core.requests[pauseID] == "accepted"}) else { await fail("pause"); return }
        guard let revokeID = core.send(["action":"revoke_agreement"]),
            await wait({!core.accepted && core.requests[revokeID] == "accepted"}) else { await fail("revoke agreement"); return }
        core.stop()
        guard await wait({!core.running && core.state == "Stopped"}) else { await fail("shutdown"); return }
        guard await PlaybackSmoke.run() else { await fail("native playback controls"); return }
        print("{\"ok\":true,\"playback_controls\":true,\"protocol\":2,\"encrypted_fixture\":true,\"paged_rows\":230,\"speaker_naming\":true,\"audio_seek\":true,\"agreement\":true,\"settings\":true,\"reconnect\":true,\"pause\":true,\"shutdown\":true}")
        exit(0)
    }
}
