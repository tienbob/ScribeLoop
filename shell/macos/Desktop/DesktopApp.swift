import SwiftUI
import AppKit
import ServiceManagement

@MainActor final class DesktopDelegate: NSObject, NSApplicationDelegate {
    weak var core: DesktopClient?
    private var callPanel: NSPanel?
    func showCall(_ candidate: Record) {
        guard let core else { return }
        callPanel?.close()
        let panel = NSPanel(contentRect:NSRect(x:0,y:0,width:640,height:600),
            styleMask:[.titled,.closable,.nonactivatingPanel],backing:.buffered,defer:false)
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
            Notebook(core:core).onAppear { delegate.core = core; core.onCandidate = { [weak delegate] row in delegate?.showCall(row) }; if !core.running { core.start() } }
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
    @State private var showPreferences = false
    @State private var showPeople = false
    @State private var tab = "Notes"
    @State private var confirmed: String?
    private let paper = Color(nsColor:.windowBackgroundColor)
    var candidates: [Record] { objects(core.discovery["candidates"]).filter { $0.raw["selectable"] as? Bool == true } }
    var body: some View {
        NavigationSplitView {
            VStack(alignment:.leading,spacing:16) {
                HStack { Text("ScribeLoop").font(.system(size:29,weight:.semibold,design:.serif)); Spacer() }
                Text("YOUR CONVERSATIONS, KEPT CLOSE").font(.caption2).tracking(1).foregroundStyle(.secondary)
                Button { candidate = nil; recording = true } label: { Label("New recording",systemImage:"plus") }.buttonStyle(.borderedProminent)
                    .disabled(!core.accepted || !core.connected || core.busy)
                List(selection:$core.selectedID) {
                    ForEach(core.sessions) { row in
                        VStack(alignment:.leading,spacing:5) {
                            Text(row.text("title")).font(.headline).lineLimit(2)
                            Text(Date(timeIntervalSince1970:Double(row.int("started_at"))/1000),style:.date).font(.caption).foregroundStyle(.secondary)
                            Text(row.text("status").capitalized).font(.caption2).foregroundStyle(row.id == core.activeID ? Color.red : Color.secondary)
                        }.padding(.vertical,8).tag(row.id)
                    }
                }.listStyle(.sidebar)
                if let cursor = core.nextCursor { Button("Older meetings") { core.send(["action":"history_page","cursor":cursor]) } }
                HStack {
                    Label(core.monitorLabel,
                          systemImage:core.connected ? "circle.fill" : "exclamationmark.circle").font(.caption).foregroundStyle(.secondary)
                    Spacer()
                    Button { showPreferences = true } label: { Image(systemName:"gearshape") }.help("Preferences")
                }
            }.padding(20).background(Color(nsColor:.controlBackgroundColor))
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
                        Button("Review recording") { candidate = current; recording = true }.buttonStyle(.borderedProminent).disabled(core.busy)
                    }.padding(18).background(Color.green.opacity(0.08))
                }
                if let activeID = core.activeID {
                    HStack { Image(systemName:"record.circle.fill").foregroundStyle(.red); Text("Recording · Cloud / Deepgram")
                        if let row = core.sessions.first(where: { $0.id == activeID }) { TimelineView(.periodic(from:.now,by:1)) { _ in Text(Date(timeIntervalSince1970:Double(row.int("started_at"))/1000),style:.timer).monospacedDigit() } }
                        Spacer(); Button("Stop recording",role:.destructive) { core.stopRecording() }.keyboardShortcut(".",modifiers:.command)
                    }.padding(18)
                }
                if core.callEnding, let activeID = core.activeID {
                    HStack { Text("Call activity ended. Recording will stop after the inactivity grace period."); Button("Keep recording") { core.send(["action":"keep_recording","session_id":activeID]); core.callEnding = false } }.padding()
                }
                if let selected = core.selected {
                    VStack(alignment:.leading,spacing:12) {
                        Text(selected.text("title")).font(.system(size:30,weight:.medium,design:.serif)).textSelection(.enabled)
                        HStack { Text(selected.text("status").capitalized); Text("·"); Text("\(core.total) transcript segments"); Spacer()
                            Menu("Meeting") { meetingActions(selected) }.disabled(!core.connected)
                        }.font(.callout).foregroundStyle(.secondary)
                        Picker("Meeting view",selection:$tab) { Text("Notes").tag("Notes"); Text("Transcript").tag("Transcript"); Text("Audio").tag("Audio") }.pickerStyle(.segmented).frame(maxWidth:360)
                    }.padding(28)
                    Divider()
                    if tab == "Transcript" { transcript }
                    else if tab == "Audio" { AudioView(core:core) }
                    else { notes }
                } else {
                    Spacer()
                    Image(systemName:"waveform.path").font(.system(size:42)).foregroundStyle(.secondary)
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
        .tint(.accentColor)
        .onChange(of:core.selectedID) { _, value in if let value { core.select(value) } }
        .sheet(isPresented:$recording) { RecordingReview(core:core,candidate:candidate) }
        .sheet(isPresented:$showPreferences) { Preferences(core:core).frame(width:650,height:720) }
        .sheet(isPresented:$showPeople) { PeopleView(core:core).frame(width:600,height:540) }
        .alert("Delete this meeting permanently?",isPresented:Binding(get:{confirmed != nil},set:{ if !$0 { confirmed = nil } })) {
            Button("Cancel",role:.cancel) { confirmed = nil }
            Button("Delete meeting",role:.destructive) { if let id = confirmed { core.send(["action":"delete","session_id":id,"confirmed":true]); confirmed = nil } }
        } message: { Text("The recording key, audio, transcript and saved notes will be deleted.") }
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
        Button("Generate summary") { summarize(row) }.disabled(core.busy || core.revision == 0 || !core.accepted || core.summaryProvider["configured"] as? Bool != true)
        Button("Export Markdown…") { core.send(["action":"export_markdown","session_id":row.id,"revision":core.revision]) }.disabled(core.revision == 0)
        Button("People…") { core.send(["action":"people_page"]); showPeople = true }
        Divider()
        Button("Recover interrupted audio") { core.send(["action":"recover","session_id":row.id]) }.disabled(core.busy || !["failed","interrupted"].contains(row.text("status")))
        Button("Resume transcription") { core.send(["action":"resume","session_id":row.id,"language":core.settings["language"] ?? "auto"]) }.disabled(core.busy || !core.accepted || !["failed","interrupted"].contains(row.text("status")))
        Button("Delete meeting…",role:.destructive) { confirmed = row.id }.disabled(core.busy)
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
                .onChange(of:core.pageSerial) { _, _ in if let citation = core.citationID { proxy.scrollTo(citation,anchor:.center) } else if let first = core.rows.first { proxy.scrollTo(first.id,anchor:.top) } }
            }
        }
    }
    private var notes: some View {
        ScrollView {
            VStack(alignment:.leading,spacing:22) {
                if core.summaryID == core.selectedID { Label("Preparing cited notes…",systemImage:"sparkles").foregroundStyle(.secondary); ProgressView() }
                if let saved = core.detail["summary"] as? Object, let summary = saved["summary"] as? Object {
                    Text("\(text(saved["provider"])) · \(text(saved["model"])) · Revision \((saved["revision"] as? Int) ?? 0)").font(.caption).foregroundStyle(.secondary)
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
                Text("Review notes against their cited transcript. Names are manual labels, not verified identities.").font(.caption).foregroundStyle(.secondary)
            }.padding(28).frame(maxWidth:850,alignment:.leading)
        }
    }
}

