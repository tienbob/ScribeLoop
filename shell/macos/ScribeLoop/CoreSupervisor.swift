import Foundation
import Combine
import Security
import Darwin

struct FixtureMeeting: Decodable, Identifiable {
    let id: String
    let title: String
    let status: String
    let started_at: Int64
}
struct FixtureSegment: Decodable, Identifiable {
    let id: String
    let text: String
    let stream: String
    let start_ms: Int
    let end_ms: Int
}
struct FixtureChecks: Decodable {
    let sqlcipher: Bool
    let encrypted_audio: Bool
    let sqlite_vec: Bool
    let soundfile: Bool
    let provider_calls: Bool
    let capture: Bool
    let keychain: String
    var safe: Bool { sqlcipher && encrypted_audio && sqlite_vec && soundfile && !provider_calls && !capture && keychain == "not_requested" }
}
struct FixtureSnapshot: Decodable {
    let type: String
    let `protocol`: Int
    let launch_id: String
    let fixture: Bool
    let sessions: [FixtureMeeting]
    let segments: [String: [FixtureSegment]]
    let checks: FixtureChecks
}
private struct Ready: Decodable {
    let type: String
    let `protocol`: Int
    let pid: Int32
    let launch_id: String
    let host: String
    let port: Int
    let fixture: Bool
}

@MainActor final class CoreSupervisor: ObservableObject {
    @Published private(set) var state = "Stopped"
    @Published private(set) var problem: String?
    @Published private(set) var meetings: [FixtureMeeting] = []
    @Published private(set) var segments: [String: [FixtureSegment]] = [:]
    @Published private(set) var checks: FixtureChecks?
    @Published var selectedID: String?
    private var process: Process?
    private var input: Pipe?
    private var output: Pipe?
    private var diagnostics: Pipe?
    private var controlBuffer = Data()
    private var token = ""
    private var launchID = ""
    private var port: Int?
    private var stopping = false
    private var connectionID = UUID()
    private var socket: URLSessionWebSocketTask?
    private var reader: Task<Void, Never>?
    private var startupDeadline: Task<Void, Never>?
    private var shutdownDeadline: Task<Void, Never>?
    private var quitCompletion: (() -> Void)?
    var running: Bool { process?.isRunning == true }
    var childPID: Int32? { process?.processIdentifier }

    private func randomHex(_ count: Int) throws -> String {
        var bytes = [UInt8](repeating: 0, count: count)
        guard SecRandomCopyBytes(kSecRandomDefault, count, &bytes) == errSecSuccess else { throw CocoaError(.coderInvalidValue) }
        return bytes.map { String(format: "%02x", $0) }.joined()
    }

    func start() {
        guard !running else { return }
        cleanConnection()
        meetings = []; segments = [:]; checks = nil; selectedID = nil
        state = "Starting"; problem = nil; stopping = false; controlBuffer = Data()
        do {
            token = try randomHex(32); launchID = try randomHex(16)
            let generation = launchID
            let executable = Bundle.main.bundleURL.appendingPathComponent("Contents/Resources/Core/scribeloop-core-m0")
            guard FileManager.default.isExecutableFile(atPath: executable.path) else {
                fail("Bundled core is missing. Run the M0 bundle builder."); return
            }
            let child = Process(), stdin = Pipe(), stdout = Pipe(), stderr = Pipe()
            child.executableURL = executable
            child.currentDirectoryURL = URL(fileURLWithPath: "/")
            child.environment = ["PATH": "/usr/bin:/bin:/usr/sbin:/sbin", "HOME": NSHomeDirectory(),
                "TMPDIR": NSTemporaryDirectory(), "LC_ALL": "en_US.UTF-8", "PYTHONNOUSERSITE": "1"]
            child.standardInput = stdin; child.standardOutput = stdout; child.standardError = stderr
            stdout.fileHandleForReading.readabilityHandler = { [weak self] handle in
                let data = handle.availableData
                Task { @MainActor in self?.control(data, generation: generation) }
            }
            // Drain, never retain or display arbitrary child stderr.
            stderr.fileHandleForReading.readabilityHandler = { handle in _ = handle.availableData }
            child.terminationHandler = { [weak self] ended in
                Task { @MainActor in self?.terminated(ended, generation: generation) }
            }
            process = child; input = stdin; output = stdout; diagnostics = stderr
            try child.run()
            let request: [String: Any] = ["protocol": 1, "token": token, "launch_id": generation, "fixture": true]
            var data = try JSONSerialization.data(withJSONObject: request); data.append(10)
            try stdin.fileHandleForWriting.write(contentsOf: data)
            startupDeadline = Task { [weak self] in
                try? await Task.sleep(nanoseconds: 30_000_000_000)
                guard !Task.isCancelled, let self, self.launchID == generation, self.state != "Connected" else { return }
                self.fail("The isolated core did not become ready. Stop and try again.")
                self.stop()
            }
        } catch {
            fail("Could not launch the isolated core."); stop()
        }
    }

