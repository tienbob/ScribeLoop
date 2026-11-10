import Foundation
import EventKit

final class ReadProbe: @unchecked Sendable {
    private let lock = NSLock()
    private var calls:[String] = []
    private var active = 0
    private var peak = 0
    let gate = DispatchSemaphore(value:0)
    func enter(_ name:String) { lock.lock(); calls.append(name); active += 1; peak = max(peak,active); lock.unlock() }
    func leave() { lock.lock(); active -= 1; lock.unlock() }
    func snapshot() -> ([String],Int,Int) { lock.lock(); defer { lock.unlock() }; return (calls,active,peak) }
}

@main struct CalendarTests {
    @MainActor static func main() async {
        precondition(CalendarAccessRoute.route(.notDetermined) == .request,"A TCC reset must request access again, not send the user to an empty Settings list")
        precondition(CalendarAccessRoute.route(.denied) == .settings)
        precondition(CalendarAccessRoute.route(.restricted) == .settings)
        if #available(macOS 14.0,*) {
            precondition(CalendarAccessRoute.route(.writeOnly) == .request)
            precondition(CalendarAccessRoute.route(.fullAccess) == .ready)
        } else { precondition(CalendarAccessRoute.route(.authorized) == .ready) }
        let suite = "ScribeLoop.CalendarTests."+UUID().uuidString
        let defaults = UserDefaults(suiteName:suite)!
        defer { defaults.removePersistentDomain(forName:suite) }
        defaults.set(true,forKey:"calendar.enabled")
        defaults.set(["work"],forKey:"calendar.selected")
        let granted:EKAuthorizationStatus
        if #available(macOS 14.0,*) { granted = .fullAccess } else { granted = .authorized }
        let connected = CalendarCoordinator(defaults:defaults,authorization:{ granted })
        precondition(connected.connected && !connected.permissionNeedsRequest)
        let reopened = CalendarCoordinator(defaults:defaults,authorization:{ granted })
        precondition(reopened.connected && reopened.selected == ["work"],"Reopening must preserve the integration and selected calendars")
        let revoked = CalendarCoordinator(defaults:defaults,authorization:{ .denied })
        precondition(!revoked.connected && revoked.accessBlocked && revoked.enabled,"Denied access is not a connected state, and must not erase saved intent")
        let reset = CalendarCoordinator(defaults:defaults,authorization:{ .notDetermined })
        precondition(!reset.connected && reset.permissionNeedsRequest)
        reset.disable()
        let disconnected = CalendarCoordinator(defaults:defaults,authorization:{ granted })
        precondition(!disconnected.connected && disconnected.selected == ["work"],"Disconnect keeps selections without pretending that macOS revoked its grant")
        for url in ["https://meet.google.com/abc-defg-hij","https://teams.microsoft.com/l/meetup-join/test","https://company.zoom.us/j/123","https://company.webex.com/meet/user"] {
            precondition(MeetingLink.validated(URL(string:url)!) != nil)
        }
        for url in ["http://meet.google.com/abc","https://zoom.us.evil.test/j/1","https://evilzoom.us/j/1","https://user:password@zoom.us/j/1","https://zoom.us:8443/j/1","file:///tmp/meeting","javascript:alert(1)"] {
            precondition(MeetingLink.validated(URL(string:url)!) == nil)
        }
        precondition(MeetingLink.find(in:["Join here https://meet.google.com/abc-defg-hij"])?.host == "meet.google.com")
        precondition(MeetingLink.find(in:["https://zoom.us/j/1 https://meet.google.com/abc-defg-hij"]) == nil)
        let occurrence = Date(timeIntervalSince1970:1793667600)
        let first = CalendarMeeting.occurrenceID(calendar:"work",item:"recurring",occurrence:occurrence)
        precondition(first.count==64)
        precondition(first != CalendarMeeting.occurrenceID(calendar:"work",item:"recurring",occurrence:occurrence.addingTimeInterval(86400)))
        precondition(first != CalendarMeeting.occurrenceID(calendar:"personal",item:"recurring",occurrence:occurrence))
        let event = CalendarMeeting(id:first,title:"Meeting",calendarTitle:"Work",start:occurrence,end:occurrence.addingTimeInterval(3600),timezone:"Asia/Ho_Chi_Minh",allDay:false,joinURL:URL(string:"https://zoom.us/j/1"))
        precondition(event.snapshot["url"] == nil && event.snapshot["notes"] == nil && event.snapshot["attendees"] == nil)
        precondition(CalendarMeeting.clean("bad\nname",limit:160,fallback:"Untitled") == "badname")
        let probe = ReadProbe()
        let queue = CalendarReadQueue(delay:0) { ids,token in
            let name = ids.first!
            probe.enter(name); defer { probe.leave() }
            if ["first","cancel"].contains(name) { precondition(probe.gate.wait(timeout:.now()+3) == .success) }
            if token.isCancelled { return nil }
            return CalendarReadResult(calendars:[],meetings:[],truncated:false)
        }
        var completed:[String] = []
        func waitFor(_ condition:() -> Bool) async {
            let deadline = Date().addingTimeInterval(3)
            while !condition() {
                precondition(Date()<deadline,"Read queue did not settle")
                try? await Task.sleep(nanoseconds:1_000_000)
            }
        }
        queue.schedule(ids:["first"]) { _ in completed.append("first") }
        await waitFor { probe.snapshot().0 == ["first"] }
        queue.schedule(ids:["superseded"]) { _ in completed.append("superseded") }
        queue.schedule(ids:["latest"]) { _ in completed.append("latest") }
        try? await Task.sleep(nanoseconds:10_000_000)
        precondition(probe.snapshot().0 == ["first"],"A canceled blocking read must still occupy its slot")
        probe.gate.signal()
        await waitFor { completed == ["latest"] }
        precondition(probe.snapshot().0 == ["first","latest"] && probe.snapshot().2 == 1)
        queue.schedule(ids:["cancel"]) { _ in completed.append("cancel") }
        await waitFor { probe.snapshot().0.last == "cancel" }
        queue.cancel(); probe.gate.signal()
        queue.schedule(ids:["after"]) { _ in completed.append("after") }
        await waitFor { completed == ["latest","after"] }
        precondition(probe.snapshot().2 == 1,"Refresh/reconnect must never overlap readers")
        queue.cancel()
        print("Calendar regressions passed: permissions, URLs, recurrence, serial reads, latest request coalescing, cancellation and reconnect")
    }
}