struct RecordingReview: View {
    @ObservedObject var core: DesktopClient
    var candidate: Record?
    var onClose: (() -> Void)? = nil
    @Environment(\.dismiss) private var dismiss
    @State private var scope = "microphone"
    @State private var app = ""
    @State private var microphone = "default"
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
            Text("Cloud · Deepgram Nova-3 with speaker separation\nNotes · \(text(core.summaryProvider["provider"])) / \(text(core.summaryProvider["model"]))\nEndpoint · \(text(core.summaryProvider["endpoint"]))").font(.callout).textSelection(.enabled)
            let duration = core.settings["duration_s"] as? Int ?? 3600
            Text("Limit: \(duration/60) minutes · estimated audio cap $\(duration == 300 ? "0.25" : duration == 3600 ? "3" : "8"). Provider charges apply. Language: \(text(core.settings["language"]))").font(.callout)
            Text(candidate == nil ? "Press Stop to end a manual recording. Notes follow automatically." : "Recording stops after 60 seconds of known call inactivity. Unknown activity keeps recording; Stop always remains available.").font(.caption).foregroundStyle(.secondary)
            if core.transcription["configured"] as? Bool != true { Label("App transcription is unavailable. Contact your administrator.",systemImage:"key").foregroundStyle(.orange) }
            HStack {
                Button("Not now") { if let candidate { core.send(["action":"dismiss_candidate","candidate_id":candidate.id]) }; closeReview() }.keyboardShortcut(.cancelAction)
                Spacer()
                Button("Record & take notes") { core.record(scope:scope,app:app.isEmpty ? nil : app,microphone:microphone,candidate:candidate); closeReview() }
                    .buttonStyle(.borderedProminent).keyboardShortcut(.defaultAction)
                    .disabled(!core.accepted || !core.connected || core.busy || core.transcription["configured"] as? Bool != true || (candidate == nil && scope == "application" && app.isEmpty))
            }
        }.padding(32).frame(width:580)
    }
    private func closeReview() { onClose?(); dismiss() }
}