    private func control(_ data: Data, generation: String) {
        guard generation == launchID, !stopping else { return }
        guard !data.isEmpty else { return }
        controlBuffer.append(data)
        guard controlBuffer.count <= 8192 else { fail("Invalid core readiness response."); stop(); return }
        while let newline = controlBuffer.firstIndex(of: 10) {
            let line = Data(controlBuffer[..<newline]); controlBuffer.removeSubrange(...newline)
            guard let ready = try? JSONDecoder().decode(Ready.self, from: line),
                  ready.type == "ready", ready.protocol == 1, ready.fixture,
                  ready.launch_id == generation, ready.pid == process?.processIdentifier,
                  ready.host == "127.0.0.1", (1...65535).contains(ready.port), port == nil else {
                fail("The isolated core returned an invalid handshake."); stop(); return
            }
            port = ready.port
            connect()
        }
    }

    func reconnect() { guard running, port != nil, !stopping else { return }; connect() }
    private func connect() {
        cleanConnection()
        guard let port else { return }
        state = "Connecting"; problem = nil
        let id = UUID(); connectionID = id
        let configuration = URLSessionConfiguration.ephemeral
        configuration.urlCache = nil; configuration.httpCookieStorage = nil
        configuration.connectionProxyDictionary = [:]
        let session = URLSession(configuration: configuration)
        let ws = session.webSocketTask(with: URL(string: "ws://127.0.0.1:\(port)/ws")!)
        ws.maximumMessageSize = 256_000
        socket = ws; ws.resume()
        let secret = token
        reader = Task { [weak self] in
            defer { session.invalidateAndCancel() }
            do {
                try await ws.send(.string(secret))
                while !Task.isCancelled {
                    let message = try await ws.receive()
                    let data: Data
                    switch message { case .string(let string): data = Data(string.utf8); case .data(let bytes): data = bytes; @unknown default: throw CocoaError(.coderInvalidValue) }
                    guard data.count <= 256_000 else { throw CocoaError(.coderInvalidValue) }
                    let value = try JSONDecoder().decode(FixtureSnapshot.self, from: data)
                    guard let self, id == self.connectionID, value.type == "snapshot", value.protocol == 1,
                          value.fixture, value.launch_id == self.launchID, value.checks.safe,
                          value.sessions.count <= 16, value.segments.values.reduce(0, { $0 + $1.count }) <= 128 else {
                        throw CocoaError(.coderInvalidValue)
                    }
                    self.meetings = value.sessions; self.segments = value.segments; self.checks = value.checks
                    if !value.sessions.contains(where: { $0.id == self.selectedID }) { self.selectedID = value.sessions.first?.id }
                    self.state = "Connected"; self.startupDeadline?.cancel()
                }
            } catch {
                guard let self, id == self.connectionID, !self.stopping, !Task.isCancelled else { return }
                self.fail("Connection interrupted. Reconnect restores this synthetic fixture without replaying commands.")
            }
        }
    }

    func refresh() {
        guard state == "Connected", let ws = socket else { return }
        Task { try? await ws.send(.string("{\"action\":\"snapshot\"}")) }
    }
    private func cleanConnection() {
        connectionID = UUID(); reader?.cancel(); reader = nil
        socket?.cancel(with: .goingAway, reason: nil); socket = nil
    }
    private func fail(_ message: String) { problem = message; state = "Needs attention" }
    func stop(completion: (() -> Void)? = nil) {
        quitCompletion = completion ?? quitCompletion
        guard let child = process, child.isRunning else { finishStop(); return }
        guard !stopping else { return }
        stopping = true; state = "Stopping"; startupDeadline?.cancel(); cleanConnection()
        try? input?.fileHandleForWriting.write(contentsOf: Data("STOP\n".utf8))
        try? input?.fileHandleForWriting.close()
        let generation = launchID
        shutdownDeadline = Task { [weak self, weak child] in
            try? await Task.sleep(nanoseconds: 5_000_000_000)
            guard !Task.isCancelled, let self, self.launchID == generation, let child, child.isRunning else { return }
            child.terminate()
            try? await Task.sleep(nanoseconds: 2_000_000_000)
            if !Task.isCancelled && child.isRunning { Darwin.kill(child.processIdentifier, SIGKILL) }
        }
    }
    private func terminated(_ child: Process, generation: String) {
        guard launchID == generation else { return }
        let expected = stopping
        finishStop()
        if !expected { fail("The isolated core exited. Start again to create a fresh synthetic fixture.") }
    }
    private func finishStop() {
        shutdownDeadline?.cancel(); shutdownDeadline = nil; startupDeadline?.cancel()
        cleanConnection(); port = nil; token = ""; controlBuffer = Data()
        output?.fileHandleForReading.readabilityHandler = nil
        diagnostics?.fileHandleForReading.readabilityHandler = nil
        try? output?.fileHandleForReading.close(); try? diagnostics?.fileHandleForReading.close()
        try? input?.fileHandleForWriting.close()
        input = nil; output = nil; diagnostics = nil; process = nil; stopping = false; state = "Stopped"
        let callback = quitCompletion; quitCompletion = nil; callback?()
    }
}
