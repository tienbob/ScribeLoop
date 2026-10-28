import SwiftUI
import AppKit

// Keep native list navigation while drawing selection in the notebook palette.
struct NotebookListSelection: NSViewRepresentable {
    func makeNSView(context:Context) -> Anchor { Anchor() }
    func updateNSView(_ view:Anchor,context:Context) { view.suppressSystemHighlight() }
    final class Anchor:NSView {
        override func viewDidMoveToWindow() {
            super.viewDidMoveToWindow()
            suppressSystemHighlight()
        }
        func suppressSystemHighlight() {
            DispatchQueue.main.async { [weak self] in
                var ancestor = self?.superview
                while let view = ancestor {
                    if let table = view as? NSTableView {
                        table.selectionHighlightStyle = .none
                        return
                    }
                    ancestor = view.superview
                }
            }
        }
    }
}

enum ScribeTheme {
    static func adaptive(_ light:UInt32,_ dark:UInt32) -> Color {
        Color(nsColor:NSColor(name:nil) { appearance in
            let value = appearance.bestMatch(from:[.darkAqua,.aqua]) == .darkAqua ? dark : light
            return NSColor(srgbRed:CGFloat((value >> 16) & 255)/255,
                green:CGFloat((value >> 8) & 255)/255,blue:CGFloat(value & 255)/255,alpha:1)
        })
    }
    static let canvas = adaptive(0xF7F8F4,0x191E1C)
    static let sidebar = adaptive(0xEBEFE8,0x111713)
    static let panel = adaptive(0xFFFFFF,0x222A25)
    static let border = adaptive(0xD8E0D7,0x39453D)
    static let accent = adaptive(0x246B53,0x9BD3B4)
    static let action = Color(red:0.16,green:0.40,blue:0.31)
    static let subtle = adaptive(0xEDF4ED,0x26382D)
    static let warning = adaptive(0x875600,0xF3BB75)
    static let recording = adaptive(0xB52F2B,0xFFA69B)
}

struct ScribeMark: View {
    var size:CGFloat = 38
    var body: some View {
        Image(nsImage:NSApplication.shared.applicationIconImage)
            .resizable().interpolation(.high).frame(width:size,height:size)
            .accessibilityHidden(true)
    }
}

struct MeetingStatus: View {
    let status:String
    private var color:Color {
        if ["failed","interrupted"].contains(status) { return ScribeTheme.warning }
        if ["recording","preparing","transcribing"].contains(status) { return ScribeTheme.recording }
        return ScribeTheme.accent
    }
    var body: some View {
        HStack(spacing:5) { Circle().fill(color).frame(width:5,height:5); Text(status.capitalized) }
            .font(.system(size:11,weight:.medium)).foregroundStyle(color)
            .padding(.horizontal,8).padding(.vertical,4)
            .background(color.opacity(0.10),in:Capsule())
    }
}

extension View {
    func notebookCard() -> some View {
        self.padding(20).background(ScribeTheme.panel,in:RoundedRectangle(cornerRadius:16))
            .overlay { RoundedRectangle(cornerRadius:16).strokeBorder(ScribeTheme.border.opacity(0.65),lineWidth:1) }
    }
}