struct CitedNotes: View {
    let summary: Object
    let citation: (String) -> Void
    var body: some View {
        VStack(alignment:.leading,spacing:22) {
            if let overview = summary["overview"] as? Object { item(overview) }
            ForEach(["key_points","decisions","action_items","open_questions","uncertainties"],id:\.self) { key in
                let rows = summary[key] as? [Object] ?? []
                if !rows.isEmpty {
                    Text(key.replacingOccurrences(of:"_",with:" ").capitalized).font(.title3)
                    ForEach(Array(rows.enumerated()),id:\.offset) { _, row in item(row) }
                }
            }
        }
    }
    private func item(_ row: Object) -> some View {
        VStack(alignment:.leading,spacing:8) {
            Text(verbatim:text(row["text"]).isEmpty ? text(row["task"]) : text(row["text"])).textSelection(.enabled)
            if row["task"] != nil { Text("Owner: \(text(row["owner"]).isEmpty ? "Not stated" : text(row["owner"])) · Due: \(text(row["due"]).isEmpty ? "Not stated" : text(row["due"]))").font(.caption).foregroundStyle(.secondary) }
            HStack { ForEach(Array((row["segment_ids"] as? [String] ?? []).enumerated()),id:\.offset) { index, id in Button("Source \(index+1)") { citation(id) }.buttonStyle(.link).foregroundStyle(.tint).font(.caption) } }
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
        core.send(["action":"accept_agreement","version":text(core.agreement["version"])])
        guard await wait({core.accepted}) else { await fail("agreement"); return }
        core.send(["action":"save_settings","settings":["language":"vi"]])
        guard await wait({text(core.settings["language"]) == "vi"}) else { await fail("settings"); return }
        core.reconnect()
        guard await wait({core.connected && core.selectedID == sid && core.rows.count > 0}) else { await fail("reconnect"); return }
        core.send(["action":"set_detection","enabled":false])
        guard await wait({!core.detection}) else { await fail("pause"); return }
        core.stop()
        guard await wait({!core.running && core.state == "Stopped"}) else { await fail("shutdown"); return }
        print("{\"ok\":true,\"protocol\":2,\"encrypted_fixture\":true,\"paged_rows\":230,\"agreement\":true,\"settings\":true,\"reconnect\":true,\"pause\":true,\"shutdown\":true}")
        exit(0)
    }
}
