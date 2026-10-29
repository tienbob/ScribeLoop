import AppKit
import Darwin
import ScreenCaptureKit

let bundle = "dev.scribeloop.liveness-target"
let began = DispatchTime.now().uptimeNanoseconds
func ms() -> UInt64 { (DispatchTime.now().uptimeNanoseconds - began) / 1_000_000 }
func out(_ s: String) { print("\(ms()) \(s)"); fflush(stdout) }
func proc(_ pid: pid_t) -> String {
    var info = proc_bsdinfo()
    let size = Int32(MemoryLayout<proc_bsdinfo>.size)
    errno = 0
    let n = proc_pidinfo(pid, Int32(PROC_PIDTBSDINFO), 0, &info, size)
    guard n == size else { return n == 0 && errno == ESRCH ? "gone" : "unknown:\(n):\(errno)" }
    return "alive:\(info.pbi_status):\(UInt64(info.pbi_start_tvsec)*1_000_000 + UInt64(info.pbi_start_tvusec))"
}

@main struct Probe {
    static func main() async throws {
        _ = began
        let executable = URL(fileURLWithPath: CommandLine.arguments[1])
        for scenario in ["graceful", "force", "fast_relaunch"] {
            let target = Process(); target.executableURL = executable; try target.run()
            let pid = target.processIdentifier
            var replacement: Process?
            for tick in 0..<45 {
                if tick == 10 {
                    out("SIGNAL \(scenario) pid=\(pid) proc=\(proc(pid))")
                    kill(pid, scenario == "graceful" ? SIGTERM : SIGKILL)
                    if scenario == "fast_relaunch" {
                        let next = Process(); next.executableURL = executable; try next.run(); replacement = next
                        out("REPLACEMENT pid=\(next.processIdentifier) proc=\(proc(next.processIdentifier))")
                    }
                }
                let app = NSRunningApplication(processIdentifier: pid)
                let na = app.map { "terminated:\($0.isTerminated)" } ?? "nil"
                let ws = NSWorkspace.shared.runningApplications.filter { $0.bundleIdentifier == bundle }.map { $0.processIdentifier }
                var sck = ""
                do {
                    let content = try await SCShareableContent.excludingDesktopWindows(false, onScreenWindowsOnly: false)
                    let ids = content.applications.filter { $0.bundleIdentifier == bundle }.map { $0.processID }
                    let counts = Dictionary(grouping: content.applications, by: { $0.bundleIdentifier }).mapValues { $0.count }
                    sck = "sck=\(ids) multiplicity=\(counts.filter { $0.value > 1 })"
                } catch { sck = "sck_error=\((error as NSError).domain):\((error as NSError).code)" }
                out("\(scenario) tick=\(tick) pid=\(pid) proc=\(proc(pid)) na=\(na) ws=\(ws) \(sck)")
                try await Task.sleep(nanoseconds: 100_000_000)
            }
            target.waitUntilExit()
            if let replacement { replacement.terminate(); replacement.waitUntilExit() }
        }
    }
}
