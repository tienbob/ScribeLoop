import SwiftUI
import EventKit
import AppKit
import CryptoKit

struct MeetingLink {
    static func validated(_ url:URL) -> URL? {
        let hosts = ["meet.google.com","teams.microsoft.com","teams.live.com","teams.cloud.microsoft","zoom.us","zoomgov.com","webex.com"]
        guard url.scheme?.lowercased() == "https", let host = url.host?.lowercased(),
              url.user == nil, url.password == nil, url.port == nil || url.port == 443,
              hosts.contains(where:{ host == $0 || host.hasSuffix("."+$0) }) else { return nil }
        return url
    }
    static func find(in fields:[String]) -> URL? {
        guard let detector = try? NSDataDetector(types:NSTextCheckingResult.CheckingType.link.rawValue) else { return nil }
        var urls = Set<URL>()
        for field in fields {
            let value = String(field.prefix(16_000))
            for match in detector.matches(in:value,range:NSRange(value.startIndex...,in:value)) {
                if let url = match.url, let safe = validated(url) { urls.insert(safe) }
            }
        }
        // Multiple destinations need the user's review in their calendar app.
        return urls.count == 1 ? urls.first : nil
    }
}

struct CalendarMeeting: Identifiable, Sendable {
    let id:String
    let title:String
    let calendarTitle:String
    let start:Date
    let end:Date
    let timezone:String
    let allDay:Bool
    let joinURL:URL?
    static func occurrenceID(calendar:String,item:String,occurrence:Date) -> String {
        let value = "\(calendar)\u{001f}\(item)\u{001f}\(Int64((occurrence.timeIntervalSince1970*1000).rounded()))"
        return SHA256.hash(data:Data(value.utf8)).map{String(format:"%02x",$0)}.joined()
    }
    var snapshot:Object {
        ["id":id,"title":title,"calendar_title":calendarTitle,
         "start_ms":Int64((start.timeIntervalSince1970*1000).rounded()),
         "end_ms":Int64((end.timeIntervalSince1970*1000).rounded()),"timezone":timezone]
    }
    static func clean(_ text:String,limit:Int,fallback:String) -> String {
        let value = text.unicodeScalars.filter{$0.value>=32}.prefix(limit).map(String.init).joined()
            .trimmingCharacters(in:.whitespacesAndNewlines)
        return value.isEmpty ? fallback : value
    }
}

struct CalendarChoice: Identifiable, Sendable { let id:String; let title:String }
struct CalendarReadResult: Sendable { let calendars:[CalendarChoice]; let meetings:[CalendarMeeting]; let truncated:Bool }

final class CalendarReadCancellation: @unchecked Sendable {
    private let lock = NSLock()
    private var cancelled = false
    func cancel() { lock.lock(); cancelled = true; lock.unlock() }
    var isCancelled:Bool { lock.lock(); defer { lock.unlock() }; return cancelled }
}

/// At most one blocking EventKit read and one latest pending request.
@MainActor final class CalendarReadQueue {
    typealias Reader = @Sendable (Set<String>,CalendarReadCancellation) -> CalendarReadResult?
    private let reader:Reader
    private let delay:UInt64
    private var pending:(ids:Set<String>,completion:(CalendarReadResult)->Void)?
    private var pendingReady = false
    private var debounce:Task<Void,Never>?
    private var worker:Task<Void,Never>?
    private var cancellation:CalendarReadCancellation?
    init(delay:UInt64 = 150_000_000,reader:@escaping Reader) { self.delay = delay; self.reader = reader }
    func schedule(ids:Set<String>,completion:@escaping (CalendarReadResult)->Void) {
        pending = (ids,completion); pendingReady = false
        cancellation?.cancel(); debounce?.cancel()
        debounce = Task {
            try? await Task.sleep(nanoseconds:delay)
            guard !Task.isCancelled else { return }
            pendingReady = true; startNext()
        }
    }
    func cancel() {
        pending = nil; pendingReady = false; debounce?.cancel(); debounce = nil
        cancellation?.cancel()
        // Keep the in-flight slot occupied until EventKit returns. Canceling
        // an unstructured task does not abort its synchronous API call.
    }
    private func startNext() {
        guard worker == nil, pendingReady, let request = pending else { return }
        pending = nil; pendingReady = false
        let token = CalendarReadCancellation(); cancellation = token
        let ids = request.ids, read = reader
        worker = Task {
            let value = await Task.detached(priority:.utility) { read(ids,token) }.value
            worker = nil; cancellation = nil
            if !token.isCancelled, let value { request.completion(value) }
            startNext()
        }
    }
    deinit { debounce?.cancel(); cancellation?.cancel() }
}

