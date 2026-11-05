import SwiftUI
import AppKit

/// Observe the existing SwiftUI scroll view without changing its row identity.
/// Only user scroll events change follow mode; layout/programmatic movement does not.
struct TranscriptScrollTracking: NSViewRepresentable {
    var following: Bool
    var onUserScroll: (Bool,Bool) -> Void
    final class Anchor: NSView {
        var following = false
        var onUserScroll: (Bool,Bool) -> Void = { _,_ in }
        private weak var scroll: NSScrollView?
        private var observers: [NSObjectProtocol] = []
        private var monitor: Any?
        private var queued = false
        private var lastNearBottom:Bool?
        private var lastNearTop:Bool?
        private var live = false
        private var userUntil: TimeInterval = 0
        override func viewDidMoveToWindow() { super.viewDidMoveToWindow(); attach() }
        override func viewDidMoveToSuperview() { super.viewDidMoveToSuperview(); attach() }
        override func layout() { super.layout(); attach() }
        func attach() {
            guard window != nil, let target = enclosingScrollView, let document = target.documentView else {
                if window == nil { stop() }; return
            }
            guard scroll !== target else { return }
            stop(); scroll = target
            target.contentView.postsBoundsChangedNotifications = true
            document.postsFrameChangedNotifications = true
            let center = NotificationCenter.default
            observers.append(center.addObserver(forName:NSView.boundsDidChangeNotification,object:target.contentView,queue:.main) { [weak self] _ in
                guard let self, self.live || ProcessInfo.processInfo.systemUptime < self.userUntil else { return }
                self.reportUserPosition()
            })
            observers.append(center.addObserver(forName:NSView.frameDidChangeNotification,object:document,queue:.main) { [weak self] _ in self?.scheduleBottom() })
            observers.append(center.addObserver(forName:NSScrollView.willStartLiveScrollNotification,object:target,queue:.main) { [weak self] _ in self?.live = true })
            observers.append(center.addObserver(forName:NSScrollView.didEndLiveScrollNotification,object:target,queue:.main) { [weak self] _ in
                self?.live = false; self?.reportUserPosition()
            })
            monitor = NSEvent.addLocalMonitorForEvents(matching:[.scrollWheel,.keyDown,.leftMouseDown,.leftMouseDragged]) { [weak self] event in
                guard let self, let scroll = self.scroll, event.window === scroll.window else { return event }
                if event.type == .keyDown {
                    guard [115,116,119,121,125,126].contains(event.keyCode),
                          let responder = scroll.window?.firstResponder as? NSView,
                          responder === scroll || responder.isDescendant(of:scroll) else { return event }
                } else {
                    let point = scroll.convert(event.locationInWindow,from:nil)
                    guard scroll.bounds.contains(point) else { return event }
                }
                self.userUntil = ProcessInfo.processInfo.systemUptime + 0.3
                return event
            }
            scheduleBottom()
        }
        func reportUserPosition() {
            guard let scroll, let document = scroll.documentView else { return }
            let visible = scroll.documentVisibleRect
            let bottomDistance = document.isFlipped ? document.bounds.maxY-visible.maxY : visible.minY-document.bounds.minY
            let topDistance = document.isFlipped ? visible.minY-document.bounds.minY : document.bounds.maxY-visible.maxY
            let nearBottom = bottomDistance <= 56
            let nearTop = topDistance <= 56
            // A new scroll away after resuming is a transition even if the last
            // reported position was also away from the bottom.
            let stoppedFollowing = following && !nearBottom
            if !nearBottom { following = false }
            guard stoppedFollowing || lastNearBottom != nearBottom || lastNearTop != nearTop else { return }
            lastNearBottom = nearBottom
            lastNearTop = nearTop
            DispatchQueue.main.async { [weak self] in self?.onUserScroll(nearBottom,nearTop) }
        }
        func scheduleBottom() {
            guard following, !queued else { return }
            queued = true
            DispatchQueue.main.async { [weak self] in
                guard let self else { return }; self.queued = false
                guard self.following, !self.live,
                      ProcessInfo.processInfo.systemUptime >= self.userUntil,
                      let scroll = self.scroll, let document = scroll.documentView else { return }
                let clip = scroll.contentView
                let y = document.isFlipped ? max(document.bounds.minY,document.bounds.maxY-clip.bounds.height) : document.bounds.minY
                if abs(clip.bounds.origin.y-y) > 0.5 {
                    clip.scroll(to:NSPoint(x:clip.bounds.origin.x,y:y))
                    scroll.reflectScrolledClipView(clip)
                }
            }
        }
        func stop() {
            for token in observers { NotificationCenter.default.removeObserver(token) }
            observers.removeAll()
            if let monitor { NSEvent.removeMonitor(monitor) }; monitor = nil
            scroll = nil; live = false; lastNearBottom = nil; lastNearTop = nil
        }
    }
    func makeNSView(context:Context) -> Anchor {
        let view = Anchor(); view.following = following; view.onUserScroll = onUserScroll; return view
    }
    func updateNSView(_ view:Anchor,context:Context) {
        let resumed = following && !view.following
        view.following = following; view.onUserScroll = onUserScroll; view.attach()
        if resumed { view.scheduleBottom() }
    }
    static func dismantleNSView(_ view:Anchor,coordinator:()) { view.stop() }
}

