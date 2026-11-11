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
    @StateObject private var calendar = CalendarCoordinator()
    @Environment(\.openWindow) private var openWindow
    var body: some Scene {
        Window("ScribeLoop",id:"notebook") {
            Notebook(core:core,calendar:calendar).onAppear { delegate.core = core; core.onCandidate = { [weak controller = delegate] row in controller?.showCall(row) }; if !core.running { core.start() } }
        }.defaultSize(width:1160,height:780)
        .commands {
            CommandGroup(after:.help) {
                Button("Review setup…") {
                    openWindow(id:"notebook"); NSApp.activate(ignoringOtherApps:true)
                    NotificationCenter.default.post(name:Notification.Name("ScribeLoopReviewSetup"),object:nil)
                }.disabled(!core.connected || core.busy)
            }
        }
        MenuBarExtra {
            Text(core.activeID == nil ? (core.summaryID == nil ? core.monitorLabel : "Preparing notes") : "Recording · microphone and selected source")
            if core.activeID != nil { Button("Stop recording",role:.destructive) { core.stopRecording() } }
            if !objects(core.discovery["candidates"]).isEmpty { Text("Possible call detected · open ScribeLoop to review") }
            Button("Open ScribeLoop") { openWindow(id:"notebook"); NSApp.activate(ignoringOtherApps:true) }
            Button("Settings…") {
                if !NSApp.sendAction(Selector(("showSettingsWindow:")),to:nil,from:nil) {
                    NSApp.sendAction(Selector(("showPreferencesWindow:")),to:nil,from:nil)
                }
                NSApp.activate(ignoringOtherApps:true)
            }
            Divider()
            Button("Quit ScribeLoop") { NSApp.terminate(nil) }
        } label: { Image(systemName:core.activeID == nil ? "waveform" : "record.circle.fill").accessibilityLabel(core.activeID == nil ? "ScribeLoop" : "ScribeLoop recording") }
        Settings { Preferences(core:core,calendar:calendar).frame(width:650,height:720) }
    }
}

struct SettingsDestination:Identifiable { let id:String }

/// Chat-style floating jump buttons for the transcript box. Shown only while
/// reading away from the live bottom; hidden again once following resumes.
private struct TranscriptJumpButton: View {
    let icon:String
    let label:String
    var badge = false
    let action:() -> Void
    var body: some View {
        Button(action:action) {
            ZStack(alignment:.topTrailing) {
                Image(systemName:icon).font(.system(size:13,weight:.semibold))
                    .foregroundStyle(ScribeTheme.accent)
                    .frame(width:32,height:32)
                    .background(ScribeTheme.panel,in:Circle())
                    .overlay { Circle().strokeBorder(ScribeTheme.border.opacity(0.65),lineWidth:1) }
                if badge { Circle().fill(ScribeTheme.accent).frame(width:9,height:9).offset(x:2,y:2) }
            }
            .shadow(color:.black.opacity(0.10),radius:3,y:1)
        }.buttonStyle(.plain)
            .help(label)
            .accessibilityLabel(label)
    }
}

