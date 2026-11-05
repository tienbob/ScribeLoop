import AppKit
import SwiftUI

/// Offscreen views only: no app workspace, network, microphone, playback or input injection.
@main struct TranscriptScrollTests {
    @MainActor static func pump() { RunLoop.main.run(until:Date().addingTimeInterval(0.04)) }
    @MainActor static func main() {
        _ = NSApplication.shared
        for flipped in [true,false] {
            final class Document: NSView {
                var usesFlippedCoordinates = true
                override var isFlipped: Bool { usesFlippedCoordinates }
            }
            let window = NSWindow(contentRect:NSRect(x:0,y:0,width:600,height:400),
                                  styleMask:.borderless,backing:.buffered,defer:false)
            let scroll = NSScrollView(frame:NSRect(x:0,y:0,width:600,height:400))
            scroll.hasVerticalScroller = true
            let document = Document(frame:NSRect(x:0,y:0,width:580,height:2000))
            document.usesFlippedCoordinates = flipped
            scroll.documentView = document
            let anchor = TranscriptScrollTracking.Anchor(frame:.zero)
            document.addSubview(anchor)
            window.contentView = scroll
            anchor.following = true
            var nearBottom: Bool?
            var nearTop: Bool?
            var reports = 0
            anchor.onUserScroll = { nearBottom = $0; nearTop = $1; reports += 1 }
            anchor.attach(); anchor.scheduleBottom(); pump()
            let bottom = flipped ? document.bounds.maxY-scroll.contentView.bounds.height : document.bounds.minY
            precondition(abs(scroll.contentView.bounds.origin.y-bottom)<1,"Follow reaches the bottom")
            anchor.reportUserPosition(); pump()
            precondition(nearTop == false && nearBottom == true,"The live bottom is not the top")
            document.setFrameSize(NSSize(width:580,height:2400)); pump()
            let grownBottom = flipped ? document.bounds.maxY-scroll.contentView.bounds.height : document.bounds.minY
            precondition(abs(scroll.contentView.bounds.origin.y-grownBottom)<1,"Appended content remains visible")
            scroll.contentView.scroll(to:NSPoint(x:0,y:500))
            precondition(anchor.following,"Programmatic geometry alone must not change user follow mode")
            anchor.reportUserPosition(); pump()
            precondition(nearBottom == false && !anchor.following)
            let firstReports = reports
            for _ in 0..<1000 { anchor.reportUserPosition() }
            pump()
            precondition(reports == firstReports,"Repeated positions must not trigger redundant SwiftUI updates")
            anchor.following = true
            anchor.reportUserPosition(); pump()
            precondition(reports == firstReports+1 && !anchor.following,"A fresh scroll away after resuming must still report its transition")
            let position = scroll.contentView.bounds.origin.y
            document.setFrameSize(NSSize(width:580,height:2800)); pump()
            precondition(abs(scroll.contentView.bounds.origin.y-position)<1,"Reading position survives new content")
            anchor.following = true; anchor.scheduleBottom(); pump()
            anchor.reportUserPosition(); pump()
            precondition(nearBottom == true,"Returning to the bottom can resume following")
            let topY = flipped ? document.bounds.minY : document.bounds.maxY-scroll.contentView.bounds.height
            scroll.contentView.scroll(to:NSPoint(x:0,y:topY))
            anchor.reportUserPosition(); pump()
            precondition(nearTop == true && nearBottom == false,"Reaching the top must report near the top")
            precondition(!anchor.following,"The top of a grown document is not the live bottom")
            let liveBottom = flipped ? document.bounds.maxY-scroll.contentView.bounds.height : document.bounds.minY
            scroll.contentView.scroll(to:NSPoint(x:0,y:liveBottom))
            anchor.reportUserPosition(); pump()
            precondition(nearTop == false && nearBottom == true,"Leaving the top must clear the near-top report")
            anchor.stop(); window.contentView = nil
        }
        print("Transcript scroll view regressions passed: append, preserve position, latest, near-top, flipped coordinates, cleanup")
    }
}