enum CalendarAccessRoute {
    case request, ready, settings
    static func route(_ status:EKAuthorizationStatus) -> Self {
        if status == .notDetermined { return .request }
        if #available(macOS 14.0,*) {
            if status == .writeOnly { return .request }
            if status == .fullAccess { return .ready }
        } else if status == .authorized { return .ready }
        return .settings
    }
}

@MainActor final class CalendarCoordinator: ObservableObject {
    @Published var enabled:Bool
    @Published var selected:Set<String>
    @Published var calendars:[CalendarChoice] = []
    @Published var meetings:[CalendarMeeting] = []
    @Published var loading = false
    @Published var message:String?
    @Published var refreshed:Date?
    @Published var truncated = false
    @Published var accessBlocked = false
    @Published var permissionNeedsRequest = false
    @Published private(set) var accessReady = false
    var connected:Bool { enabled && accessReady }
    private let authorization:() -> EKAuthorizationStatus
    private var activationObserver:NSObjectProtocol?
    private let defaults:UserDefaults
    private let store = EKEventStore()
    private var observer:NSObjectProtocol?
    private lazy var reads = CalendarReadQueue { ids,token in Self.read(ids:ids,now:Date(),cancelled:token) }
    private var generation = UUID()
    private var requestingAccess = false
    init(defaults:UserDefaults = .standard,authorization:@escaping () -> EKAuthorizationStatus = { EKEventStore.authorizationStatus(for:.event) }) {
        self.defaults = defaults
        self.authorization = authorization
        enabled = defaults.bool(forKey:"calendar.enabled")
        selected = Set(defaults.stringArray(forKey:"calendar.selected") ?? [])
        updateAuthorization()
        activationObserver = NotificationCenter.default.addObserver(forName:NSApplication.didBecomeActiveNotification,object:nil,queue:.main) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
        observer = NotificationCenter.default.addObserver(forName:.EKEventStoreChanged,object:nil,queue:.main) { [weak self] _ in
            Task { @MainActor in self?.refresh() }
        }
    }
    deinit {
        if let observer { NotificationCenter.default.removeObserver(observer) }
        if let activationObserver { NotificationCenter.default.removeObserver(activationObserver) }
    }
    static var canRead:Bool {
        CalendarAccessRoute.route(EKEventStore.authorizationStatus(for:.event)) == .ready
    }
    private var accessRoute:CalendarAccessRoute { CalendarAccessRoute.route(authorization()) }
    private func updateAuthorization() {
        accessReady = accessRoute == .ready
        permissionNeedsRequest = enabled && accessRoute == .request
        accessBlocked = accessRoute == .settings
    }
    func enable() {
        guard !loading else { return }
        if accessRoute == .ready {
            enabled = true; defaults.set(true,forKey:"calendar.enabled"); refresh(); return
        }
        generation = UUID(); let ticket = generation
        loading = true; requestingAccess = true; message = nil
        Task {
            do {
                let granted:Bool
                if #available(macOS 14.0,*) { granted = try await store.requestFullAccessToEvents() }
                else { granted = try await store.requestAccess(to:.event) }
                guard ticket == generation else { return }
                loading = false; requestingAccess = false
                updateAuthorization()
                guard granted else {
                    permissionNeedsRequest = accessRoute == .request
                    accessBlocked = !permissionNeedsRequest
                    message = permissionNeedsRequest ? "Calendar access was not approved. You can request access again." : "In System Settings, open Privacy & Security → Calendars and allow ScribeLoop."
                    return
                }
                accessBlocked = false; permissionNeedsRequest = false
                enabled = true; defaults.set(true,forKey:"calendar.enabled"); refresh()
            } catch {
                guard ticket == generation else { return }
                loading = false; requestingAccess = false; updateAuthorization(); message = "Calendar access could not be enabled. Manual recording still works."
            }
        }
    }
    func disable() {
        generation = UUID(); reads.cancel(); requestingAccess = false
        enabled = false; defaults.set(false,forKey:"calendar.enabled")
        accessBlocked = false; permissionNeedsRequest = false
        meetings = []; calendars = []; refreshed = nil; message = nil; loading = false
    }
    func toggle(_ id:String) {
        if selected.contains(id) { selected.remove(id) } else { selected.insert(id) }
        defaults.set(selected.sorted(),forKey:"calendar.selected"); refresh()
    }
    func refresh() {
        #if !CALENDAR_LAYOUT_PREVIEW
        guard !requestingAccess else { return }
        updateAuthorization()
        guard enabled else { return }
        guard accessReady else {
            permissionNeedsRequest = accessRoute == .request
            accessBlocked = !permissionNeedsRequest
            generation = UUID(); reads.cancel(); meetings = []; calendars = []; refreshed = nil; loading = false
            message = permissionNeedsRequest ? nil : "Calendar permission is unavailable. Enable access in macOS Privacy & Security, or keep using manual recording."; return
        }
        accessBlocked = false; permissionNeedsRequest = false
        generation = UUID(); let ticket = generation; let ids = selected
        loading = true; message = nil
        reads.schedule(ids:ids) { [weak self] result in
            guard let self, ticket == self.generation, self.enabled else { return }
            guard self.accessRoute == .ready else { self.refresh(); return }
            self.calendars = result.calendars; self.meetings = result.meetings
            self.truncated = result.truncated; self.refreshed = Date(); self.loading = false
            if ids.isEmpty { self.message = "Choose the calendars to show. No calendars are selected by default." }
            else if !ids.isSubset(of:Set(self.calendars.map(\.id))) { self.message = "Some selected calendars are unavailable. Review your calendar selection after account changes." }
        }
        #endif
    }
    nonisolated private static func read(ids:Set<String>,now:Date,cancelled:CalendarReadCancellation) -> CalendarReadResult? {
        guard !cancelled.isCancelled else { return nil }
        let reader = EKEventStore()
        let available = reader.calendars(for:.event)
        guard !cancelled.isCancelled else { return nil }
        let choices = available.map{CalendarChoice(id:$0.calendarIdentifier,title:CalendarMeeting.clean($0.title,limit:100,fallback:"Calendar"))}.sorted{$0.title.localizedStandardCompare($1.title) == .orderedAscending}
        let selected = available.filter{ids.contains($0.calendarIdentifier)}
        guard !selected.isEmpty else { return CalendarReadResult(calendars:choices,meetings:[],truncated:false) }
        let start = Calendar.current.startOfDay(for:now)
        let end = Calendar.current.date(byAdding:.day,value:14,to:start)!
        let predicate = reader.predicateForEvents(withStart:start,end:end,calendars:selected)
        let fetched = reader.events(matching:predicate)
        guard !cancelled.isCancelled else { return nil }
        let events = fetched.filter { event in
            guard let start = event.startDate, let end = event.endDate, end >= start else { return false }
            return event.status != .canceled && !(event.attendees?.contains{ $0.isCurrentUser && $0.participantStatus == .declined } ?? false)
        }.sorted{$0.startDate<$1.startDate}
        var seen = Set<String>()
        let meetings = events.prefix(500).compactMap { event -> CalendarMeeting? in
            guard !cancelled.isCancelled else { return nil }
            let item = event.calendarItemExternalIdentifier ?? event.calendarItemIdentifier
            let key = CalendarMeeting.occurrenceID(calendar:event.calendar.calendarIdentifier,item:item,occurrence:event.occurrenceDate ?? event.startDate)
            guard seen.insert(key).inserted else { return nil }
            return CalendarMeeting(id:key,title:CalendarMeeting.clean(event.title ?? "",limit:160,fallback:"Untitled event"),
                calendarTitle:CalendarMeeting.clean(event.calendar.title,limit:100,fallback:"Calendar"),
                start:event.startDate,end:event.endDate,timezone:event.timeZone?.identifier ?? TimeZone.current.identifier,
                allDay:event.isAllDay,joinURL:MeetingLink.find(in:[event.url?.absoluteString ?? "",event.location ?? "",event.notes ?? ""]))
        }
        guard !cancelled.isCancelled else { return nil }
        return CalendarReadResult(calendars:choices,meetings:meetings,truncated:events.count>500)
    }
}