struct Notebook: View {
    @ObservedObject var core: DesktopClient
    @ObservedObject var calendar:CalendarCoordinator
    @AppStorage("firstUseSetupCompleted") private var setupCompleted = false
    @State private var showSetup = false
    @State private var showCalendar = false
    @State private var calendarConversation:Record?
    @State private var emailSharing:EmailSharingContext?
    @State private var recording = false
    @State private var candidate: Record?
    @State private var retryCapture: Object?
    @State private var preferencesRequest:SettingsDestination?
    @State private var calendarSettingsRequested = false
    @State private var showPeople = false
    @State private var showSearch = false
    @State private var showAsk = false
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
                Button { calendarConversation = nil; showCalendar = true } label: { Label("Calendar",systemImage:"calendar").frame(maxWidth:.infinity) }.buttonStyle(.bordered)
                Button { showSearch = true } label: { Label("Search",systemImage:"magnifyingglass").frame(maxWidth:.infinity) }.buttonStyle(.bordered)
                    .keyboardShortcut("f",modifiers:.command).disabled(!core.accepted || !core.connected || core.busy)
                Button { showAsk = true } label: { Label("Ask",systemImage:"sparkles").frame(maxWidth:.infinity) }.buttonStyle(.bordered)
                    .disabled(!core.accepted || !core.connected || core.busy || core.summaryProvider["configured"] as? Bool != true)
                    .keyboardShortcut("k",modifiers:.command)
                    .help(core.summaryProvider["configured"] as? Bool == true ? "Ask questions about saved meetings" : "Set up a notes provider in Preferences to use Ask")
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
                    Button { NSApp.activate(ignoringOtherApps:true); preferencesRequest = SettingsDestination(id:"Recording") } label: { Image(systemName:"gearshape").padding(4) }.buttonStyle(.borderless).help("Preferences").disabled(!core.connected)
                }.padding(10).background(ScribeTheme.panel,in:RoundedRectangle(cornerRadius:12))
            }.padding(18).background(ScribeTheme.sidebar)
            .navigationSplitViewColumnWidth(min:260,ideal:290,max:380)
        } detail: {
            VStack(spacing:0) {
                if let message = core.message, !message.isEmpty {
                    HStack(alignment:.top) { Image(systemName:"info.circle"); Text(message).textSelection(.enabled); Spacer(); Button("Dismiss") { core.message = nil } }.font(.callout).padding(14).background(Color.orange.opacity(0.09))
                }
                if core.uncertainActions {
                    HStack { Spacer(); Button("I reviewed the current state") { core.acknowledgeUncertainActions() } }.font(.callout).padding(.horizontal,14).padding(.vertical,6)
                }
                if !core.accepted && core.connected { agreementView }
                else if let current = candidates.first, core.activeID == nil {
                    HStack { Label("Possible call in \(current.text("name"))",systemImage:"phone.arrow.up.right"); Spacer()
                        Button("Not now") { core.send(["action":"dismiss_candidate","candidate_id":current.id]) }
                        Button("Review recording") { candidate = current; retryCapture = nil; recording = true }.buttonStyle(.borderedProminent).disabled(core.busy)
                    }.padding(18).background(Color.green.opacity(0.08))
                }
                if let activeID = core.activeID {
                    HStack { Image(systemName:core.recordingPaused ? "pause.circle.fill" : "record.circle.fill").foregroundStyle(core.recordingPaused ? .orange : .red); Text(core.captureStopping ? "Stopping capture…" : core.recordingPaused ? "Paused · New audio is excluded" : "Recording")
                        if let row = core.sessions.first(where: { $0.id == activeID }) { TimelineView(.periodic(from:.now,by:1)) { _ in Text(Date(timeIntervalSince1970:Double(row.int("started_at"))/1000),style:.timer).monospacedDigit() } }
                        Spacer()
                        Button { core.toggleRecordingPause() } label: {
                            Label(core.recordingPaused ? "Continue" : "Pause",systemImage:core.recordingPaused ? "play.fill" : "pause.fill")
                        }.disabled(!core.connected || core.captureStopping)
                        Button("Stop recording",role:.destructive) { core.stopRecording() }.keyboardShortcut(".",modifiers:.command)
                    }.padding(18)
                    if core.recordingPaused {
                        Text("Continue to include new audio. Microphone access stays active; the recording time limit still applies.")
                            .font(.caption).foregroundStyle(.secondary).padding(.horizontal,18).padding(.bottom,10)
                    }
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
                if core.connected && core.accepted && (showSetup || (core.sessions.isEmpty && !setupCompleted)) {
                    FirstUseSetup(core:core,openSettings:{ preferencesRequest = SettingsDestination(id:$0) },
                        testRecording:{ candidate = nil; retryCapture = nil; recording = true },
                        finish:{ setupCompleted = true; showSetup = false })
                } else if let selected = core.selected {
                    VStack(alignment:.leading,spacing:18) {
                        HStack(alignment:.top,spacing:20) {
                            VStack(alignment:.leading,spacing:12) {
                                Text(selected.text("title")).font(.system(size:30,weight:.medium,design:.serif)).lineLimit(2).textSelection(.enabled)
                                HStack(spacing:10) {
                                    MeetingStatus(status:selected.text("status"))
                                    Text(Date(timeIntervalSince1970:Double(selected.int("started_at"))/1000),style:.date)
                                    Text("· \(core.total) \(core.total == 1 ? "segment" : "segments")")
                                }.font(.caption).foregroundStyle(.secondary)
                                if let reason = recordingEndLabel(selected.text("end_reason")) {
                                    Text(reason).font(.caption).foregroundStyle(.secondary)
                                }
                                if !selected.text("notes_status").isEmpty {
                                    Text(selected.text("notes_status")).font(.callout).foregroundStyle(.secondary)
                                }
                                if let event = core.detail["calendar_event"] as? Object {
                                    Button { calendarConversation = selected; showCalendar = true } label: { Label(text(event["title"]),systemImage:"calendar") }.buttonStyle(.borderless).font(.callout)
                                }
                                if !selected.text("transcription_warning").isEmpty {
                                    Label(selected.text("transcription_warning"),systemImage:"exclamationmark.triangle").font(.callout).foregroundStyle(ScribeTheme.warning)
                                }
                                if let failure = selected.raw["summary_failure"] as? Object {
                                    Label(text(failure["message"]),systemImage:"exclamationmark.triangle")
                                        .font(.callout).foregroundStyle(ScribeTheme.warning)
                                }
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
                            Button("Email",systemImage:"envelope") { emailSharing = EmailSharingContext(conversation:selected,detail:core.detail) }
                                .disabled(!canShare(selected))
                            Menu("Meeting") { meetingActions(selected) }.disabled(!core.connected)
                            }.controlSize(.regular).font(.callout)
                        }
                        Picker("Meeting view",selection:$tab) { Label("Notes",systemImage:"note.text").tag("Notes"); Label("Transcript",systemImage:"text.bubble").tag("Transcript"); Label("Audio",systemImage:"waveform").tag("Audio") }.pickerStyle(.segmented).frame(maxWidth:390)
                    }.padding(28).id(selected.id+selected.text("title")+selected.text("status"))
                    Divider()
                    if tab == "Transcript" { transcript }
                    else if tab == "Audio" { AudioView(core:core) }
                    else { notes }
                } else if core.connected && !core.accepted {
                    Spacer()
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
        .onReceive(NotificationCenter.default.publisher(for:NSApplication.didBecomeActiveNotification)) { _ in core.refreshMeetingAudioPermission() }
        .onReceive(NotificationCenter.default.publisher(for:Notification.Name("ScribeLoopOpenSettings"))) { _ in
            NSApp.activate(ignoringOtherApps:true)
            preferencesRequest = SettingsDestination(id:"Models")
        }
        .onReceive(NotificationCenter.default.publisher(for:Notification.Name("ScribeLoopReviewSetup"))) { _ in showSetup = true }
        .onChange(of:core.selectedID) { value in if let value { core.select(value) } }
        .sheet(isPresented:$showCalendar,onDismiss:{
            if calendarSettingsRequested { calendarSettingsRequested = false; preferencesRequest = SettingsDestination(id:"Calendar") }
        }) { CalendarPanel(core:core,calendar:calendar,conversation:calendarConversation,onOpenSettings:{ calendarSettingsRequested = true; showCalendar = false }) }
        .sheet(item:$emailSharing) { context in EmailSharingPanel(core:core,context:context) }
        .sheet(isPresented:$recording) { RecordingReview(core:core,candidate:candidate,initialCapture:retryCapture) }
        .sheet(item:$preferencesRequest) { destination in Preferences(core:core,calendar:calendar,initialPage:destination.id).frame(width:650,height:720) }
        .sheet(isPresented:$showPeople) { PeopleView(core:core).frame(width:650,height:680) }
        .sheet(isPresented:$showSearch) {
            MeetingSearchView(core:core) { result in
                showSearch=false
                core.select(result.text("session_id"))
                if result.text("source_type") == "summary" { tab="Notes" }
                else if result.text("source_type") == "transcript" {
                    tab="Transcript"
                    core.loadPage(0,anchor:result.text("source_id"))
                } else { tab="Transcript" }
            }.frame(width:760,height:620)
        }
        .sheet(isPresented:$showAsk) {
            MeetingAskView(core:core) { citation in
                showAsk=false
                core.select(citation.text("meeting_id"))
                if citation.text("source_type") == "summary" { tab="Notes" }
                else { tab="Transcript"; core.loadPage(0,anchor:citation.text("source_id")) }
            }.frame(width:820,height:700)
        }
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
            Text("You choose Record or Not now for every call. Cloud transcription sends audio to your selected provider; notes and optional transcript cleanup send text to their configured providers. Provider charges may apply.")
            Text("Your notebook is encrypted on this Mac. Audio is kept for 30 days unless you archive it. You can stop recording at any time.")
            DisclosureGroup("Privacy and storage details") {
                Text("Browser recording includes all tabs in that browser. Unarchived audio expires while the app is running and idle. Reruns upload saved audio again. Exports are readable outside the app. Naming people does not enable voice recognition. Local transcription, cleanup and notes can be configured independently; check each destination in setup.").font(.caption).foregroundStyle(.secondary)
            }
            Button("Agree and set up") { core.send(["action":"accept_agreement","version":text(core.agreement["version"])]) }.buttonStyle(.borderedProminent)
        }.font(.callout).padding(24).background(Color.green.opacity(0.06))
    }
    @ViewBuilder private func meetingActions(_ row: Record) -> some View {
        Button("Rename conversation…",systemImage:"pencil") { renaming = row }.disabled(core.busy)
        Button("Link calendar event…",systemImage:"calendar") { calendarConversation = row; showCalendar = true }
        Divider()
        Button("Generate summary") { summarize(row) }.disabled(core.busy || core.revision == 0 || !core.accepted || core.summaryProvider["configured"] as? Bool != true)
        Button("Export Markdown…") { core.send(["action":"export_markdown","session_id":row.id,"revision":core.revision]) }.disabled(core.revision == 0)
        Button("Share by email…",systemImage:"envelope") { emailSharing = EmailSharingContext(conversation:row,detail:core.detail) }.disabled(!canShare(row))
        Button("People…") { core.send(["action":"people_page"]); showPeople = true }
        Divider()
        Button("Recover interrupted audio") { core.send(["action":"recover","session_id":row.id]) }.disabled(core.busy || !["failed","interrupted"].contains(row.text("status")))
        Button("Resume transcription") { core.resumeTranscription(row.id) }.disabled(core.busy || !core.accepted || !["failed","interrupted"].contains(row.text("status")))
        Button("Delete conversation…",systemImage:"trash",role:.destructive) { confirmed = row.id }.disabled(core.busy)
    }
    private func canShare(_ row:Record) -> Bool {
        core.connected && !core.busy && row.text("source") == "meeting" && ["completed","failed","interrupted"].contains(row.text("status"))
            && text((core.detail["session"] as? Object)?["id"]) == row.id
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
                Spacer()
                Text(core.transcriptFollow.following ? (core.activeID == core.selectedID ? "Following live" : "At latest") : "Reading earlier")
                    .font(.caption).foregroundStyle(.secondary)
            }.padding(.horizontal,28).padding(.vertical,12)
            ScrollViewReader { proxy in
                ScrollView {
                    LazyVStack(alignment:.leading,spacing:14) {
                        ForEach(core.transcriptTurns) { turn in TranscriptRow(core:core,busy:core.busy,citationID:core.citationID,turn:turn) }
                        ForEach(core.partials.keys.sorted(),id:\.self) { stream in
                            if let value = core.partials[stream], !value.isEmpty { Text("Recognizing speaker… · \(value)").italic().foregroundStyle(.secondary)
                                .frame(maxWidth:.infinity,alignment:["application","system"].contains(core.transcriptCaptureScope) && stream == "mic" ? .trailing : .leading) }
                        }
                        if core.rows.isEmpty { Text("Confirmed speech appears here while recording. Speaker labels are anonymous until you name them.").foregroundStyle(.secondary) }
                        Color.clear.frame(height:1).id("transcript-bottom")
                    }.padding(28)
                        .background(TranscriptScrollTracking(following:core.transcriptFollow.following,
                            onUserScroll:{ core.transcriptUserScrolled(nearBottom:$0,nearTop:$1) }).frame(width:0,height:0).allowsHitTesting(false))
                }
                .onChange(of:core.pageSerial) { _ in
                    if let citation = core.citationID { proxy.scrollTo(citation,anchor:.center) }
                    else if core.pageScrollToLatest { proxy.scrollTo("transcript-bottom",anchor:.bottom) }
                    else if let first = core.rows.first { proxy.scrollTo(first.id,anchor:.top) }
                }
            }
            .overlay(alignment:.bottomTrailing) {
                if !core.transcriptFollow.following {
                    VStack(spacing:10) {
                        if !(core.offset == 0 && core.transcriptFollow.nearTop) {
                            TranscriptJumpButton(icon:"chevron.up",label:"Return to the top of the transcript") { core.loadPage(0) }
                        }
                        TranscriptJumpButton(icon:"chevron.down",
                            label:core.transcriptFollow.unread ? "New speech · Jump to latest" : "Jump to the latest speech",
                            badge:core.transcriptFollow.unread) { core.loadPage(-1) }
                    }
                    .padding(.trailing,28).padding(.bottom,20)
                    .transition(.opacity)
                }
            }
            .animation(.easeOut(duration:0.15),value:core.transcriptFollow)
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

struct FirstUseSetup: View {
    @ObservedObject var core:DesktopClient
    var openSettings:(String)->Void
    var testRecording:()->Void
    var finish:()->Void
    private var notesSettled:Bool { !core.wantsNotes || core.notesReady }
    private var ready:Bool { core.transcriptionReady && notesSettled && core.microphoneReady }
    private func status(_ title:String,_ detail:String,ready:Bool) -> some View {
        HStack(alignment:.top,spacing:12) {
            Image(systemName:ready ? "checkmark.circle.fill" : "circle").foregroundStyle(ready ? ScribeTheme.accent : .secondary)
            VStack(alignment:.leading,spacing:4) { Text(title).font(.headline); Text(detail).font(.callout).foregroundStyle(.secondary) }
            Spacer()
        }
    }
    var body:some View {
        ScrollView {
            VStack(alignment:.leading,spacing:22) {
                Text("Make room for the conversation.").font(.system(size:30,design:.serif))
                Text("A few choices, then you’re ready. Nothing records until you choose Record.").foregroundStyle(.secondary)
                VStack(alignment:.leading,spacing:12) {
                    status("1. Connect transcription",core.transcriptionReady ? core.audioDestination+" · Configuration saved" : "Choose where your audio is transcribed and add your API key.",ready:core.transcriptionReady)
                    Button(core.transcriptionReady ? "Change transcription…" : "Connect transcription…") { openSettings("Models") }
                    Text("Saving a key does not verify provider access or credits. No paid API test runs automatically.").font(.caption).foregroundStyle(.secondary)
                }.notebookCard()
                VStack(alignment:.leading,spacing:12) {
                    status("2. Choose your notes",core.notesDestination,ready:notesSettled)
                    HStack {
                        Button("Configure notes…") { openSettings("Models") }
                        if core.wantsNotes {
                            Button("Use transcript only") { core.send(["action":"save_settings","settings":["automatic_notes":false]]) }.disabled(core.busy)
                        } else {
                            Button("Enable automatic notes") { core.send(["action":"save_settings","settings":["automatic_notes":true]]) }.disabled(core.busy)
                        }
                    }
                    Text("Transcript cleanup, calendars and sign-in are optional. Add them later in Settings.").font(.caption).foregroundStyle(.secondary)
                }.notebookCard()
                VStack(alignment:.leading,spacing:12) {
                    status("3. Allow your microphone",core.microphoneReady ? "Ready to record your voice." : "macOS needs your permission to capture your voice.",ready:core.microphoneReady)
                    if !core.microphoneReady {
                        Button("Allow microphone") { core.send(["action":"microphone_permission"]) }.disabled(core.busy)
                        Button("Open microphone settings") { NSWorkspace.shared.open(URL(string:"x-apple.systempreferences:com.apple.preference.security?Privacy_Microphone")!) }
                    }
                    Divider()
                    Text("Meeting apps & browsers").font(.headline)
                    Text("Allow ScribeLoop to hear Teams, Slack, Zoom and browser calls. macOS calls this Screen & System Audio Recording. Microphone-only recording does not need it.").font(.callout).foregroundStyle(.secondary)
                    Button(core.meetingAudioPermissionLabel) { core.requestMeetingAudioAccess() }.disabled(core.busy || core.meetingAudioPermissionGranted)
                    Button("Check meeting-audio access again") {
                        core.captureAccessMessage = "Checking access…"
                        core.send(["action":"check_capture_access"])
                    }.disabled(core.busy)
                    if let result = core.captureAccessMessage { Text(result).font(.caption).foregroundStyle(.secondary) }
                }.notebookCard()
                Text("Languages: \(core.transcriptionLanguageLabel)").font(.callout)
                Button("Change languages or permissions…") { openSettings("Recording") }
                HStack {
                    Button("Try a short recording…") { testRecording() }.disabled(!ready || core.busy)
                    Spacer()
                    Button("Finish setup") { finish() }.buttonStyle(.borderedProminent).tint(ScribeTheme.action).disabled(!ready || core.busy)
                }
                Text(ready ? "The test is optional. Detected calls will offer Record / Not now; you can also start manually." : "Complete transcription, choose notes or transcript-only, and allow the microphone to finish.").font(.caption).foregroundStyle(.secondary)
            }.padding(30).frame(maxWidth:740,alignment:.leading).frame(maxWidth:.infinity)
        }
    }
}

struct MeetingSearchView: View {
    @ObservedObject var core:DesktopClient
    let open:(Record)->Void
    @Environment(\.dismiss) private var dismiss
    @State private var query=""
    @State private var meetingID=""
    @State private var personID=""
    @State private var dateRange=0
    @State private var debounce:Task<Void,Never>?
    private var hybrid:Bool { text(core.settings["search_mode"]) == "hybrid" }
    private var remoteEmbeddings:Bool { hybrid && ["OpenAI","Custom"].contains(text(core.settings["embedding_provider"])) }

    private func runSearch() {
        let now=Int(Date().timeIntervalSince1970*1000)
        let from=dateRange == 0 ? nil : now-dateRange*86_400_000
        core.search(query,meetingID:meetingID.isEmpty ? nil : meetingID,
                    personID:personID.isEmpty ? nil : personID,dateFrom:from)
    }
    private func scheduleSearch() {
        debounce?.cancel()
        guard !hybrid else { return }
        debounce=Task { @MainActor in
            try? await Task.sleep(nanoseconds:250_000_000)
            guard !Task.isCancelled else { return }
            runSearch()
        }
    }
    var body:some View {
        VStack(spacing:0) {
            HStack {
                VStack(alignment:.leading,spacing:4) {
                    Text("Search conversations").font(.system(size:24,weight:.semibold,design:.serif))
                    Text(remoteEmbeddings ? "Hybrid search · query text and indexed passages use your embedding provider" : "Titles, current transcripts and current notes · searched privately on this Mac")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer(); Button("Done") { dismiss() }.keyboardShortcut(.cancelAction)
            }.padding(22)
            Divider()
            VStack(spacing:12) {
                HStack {
                    Image(systemName:"magnifyingglass").foregroundStyle(.secondary)
                    TextField("Search decisions, names or phrases",text:$query)
                        .textFieldStyle(.plain).font(.title3)
                        .onSubmit { runSearch() }
                        .onChange(of:query) { _ in scheduleSearch() }
                    if hybrid {
                        Button("Search") { runSearch() }.buttonStyle(.borderedProminent)
                            .disabled(core.searchLoading || query.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty)
                    }
                    if core.searchLoading { ProgressView().controlSize(.small) }
                    if !query.isEmpty {
                        Button { query=""; core.search("") } label: { Image(systemName:"xmark.circle.fill") }
                            .buttonStyle(.borderless).accessibilityLabel("Clear search")
                    }
                }.padding(12).background(ScribeTheme.panel,in:RoundedRectangle(cornerRadius:10))
                HStack {
                    Picker("Meeting",selection:$meetingID) {
                        Text("All meetings").tag("")
                        ForEach(core.sessions) { row in Text(row.text("title")).tag(row.id) }
                    }.frame(maxWidth:260)
                    Picker("Date",selection:$dateRange) {
                        Text("Any time").tag(0); Text("Past 7 days").tag(7)
                        Text("Past 30 days").tag(30); Text("Past year").tag(365)
                    }.frame(maxWidth:180)
                    Picker("Person",selection:$personID) {
                        Text("Anyone").tag("")
                        ForEach(core.people) { row in Text(row.text("canonical_name")).tag(row.id) }
                    }.frame(maxWidth:220)
                }.onChange(of:meetingID) { _ in if !hybrid { runSearch() } }
                    .onChange(of:dateRange) { _ in if !hybrid { runSearch() } }
                    .onChange(of:personID) { _ in if !hybrid { runSearch() } }
            }.padding(18)
            Divider()
            Group {
                if query.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty {
                    searchEmpty("Find a conversation","text.magnifyingglass",
                                "Try a project name, a decision, or words someone used.")
                } else if !core.searchLoading && core.searchResults.isEmpty {
                    searchEmpty("No matches","magnifyingglass",
                                "Try fewer words or broaden the meeting, date or person filters.")
                } else {
                    List(core.searchResults) { result in
                        Button { open(result) } label: {
                            VStack(alignment:.leading,spacing:7) {
                                HStack {
                                    Text(result.text("title")).font(.headline).lineLimit(1)
                                    Spacer()
                                    Text(result.text("source_type").capitalized).font(.caption).foregroundStyle(.secondary)
                                    if result.int("start_ms") > 0 {
                                        Text(String(format:"%d:%02d",result.int("start_ms")/60000,result.int("start_ms")/1000%60))
                                            .font(.caption.monospacedDigit()).foregroundStyle(.secondary)
                                    }
                                }
                                if !result.text("speaker_name").isEmpty { Text(result.text("speaker_name")).font(.caption).foregroundStyle(ScribeTheme.accent) }
                                Text(result.text("snippet")).font(.body).lineLimit(3).multilineTextAlignment(.leading)
                                Text(Date(timeIntervalSince1970:Double(result.int("started_at"))/1000),style:.date)
                                    .font(.caption).foregroundStyle(.secondary)
                            }.padding(.vertical,7).contentShape(Rectangle())
                        }.buttonStyle(.plain)
                    }.listStyle(.inset)
                }
            }.frame(maxWidth:.infinity,maxHeight:.infinity)
            if !core.searchIndex.isEmpty {
                Divider()
                HStack {
                    let pending=core.searchIndex["pending"] as? Int ?? 0
                    Label(pending == 0 ? "Index current" : "Indexing · \(pending) meetings remaining",
                          systemImage:pending == 0 ? "checkmark.circle" : "clock")
                    Spacer()
                    let semantic=core.searchIndex["semantic"] as? Object ?? [:]
                    Text(semantic["enabled"] as? Bool == true ? "Hybrid · \(text(semantic["provider"]))" : hybrid ? "Keyword results · semantic search unavailable" : text(core.searchIndex["engine"]) == "fts5" ? "Full-text search" : "Basic search · full-text index unavailable")
                }.font(.caption).foregroundStyle(.secondary).padding(.horizontal,20).padding(.vertical,10)
                if let semantic=core.searchIndex["semantic"] as? Object, !text(semantic["message"]).isEmpty {
                    Text(text(semantic["message"])).font(.caption).foregroundStyle(ScribeTheme.warning).padding(.horizontal,20).padding(.bottom,10)
                }
            }
        }.background(ScribeTheme.canvas)
        .onAppear { core.send(["action":"people_page"]) }
        .onDisappear { debounce?.cancel(); core.search("") }
    }
    private func searchEmpty(_ title:String,_ icon:String,_ detail:String)->some View {
        VStack(spacing:10) {
            Image(systemName:icon).font(.system(size:30)).foregroundStyle(.secondary)
            Text(title).font(.title3.weight(.semibold))
            Text(detail).foregroundStyle(.secondary)
        }.frame(maxWidth:.infinity,maxHeight:.infinity)
    }
}

struct MeetingAskView: View {
    @ObservedObject var core:DesktopClient
    let open:(Record)->Void
    @Environment(\.dismiss) private var dismiss
    @State private var question=""
    @State private var scope="current"
    @State private var selected=Set<String>()
    @State private var showMeetings=false
    @State private var previousAnswerID:String?

    private var scopeIDs:[String] { scope == "selected" ? Array(selected).sorted() : [] }
    private var scopeReady:Bool { scope != "current" || core.selectedID != nil }
    private var answer:Object { core.askResult["answer"] as? Object ?? [:] }
    private var evidence:[Record] { objects((core.askResult["evidence"] as? Object)?["passages"]) }
    private var scopeLabel:String {
        if scope == "current" { return core.selected?.text("title") ?? "No meeting selected" }
        if scope == "selected" { return "\(selected.count) chosen meeting\(selected.count == 1 ? "" : "s")" }
        let searched=(((core.askResult["evidence"] as? Object)?["scope"] as? Object)?["meeting_ids"] as? [String])?.count
        return searched.map { "All \($0) saved meetings" } ?? "All saved meetings"
    }
    private var previousQuestion:String {
        guard let previousAnswerID else { return "" }
        return core.askHistory.first(where:{$0.id==previousAnswerID})?.text("question") ??
            text((core.askResult["evidence"] as? Object)?["question"])
    }
    private func refreshHistory() {
        guard scopeReady, scope != "selected" || !selected.isEmpty else { core.askHistory=[]; return }
        core.loadAskHistory(scope:scope,currentID:core.selectedID,selectedIDs:scopeIDs)
    }
    private func submit() {
        core.ask(question,scope:scope,currentID:core.selectedID,selectedIDs:scopeIDs,
                 previousAnswerID:previousAnswerID)
    }
    var body:some View {
        VStack(spacing:0) {
            HStack {
                VStack(alignment:.leading,spacing:4) {
                    Text("Ask your meetings").font(.system(size:24,weight:.semibold,design:.serif))
                    Text("Answers use current saved transcripts and show the evidence behind each claim.")
                        .font(.caption).foregroundStyle(.secondary)
                }
                Spacer(); Button("Done") { if core.askLoading { core.cancelAsk() }; dismiss() }.keyboardShortcut(.cancelAction)
            }.padding(22)
            Divider()
            HStack(spacing:14) {
                Picker("Scope",selection:$scope) {
                    Text("Current meeting").tag("current")
                    Text("Chosen meetings").tag("selected")
                    Text("All meetings").tag("all")
                }.pickerStyle(.segmented).labelsHidden().accessibilityLabel("Meeting scope").frame(maxWidth:430)
                if scope == "selected" {
                    Button(showMeetings ? "Hide list" : "Choose…") { showMeetings.toggle() }.fixedSize()
                }
                Spacer()
                Text("\(text(core.summaryProvider["provider"])) · \(text(core.summaryProvider["model"]))")
                    .font(.caption).foregroundStyle(.secondary).lineLimit(1)
            }.padding(.horizontal,22).padding(.vertical,12)
            HStack(spacing:6) {
                Image(systemName:"scope").foregroundStyle(ScribeTheme.accent)
                Text("Searching: \(scopeLabel)").lineLimit(1)
                Spacer()
            }.font(.caption).foregroundStyle(.secondary).padding(.horizontal,22).padding(.bottom,10)
            if showMeetings && scope == "selected" {
                VStack(spacing:8) {
                    HStack {
                        Text("Choose meetings").font(.headline)
                        Spacer()
                        Button("Select visible") { selected=Set(core.sessions.map(\.id)); refreshHistory() }.buttonStyle(.borderless)
                        Button("Clear") { selected=[]; refreshHistory() }.buttonStyle(.borderless).disabled(selected.isEmpty)
                    }
                    ScrollView {
                        LazyVStack(spacing:4) {
                        ForEach(core.sessions) { row in
                            Button {
                                if selected.contains(row.id) { selected.remove(row.id) } else { selected.insert(row.id) }
                                refreshHistory()
                            } label: {
                                HStack {
                                    Image(systemName:selected.contains(row.id) ? "checkmark.square.fill" : "square")
                                        .foregroundStyle(selected.contains(row.id) ? ScribeTheme.accent : .secondary)
                                    Text(row.text("title")).lineLimit(2); Spacer()
                                    Text(Date(timeIntervalSince1970:Double(row.int("started_at"))/1000),style:.date)
                                        .font(.caption).foregroundStyle(.secondary)
                                }.padding(7).contentShape(Rectangle())
                            }.buttonStyle(.plain).accessibilityLabel("\(selected.contains(row.id) ? "Remove" : "Add") \(row.text("title"))")
                        }
                        }
                    }.frame(maxHeight:150)
                }.padding(12).background(ScribeTheme.panel,in:RoundedRectangle(cornerRadius:10))
                    .padding(.horizontal,22).padding(.bottom,12)
            }
            Divider()
            ScrollView {
                VStack(alignment:.leading,spacing:16) {
                    if let error=core.askError { Label(error,systemImage:"exclamationmark.triangle").foregroundStyle(ScribeTheme.warning) }
                    if !answer.isEmpty {
                        let claims=objects(answer["claims"])
                        if answer["insufficient_evidence"] as? Bool == true {
                            Label(text(answer["caveat"]).isEmpty ? "Not found in the retrieved evidence." : text(answer["caveat"]),
                                  systemImage:"questionmark.bubble").foregroundStyle(.secondary)
                        }
                        ForEach(Array(claims.enumerated()),id:\.offset) { _,claim in
                            VStack(alignment:.leading,spacing:8) {
                                Text(claim.text("kind").capitalized).font(.caption.weight(.semibold)).foregroundStyle(ScribeTheme.accent)
                                Text(claim.text("text")).textSelection(.enabled)
                                HStack {
                                    ForEach((claim.raw["citation_ids"] as? [String] ?? []),id:\.self) { id in
                                        if let source=evidence.first(where:{$0.text("citation_id")==id}) {
                                            Button("Source · \(source.text("meeting_title"))") { open(source) }.buttonStyle(.borderless)
                                        }
                                    }
                                }.font(.caption)
                            }.notebookCard()
                        }
                        if (core.askResult["evidence"] as? Object)?["truncated"] as? Bool == true {
                            Label("Evidence was limited to 20 passages / 48 KB. This is not an exhaustive search.",systemImage:"info.circle")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        if text((core.askResult["evidence"] as? Object)?["retrieval_mode"]) == "current_meeting_fallback" {
                            Label("No keyword match · reviewed a bounded excerpt of the current transcript",systemImage:"text.magnifyingglass")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        if let packet=core.askResult["evidence"] as? Object, let index=packet["index"] as? Object,
                           let semantic=index["semantic"] as? Object {
                            if semantic["enabled"] as? Bool == true {
                                Label("Hybrid search · \(text(semantic["provider"]))",systemImage:"sparkle.magnifyingglass")
                                    .font(.caption).foregroundStyle(.secondary)
                            } else if !text(semantic["message"]).isEmpty {
                                Label(text(semantic["message"]),systemImage:"exclamationmark.triangle")
                                    .font(.caption).foregroundStyle(ScribeTheme.warning)
                            }
                        }
                        if let usage=core.askResult["usage"] as? Object {
                            Text("1 provider request · \(((usage["duration_ms"] as? NSNumber)?.intValue ?? 0)) ms · \(((usage["input_bytes"] as? NSNumber)?.intValue ?? 0)/1024) KB sent")
                                .font(.caption).foregroundStyle(.secondary)
                        }
                        DisclosureGroup("Evidence · \(evidence.count) passages") {
                            ForEach(evidence) { source in
                                Button { open(source) } label: {
                                    VStack(alignment:.leading,spacing:4) {
                                        Text(source.text("meeting_title")).font(.caption.weight(.semibold))
                                        Text(source.text("text")).lineLimit(4).multilineTextAlignment(.leading)
                                    }.padding(.vertical,5).contentShape(Rectangle())
                                }.buttonStyle(.plain)
                            }
                        }
                        Button("Ask a follow-up") {
                            previousAnswerID=text(core.askResult["answer_id"]); question=""
                        }.disabled(text(core.askResult["answer_id"]).isEmpty)
                    } else if !core.askLoading {
                        VStack(spacing:10) {
                            Image(systemName:"sparkles").font(.system(size:32)).foregroundStyle(.secondary)
                            Text("Ask about a decision, proposal or commitment").font(.title3.weight(.semibold))
                            Text("ScribeLoop retrieves evidence locally, then sends only the bounded text packet to your notes provider.")
                                .foregroundStyle(.secondary).multilineTextAlignment(.center)
                        }.frame(maxWidth:.infinity).padding(.top,80)
                    }
                    if !core.askHistory.isEmpty {
                        Divider(); Text("Saved questions in this scope").font(.headline)
                        ForEach(core.askHistory.prefix(8)) { row in
                            HStack {
                                Button { core.showAskHistory(row); previousAnswerID=nil } label: {
                                    VStack(alignment:.leading) {
                                        Text(row.text("question")).lineLimit(2)
                                        Text(row.raw["stale"] as? Bool == true ? "Outdated · ask again" : "\(row.text("provider")) · \(row.text("model"))")
                                            .font(.caption).foregroundStyle(row.raw["stale"] as? Bool == true ? ScribeTheme.warning : .secondary)
                                    }.frame(maxWidth:.infinity,alignment:.leading).contentShape(Rectangle())
                                }.buttonStyle(.plain).disabled(row.raw["stale"] as? Bool == true)
                                if row.raw["stale"] as? Bool != true { Button("Follow up") { previousAnswerID=row.id; question="" } }
                            }.padding(.vertical,4)
                        }
                    }
                }.padding(22)
            }
            Divider()
            VStack(alignment:.leading,spacing:8) {
                if previousAnswerID != nil {
                    HStack {
                        Label("Following up: \(previousQuestion)",systemImage:"arrowshape.turn.up.left")
                            .lineLimit(1).font(.caption).foregroundStyle(.secondary)
                        Spacer()
                        Button { previousAnswerID=nil } label: { Image(systemName:"xmark.circle.fill") }
                            .buttonStyle(.borderless).accessibilityLabel("Cancel follow-up")
                    }
                }
                HStack(spacing:10) {
                    TextField(previousAnswerID == nil ? "What did we agree about pricing?" : "Ask a follow-up…",text:$question)
                        .textFieldStyle(.roundedBorder).onSubmit { if !core.askLoading { submit() } }
                    if core.askLoading {
                        ProgressView().controlSize(.small); Button("Cancel") { core.cancelAsk() }
                    } else {
                        Button("Ask") { submit() }.buttonStyle(.borderedProminent)
                            .disabled(question.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty ||
                                      !scopeReady || (scope == "selected" && selected.isEmpty))
                    }
                }
            }.padding(18)
        }.background(ScribeTheme.canvas)
        .onAppear {
            core.askResult=[:]; core.askError=nil; previousAnswerID=nil
            if let id=core.selectedID { selected=[id] }
            refreshHistory()
        }
        .onChange(of:scope) { _ in previousAnswerID=nil; core.askResult=[:]; refreshHistory() }
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
    private func openModelSettings() {
        closeReview()
        DispatchQueue.main.asyncAfter(deadline:.now()+0.35) {
            NotificationCenter.default.post(name:Notification.Name("ScribeLoopOpenSettings"),object:nil)
        }
    }
    var body: some View {
        VStack(alignment:.leading,spacing:20) {
            Text(candidate == nil ? "A new conversation" : "Take notes for this call?").font(.system(size:30,design:.serif))
            if let candidate { Label(candidate.text("name"),systemImage:"phone").font(.title3) }
            else {
                Picker("Audio source",selection:$scope) { Text("Microphone only").tag("microphone"); Text("Meeting app + microphone").tag("application"); Text("All system audio + microphone").tag("system") }
                if scope == "application" { Picker("Application",selection:$app) { Text("Choose an application").tag(""); ForEach(objects(core.discovery["apps"])) { row in Text(row.text("name")).tag(row.id) } } }
            }
            Picker("Microphone",selection:$microphone) { ForEach(objects(core.discovery["microphones"])) { row in Text(row.text("name")).tag(row.id) } }
            if candidate != nil || scope == "application" {
                Text("Browser capture includes all tabs in the selected browser. Make sure everyone captured agrees to recording.").font(.callout).foregroundStyle(.secondary)
            } else {
                Text("Make sure everyone captured agrees to recording.").font(.callout).foregroundStyle(.secondary)
            }
            VStack(alignment:.leading,spacing:8) {
                Label("Audio · \(core.audioDestination)",systemImage:"waveform")
                Label("Notes · \(core.notesDestination)",systemImage:"note.text")
                Label("Transcript cleanup · \(core.cleanupDestination)",systemImage:"text.badge.checkmark")
            }.font(.callout).notebookCard()
            if !core.transcriptionReady {
                Label("Connect a transcription provider to start.",systemImage:"key").foregroundStyle(.orange)
                Button("Set up transcription…") { openModelSettings() }
            }
            if candidate != nil || scope != "microphone" {
                Button(core.meetingAudioPermissionLabel) { core.requestMeetingAudioAccess() }.disabled(core.busy || core.meetingAudioPermissionGranted)
                if let result = core.captureAccessMessage { Text(result).font(.caption).foregroundStyle(.secondary) }
            }
            if core.wantsNotes && !core.notesReady {
                Text("Notes are not set up. You can record a transcript now and add notes later.").font(.callout).foregroundStyle(.secondary)
                Button("Set up notes…") { openModelSettings() }
            }
            Text(core.notesReady ? "Press Stop when you’re finished. Notes follow automatically." : "Press Stop when you’re finished. This recording will save a transcript without automatic notes.").font(.callout)
            DisclosureGroup("Recording details") {
                Text("Limit: \((core.settings["duration_s"] as? Int ?? 3600)/60) minutes · Languages: \(core.transcriptionLanguageLabel). Cloud provider charges apply to the stages marked Cloud. Live text may take a few seconds to appear.").font(.caption).foregroundStyle(.secondary)
                if candidate != nil || scope == "application" {
                    Text("Call-end detection can be affected by mute or other browser tabs. Press Stop if the call has ended; choose Keep recording if prompted during a pause.").font(.caption).foregroundStyle(.secondary)
                }
            }
            if let recordError { Label(recordError,systemImage:"exclamationmark.triangle").font(.callout).foregroundStyle(ScribeTheme.warning) }
            HStack {
                Button("Not now") { if let candidate { core.send(["action":"dismiss_candidate","candidate_id":candidate.id]) }; closeReview() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button(pendingRecord == nil ? (core.notesReady ? "Record & take notes" : "Record transcript only") : "Starting…") {
                    recordError = nil
                    pendingRecord = core.record(scope:scope,app:app.isEmpty ? nil : app,microphone:microphone,candidate:candidate)
                    if pendingRecord == nil { recordError = core.message ?? "Recording could not be requested. Reconnect and try again." }
                }
                    .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(pendingRecord != nil || !core.accepted || !core.connected || core.busy
                        || (text(core.settings["transcription_engine"]) == "ollama"
                            ? core.settings["local_transcription_configured"] as? Bool != true
                            : core.transcription["configured"] as? Bool != true)
                        || (candidate == nil && scope == "application" && app.isEmpty))
            }
        }.padding(32).frame(width:580)
        .onAppear {
            core.refreshMeetingAudioPermission()
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
