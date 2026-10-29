import AppKit
import Darwin
let app = NSApplication.shared
app.setActivationPolicy(.regular)
let window = NSWindow(contentRect: NSRect(x: 0, y: 0, width: 160, height: 100),
                      styleMask: [.titled], backing: .buffered, defer: false)
window.title = "ScribeLoop liveness fixture"
window.orderFront(nil)
signal(SIGTERM, SIG_IGN)
let termination = DispatchSource.makeSignalSource(signal: SIGTERM, queue: .main)
termination.setEventHandler { app.terminate(nil) }
termination.resume()
app.run()