struct CalendarPanel: View {
    @ObservedObject var core:DesktopClient
    @ObservedObject var calendar:CalendarCoordinator
    var conversation:Record? = nil
    var onOpenSettings:() -> Void = {}
    @Environment(\.dismiss) private var dismiss
    @State private var linkRequest:String?
    @State private var result:String?
    @State private var filter = "upcoming"
    @State private var now = Date()
    private var linked:Object { core.selectedID == conversation?.id ? core.detail["calendar_event"] as? Object ?? [:] : [:] }
    private var visible:[CalendarMeeting] {
        calendar.meetings.filter { filter == "today" ? Calendar.current.isDate($0.start,inSameDayAs:now) : $0.end >= now }
    }
    private var days:[Date] { Array(Set(visible.map{Calendar.current.startOfDay(for:$0.start)})).sorted() }
    var body:some View {
        VStack(alignment:.leading,spacing:0) {
            header.padding(24)
            Divider()
            if calendar.accessBlocked {
                emptyState(icon:"calendar.badge.exclamationmark",title:"Allow Calendar access",detail:"Manage Calendar access in Settings to see your meetings here.",button:"Open Calendar settings",action:onOpenSettings)
            } else if !calendar.enabled || calendar.permissionNeedsRequest {
                emptyState(icon:"calendar.badge.plus",title:"See your meetings here",detail:"Connect macOS Calendar in Settings, then choose the calendars to show.",button:"Open Calendar settings",action:onOpenSettings)
            } else if calendar.loading && calendar.calendars.isEmpty {
                VStack(spacing:12) { ProgressView(); Text("Loading your calendars…").foregroundStyle(.secondary) }.frame(maxWidth:.infinity,maxHeight:.infinity)
            } else if calendar.selected.isEmpty {
                emptyState(icon:"calendar",title:"Choose your calendars",detail:"Choose which calendars appear in Settings.",button:"Open Calendar settings",action:onOpenSettings)
            } else {
                meetingList
            }
            if let message = calendar.message, !calendar.selected.isEmpty || !calendar.enabled {
                Text(message).font(.callout).foregroundStyle(ScribeTheme.warning).padding(.horizontal,24).padding(.bottom,12)
            }
            if let result {
                Label(result,systemImage:"info.circle").font(.callout).foregroundStyle(ScribeTheme.accent).padding(.horizontal,24).padding(.bottom,12)
            }
            Divider()
            HStack(spacing:8) {
                Image(systemName:conversation == nil ? "record.circle" : "link")
                Text(conversation == nil ? "Opening a meeting does not record it. You choose when recording starts." : "Linking connects this event to your saved recording. It does not change the calendar.")
                Spacer(minLength:0)
            }.font(.caption).foregroundStyle(.secondary).padding(.horizontal,24).padding(.vertical,14)
        }.frame(width:780,height:660).background(ScribeTheme.canvas).tint(ScribeTheme.accent)
            .onAppear { filter = conversation == nil ? "upcoming" : "today"; calendar.refresh() }
            .onReceive(NotificationCenter.default.publisher(for:NSApplication.didBecomeActiveNotification)) { _ in calendar.refresh() }
            .onReceive(NotificationCenter.default.publisher(for:.NSSystemTimeZoneDidChange)) { _ in calendar.refresh() }
            .onReceive(Timer.publish(every:60,on:.main,in:.common).autoconnect()) { date in
                if !Calendar.current.isDate(now,inSameDayAs:date) { calendar.refresh() }; now = date
            }
            .onChange(of:core.requests) { values in
                guard let id = linkRequest else { return }
                if values[id] == "accepted" { linkRequest = nil; result = "Recording link updated." }
                else if ["rejected","unknown"].contains(values[id] ?? "") { linkRequest = nil; result = "Link could not be confirmed. Reopen the recording before trying again." }
            }
    }
    private var header:some View {
        VStack(alignment:.leading,spacing:14) {
            HStack(alignment:.top) {
                VStack(alignment:.leading,spacing:5) {
                    Text(conversation == nil ? "Your meetings" : "Link a calendar event").font(.system(size:28,weight:.medium,design:.serif))
                    Text(conversation == nil ? "Your schedule, ready when you are." : "Choose the event that belongs to this recording.").font(.callout).foregroundStyle(.secondary)
                }
                Spacer()
                if calendar.connected { Button("Settings",systemImage:"gearshape") { onOpenSettings() }.help("Manage Calendar in Settings") }
                Button("Done") { dismiss() }.keyboardShortcut(.defaultAction)
            }
            if let conversation {
                HStack(alignment:.top,spacing:10) {
                    Image(systemName:"waveform").foregroundStyle(ScribeTheme.accent)
                    VStack(alignment:.leading,spacing:5) {
                        Text(conversation.text("title")).font(.callout.weight(.semibold)).lineLimit(2)
                        if let title = linked["title"] as? String {
                            HStack { Text("Linked to: \(title)").lineLimit(1); Button("Remove link") { link(nil) }.disabled(linkRequest != nil || !core.connected) }.font(.caption)
                        } else { Text("Choose an event below to link it.").font(.caption).foregroundStyle(.secondary) }
                    }
                    Spacer(minLength:0)
                }.padding(12).background(ScribeTheme.subtle,in:RoundedRectangle(cornerRadius:10))
            }
        }
    }
    private var meetingList:some View {
        VStack(spacing:0) {
            HStack {
                Picker("Show meetings",selection:$filter) { Text("Today").tag("today"); Text("Upcoming").tag("upcoming") }.pickerStyle(.segmented).labelsHidden().frame(width:230)
                Spacer()
                if calendar.loading { ProgressView().controlSize(.small) }
                Button { now = Date(); calendar.refresh() } label: { Image(systemName:"arrow.clockwise") }.buttonStyle(.borderless).help("Refresh meetings").accessibilityLabel("Refresh meetings").disabled(calendar.loading)
            }.padding(.horizontal,24).padding(.vertical,16)
            if visible.isEmpty {
                emptyState(icon:"calendar.badge.clock",title:filter == "today" ? "No meetings today" : "No upcoming meetings",detail:filter == "today" ? "Your selected calendars have no events today. Take a look at what's coming next." : "There are no upcoming events in the next 14 days. Check your calendars, or refresh after adding a meeting.",button:filter == "today" ? "View upcoming" : "Calendar settings") {
                    if filter == "today" { filter = "upcoming" } else { onOpenSettings() }
                }
            } else {
                ScrollView {
                    LazyVStack(alignment:.leading,spacing:12) {
                        ForEach(days,id:\.self) { day in
                            HStack { Text(dayTitle(day)).font(.callout.weight(.semibold)); Spacer(); Text(day.formatted(.dateTime.month(.abbreviated).day())).font(.caption).foregroundStyle(.secondary) }.padding(.top,8)
                            ForEach(visible.filter{Calendar.current.isDate($0.start,inSameDayAs:day)}) { event in eventRow(event) }
                        }
                    }.padding(.horizontal,24).padding(.bottom,20)
                }
            }
            HStack {
                if let date = calendar.refreshed { Text("Updated \(date.formatted(date:.omitted,time:.shortened))") }
                Spacer()
                Text("Times in \(TimeZone.current.identifier.replacingOccurrences(of:"_",with:" "))")
            }.font(.caption2).foregroundStyle(.secondary).padding(.horizontal,24).padding(.vertical,10)
            if calendar.truncated { Text("Showing the first 500 events. Choose fewer calendars to see a smaller list.").font(.caption).padding(.horizontal,24).padding(.bottom,10) }
        }
    }
    private func eventRow(_ event:CalendarMeeting) -> some View {
        HStack(alignment:.center,spacing:16) {
            VStack(alignment:.leading,spacing:4) {
                if event.allDay { Text("All day").font(.callout.weight(.semibold)) }
                else { Text(event.start,style:.time).font(.callout.weight(.semibold)).monospacedDigit(); Text(event.end,style:.time).font(.caption).foregroundStyle(.secondary) }
            }.frame(width:84,alignment:.leading)
            RoundedRectangle(cornerRadius:2).fill(ScribeTheme.accent.opacity(0.65)).frame(width:3)
            VStack(alignment:.leading,spacing:6) {
                Text(event.title).font(.system(size:15,weight:.semibold)).fixedSize(horizontal:false,vertical:true)
                HStack(spacing:6) {
                    Text(event.calendarTitle)
                    Text("·")
                    Text(providerName(event.joinURL))
                    if event.end < now { Text("· Ended") }
                }.font(.caption).foregroundStyle(.secondary)
            }.frame(maxWidth:.infinity,alignment:.leading)
            if conversation != nil {
                Button(text(linked["id"]) == event.id ? "Linked" : "Link this event",systemImage:"link") { link(event.snapshot) }.disabled(!core.connected || linkRequest != nil || text(linked["id"]) == event.id)
            } else if let url = event.joinURL {
                Button("Open meeting",systemImage:"arrow.up.right") { if let safe = MeetingLink.validated(url) { NSWorkspace.shared.open(safe) } }.buttonStyle(.borderedProminent).tint(ScribeTheme.action)
            } else {
                Text("No meeting link").font(.caption).foregroundStyle(.secondary)
            }
        }.padding(16).background(ScribeTheme.panel,in:RoundedRectangle(cornerRadius:12))
            .overlay { RoundedRectangle(cornerRadius:12).strokeBorder(ScribeTheme.border.opacity(0.65)) }
    }
    private func emptyState(icon:String,title:String,detail:String,button:String,action:@escaping () -> Void) -> some View {
        VStack(spacing:16) {
            Image(systemName:icon).font(.system(size:34,weight:.light)).foregroundStyle(ScribeTheme.accent).frame(width:76,height:76).background(ScribeTheme.subtle,in:RoundedRectangle(cornerRadius:20))
            VStack(spacing:8) { Text(title).font(.title2.weight(.medium)); Text(detail).font(.callout).foregroundStyle(.secondary).multilineTextAlignment(.center).frame(maxWidth:400) }
            Button(button,action:action).buttonStyle(.borderedProminent).tint(ScribeTheme.action).controlSize(.large).disabled(calendar.loading)
            if !calendar.enabled { Text("Events stay on this Mac. ScribeLoop does not change your calendar.").font(.caption).foregroundStyle(.secondary) }
        }.padding(30).frame(maxWidth:.infinity,maxHeight:.infinity)
    }
    private func dayTitle(_ date:Date) -> String {
        if Calendar.current.isDate(date,inSameDayAs:now) { return "Today" }
        if let tomorrow = Calendar.current.date(byAdding:.day,value:1,to:now), Calendar.current.isDate(date,inSameDayAs:tomorrow) { return "Tomorrow" }
        return date.formatted(.dateTime.weekday(.wide))
    }
    private func providerName(_ url:URL?) -> String {
        guard let host = url?.host else { return "Calendar event" }
        if host.contains("google") { return "Google Meet" }; if host.contains("teams") { return "Microsoft Teams" }; if host.contains("zoom") { return "Zoom" }; return "Webex"
    }
    private func link(_ event:Object?) {
        guard let sid = conversation?.id else { return }
        result = nil
        linkRequest = core.send(["action":"link_calendar_event","session_id":sid,"event":event as Any? ?? NSNull()])
        if linkRequest == nil { result = "Reconnect before linking this event." }
    }
}

