import SwiftUI
import AppKit

@MainActor final class AppDelegate: NSObject, NSApplicationDelegate {
    weak var supervisor: CoreSupervisor?
    func applicationShouldTerminate(_ sender: NSApplication) -> NSApplication.TerminateReply {
        guard let supervisor, supervisor.running else { return .terminateNow }
        supervisor.stop { sender.reply(toApplicationShouldTerminate: true) }
        return .terminateLater
    }
    func applicationShouldTerminateAfterLastWindowClosed(_ sender: NSApplication) -> Bool { false }
}

struct ScribeLoopM0App: App {
    @NSApplicationDelegateAdaptor(AppDelegate.self) private var delegate
    @StateObject private var core = CoreSupervisor()
    @Environment(\.openWindow) private var openWindow
    var body: some Scene {
        Window("ScribeLoop · Desktop spike", id: "main") {
            FixtureWindow(core: core).onAppear { delegate.supervisor = core; core.start() }
        }.defaultSize(width: 1020, height: 700)
        MenuBarExtra("ScribeLoop M0", systemImage: "waveform") {
            Text("Synthetic fixture · \(core.state)")
            Button("Open notebook") { NSApp.activate(ignoringOtherApps: true); openWindow(id: "main") }
            Button("Stop fixture core") { core.stop() }.disabled(!core.running)
            Divider()
            Button("Quit ScribeLoop M0") { NSApp.terminate(nil) }
        }
    }
}

struct FixtureWindow: View {
    @ObservedObject var core: CoreSupervisor
    private let paper = Color(nsColor: .windowBackgroundColor)
    var body: some View {
        NavigationSplitView {
            VStack(alignment: .leading, spacing: 20) {
                Text("ScribeLoop").font(.system(size: 28, weight: .semibold, design: .serif))
                Text("DESKTOP SPIKE / M0").font(.caption).tracking(2).foregroundStyle(.secondary)
                List(selection: $core.selectedID) {
                    ForEach(core.meetings) { meeting in
                        VStack(alignment: .leading, spacing: 6) {
                            Text(meeting.title).font(.headline)
                            Text("Synthetic · \(meeting.status)").font(.caption).foregroundStyle(.secondary)
                        }.padding(.vertical, 8).tag(meeting.id)
                    }
                }.listStyle(.sidebar)
                Label("No recording or cloud calls", systemImage: "lock.shield").font(.caption)
            }.padding(22).background(Color(nsColor: .controlBackgroundColor))
            .navigationSplitViewColumnWidth(min: 260, ideal: 290, max: 380)
        } detail: {
            ScrollView {
                VStack(alignment: .leading, spacing: 24) {
                    HStack {
                        Text("A native home for your notes.").font(.system(size: 33, design: .serif))
                        Spacer()
                        Label(core.state, systemImage: core.state == "Connected" ? "checkmark.circle.fill" : "circle").foregroundStyle(.secondary)
                    }
                    Text("This isolated build proves the app bundle, authenticated core and encrypted storage. Everything below is generated test data.")
                        .foregroundStyle(.secondary).fixedSize(horizontal: false, vertical: true)
                    Divider()
                    if let problem = core.problem {
                        Label(problem, systemImage: "exclamationmark.triangle").foregroundStyle(.orange).textSelection(.enabled)
                    }
                    if let meeting = core.meetings.first(where: { $0.id == core.selectedID }) {
                        Text(meeting.title).font(.title2)
                        ForEach(core.segments[meeting.id] ?? []) { row in
                            HStack(alignment: .top, spacing: 22) {
                                Text(String(format: "%d:%02d", row.start_ms / 60000, row.start_ms / 1000 % 60)).monospacedDigit().foregroundStyle(.secondary)
                                VStack(alignment: .leading, spacing: 8) {
                                    Text(row.stream == "mic" ? "MICROPHONE · GENERATED" : "MEETING AUDIO · GENERATED").font(.caption).tracking(1)
                                    Text(row.text).font(.body).textSelection(.enabled)
                                }
                            }.padding(.vertical, 12)
                        }
                    } else { Text("Waiting for the bundled core…").foregroundStyle(.secondary) }
                    Divider()
                    Text("Bundle checks").font(.title3)
                    Grid(alignment: .leading, horizontalSpacing: 40, verticalSpacing: 14) {
                        check("SQLCipher metadata", core.checks?.sqlcipher)
                        check("Authenticated encrypted audio", core.checks?.encrypted_audio)
                        check("sqlite-vec extension", core.checks?.sqlite_vec)
                        check("Bundled soundfile library", core.checks?.soundfile)
                    }
                    Text("Production Keychain, microphone permissions, meeting detection and providers are not accessed. Those belong to later milestones.")
                        .font(.callout).foregroundStyle(.secondary)
                    Spacer(minLength: 24)
                }.padding(36).frame(maxWidth: 900, alignment: .leading)
            }.background(paper)
        }.navigationSplitViewStyle(.balanced).frame(minWidth: 850, minHeight: 580)
        .toolbar {
            Button("Refresh", systemImage: "arrow.clockwise") { core.refresh() }.disabled(core.state != "Connected")
            Button("Reconnect", systemImage: "link") { core.reconnect() }.disabled(!core.running)
            if core.running { Button("Stop core", systemImage: "stop.circle") { core.stop() } }
            else { Button("Start core", systemImage: "play.circle") { core.start() } }
        }.foregroundStyle(.primary).tint(.accentColor)
    }
    private func check(_ title: String, _ value: Bool?) -> some View {
        GridRow { Text(title); Label(value == true ? "Verified" : "Waiting", systemImage: value == true ? "checkmark.seal.fill" : "circle").foregroundStyle(value == true ? Color.green : Color.secondary) }
    }
}

@main enum DesktopEntry {
    @MainActor static func main() {
        if CommandLine.arguments.contains("--m0-smoke") {
            Task { await smoke() }
            RunLoop.main.run()
        } else { ScribeLoopM0App.main() }
    }
    @MainActor static func smoke() async {
        let core = CoreSupervisor()
        let started = Date()
        func wait(_ predicate: () -> Bool) async -> Bool {
            for _ in 0..<600 {
                if predicate() { return true }
                if core.problem != nil { return false }
                try? await Task.sleep(nanoseconds: 50_000_000)
            }
            return false
        }
        core.start()
        guard await wait({ core.state == "Connected" }), core.checks?.safe == true, core.meetings.count == 1 else {
            core.stop(); print("{\"ok\":false,\"stage\":\"startup\"}"); try? await Task.sleep(nanoseconds: 3_000_000_000); exit(1)
        }
        let meeting = core.meetings.first!.id
        core.reconnect()
        guard await wait({ core.state == "Connected" }), core.meetings.first?.id == meeting else {
            core.stop(); print("{\"ok\":false,\"stage\":\"reconnect\"}"); try? await Task.sleep(nanoseconds: 3_000_000_000); exit(1)
        }
        core.stop()
        guard await wait({ !core.running && core.state == "Stopped" }) else { print("{\"ok\":false,\"stage\":\"shutdown\"}"); exit(1) }
        print("{\"ok\":true,\"fixture\":true,\"encrypted_checks\":true,\"same_session_after_reconnect\":true,\"owned_core_stopped\":true,\"elapsed_s\":\(Date().timeIntervalSince(started))}")
        exit(0)
    }
}