/// All persistent Calendar configuration lives in Settings and shares one coordinator.
struct CalendarSettings:View {
    @ObservedObject var calendar:CalendarCoordinator
    var body:some View {
        Group {
            Section("Calendar connection") {
                if calendar.connected {
                    HStack {
                        Label("Calendar connected",systemImage:"checkmark.circle.fill").foregroundStyle(ScribeTheme.accent)
                        Spacer()
                        Button("Disconnect",role:.destructive) { calendar.disable() }
                    }
                    Text("Your selected calendars appear in Your meetings. Events stay in macOS Calendar; ScribeLoop does not change them.").font(.caption).foregroundStyle(.secondary)
                    Button("Refresh calendars") { calendar.refresh() }.disabled(calendar.loading)
                } else {
                    Label(calendar.accessBlocked ? "Calendar access needs attention" : "Bring your meetings into ScribeLoop",systemImage:"calendar.badge.plus").font(.headline)
                    Text(calendar.accessBlocked ? "Your connection is saved, but macOS is not allowing access. Enable ScribeLoop in Calendar privacy settings, then check again." : "Connect macOS Calendar, then choose which calendars appear. This is optional and does not affect recording.").font(.callout).foregroundStyle(.secondary)
                    if calendar.accessBlocked {
                        Button("Open Calendar privacy settings") { NSWorkspace.shared.open(URL(string:"x-apple.systempreferences:com.apple.preference.security?Privacy_Calendars")!) }.buttonStyle(.borderedProminent).tint(ScribeTheme.action)
                        Button("Check access again") { calendar.refresh() }.disabled(calendar.loading)
                    } else {
                        Button(calendar.permissionNeedsRequest ? "Allow Calendar access" : "Connect Calendar") { calendar.enable() }.buttonStyle(.borderedProminent).tint(ScribeTheme.action).disabled(calendar.loading)
                    }
                }
                if calendar.loading { HStack { ProgressView().controlSize(.small); Text("Connecting to Calendar…").font(.caption) } }
                if let message = calendar.message { Text(message).font(.caption).foregroundStyle(.secondary) }
                if calendar.connected {
                    Text("Calendar changes apply immediately. Disconnecting does not revoke macOS permission or remove your selections.").font(.caption).foregroundStyle(.secondary)
                }
            }
            if calendar.connected {
                Section("Calendars to show") {
                    ForEach(calendar.calendars) { choice in
                        Toggle(choice.title,isOn:Binding(get:{calendar.selected.contains(choice.id)},set:{_ in calendar.toggle(choice.id)})).toggleStyle(.checkbox)
                    }
                    if calendar.calendars.isEmpty && !calendar.loading {
                        Text("No calendars found. Add an account in macOS Calendar, then refresh.").font(.callout).foregroundStyle(.secondary)
                    }
                    Text("\(calendar.selected.count) selected. Only selected calendars appear in Your meetings.").font(.caption).foregroundStyle(.secondary)
                }
            }
        }.onAppear { calendar.refresh() }
    }
}
