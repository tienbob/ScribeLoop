import SwiftUI
import AppKit
import UniformTypeIdentifiers
import Darwin

struct EmailSharingContext:Identifiable {
    let conversation:Record
    let detail:Object
    var id:String { conversation.id }
}

struct EmailAttachment {
    let url:URL
    let name:String
    let bytes:Int
    init?(_ value:Object,exportID:String) {
        guard exportID.count == 32, exportID.allSatisfy({$0.isHexDigit}),
              let path = value["path"] as? String, let name = value["name"] as? String,
              ["Summary.txt","Transcript.txt","Conversation.m4a","Conversation.wav"].contains(name) else { return nil }
        let url = URL(fileURLWithPath:path).standardizedFileURL.resolvingSymlinksInPath()
        let folder = URL(fileURLWithPath:NSTemporaryDirectory()).resolvingSymlinksInPath()
            .appendingPathComponent("scribeloop-email-\(getuid())").appendingPathComponent(exportID)
        guard url.deletingLastPathComponent() == folder, url.lastPathComponent == name,
              let attributes = try? FileManager.default.attributesOfItem(atPath:url.path),
              attributes[.type] as? FileAttributeType == .typeRegular,
              (attributes[.ownerAccountID] as? NSNumber)?.uint32Value == getuid(),
              let permissions = attributes[.posixPermissions] as? NSNumber, permissions.intValue & 0o077 == 0,
              let size = attributes[.size] as? NSNumber, size.intValue>0, size.intValue<=400_000_000,
              size.intValue == (value["bytes"] as? NSNumber)?.intValue else { return nil }
        self.url = url; self.name = name; bytes = size.intValue
    }
    var sizeLabel:String { ByteCountFormatter.string(fromByteCount:Int64(bytes),countStyle:.file) }
}

/// Only local font/paragraph attributes are created; content stays literal text.
/// No HTML importer, remote resources or model-generated markup is evaluated.
struct EmailBodyRenderer {
    static func preview(blocks:[Object],fallback:String)->NSAttributedString {
        var remaining = 12000
        let bounded:[Object] = blocks.compactMap { block in
            guard remaining>0 else { return nil }
            var copy = block
            let value = String(text(block["text"]).prefix(remaining))
            remaining -= value.count
            copy["text"] = value
            return copy
        }
        return render(blocks:bounded,fallback:String(fallback.prefix(12000)))
    }
    static func render(blocks:[Object],fallback:String)->NSAttributedString {
        let allowed:Set<String> = ["title","paragraph","heading","bullet","references","source"]
        guard !blocks.isEmpty, blocks.count<=4000,
              blocks.allSatisfy({ allowed.contains(text($0["style"])) && $0["text"] is String }),
              blocks.reduce(0,{$0+text($1["text"]).utf8.count})<=2_000_000 else {
            return NSAttributedString(string:fallback,attributes:[.font:NSFont.systemFont(ofSize:14)])
        }
        let result = NSMutableAttributedString(string:"")
        let citations = try? NSRegularExpression(pattern:#"\[[0-9]+(?:, [0-9]+)*\]"#)
        for block in blocks {
            let style = text(block["style"])
            let paragraph = NSMutableParagraphStyle()
            paragraph.lineSpacing = style == "source" ? 1 : 3
            paragraph.paragraphSpacing = style == "source" ? 3 : style == "bullet" ? 7 : 12
            paragraph.paragraphSpacingBefore = ["heading","references"].contains(style) ? 10 : 0
            if style == "bullet" { paragraph.headIndent = 16 }
            let size:CGFloat = style == "title" ? 22 : style == "source" ? 11 : 14
            let weight:NSFont.Weight = ["title","heading","references"].contains(style) ? .semibold : .regular
            var attributes:[NSAttributedString.Key:Any] = [.font:NSFont.systemFont(ofSize:size,weight:weight),.paragraphStyle:paragraph]
            if style == "source" { attributes[.foregroundColor] = NSColor.gray }
            let content = (style == "bullet" ? "•  " : "") + text(block["text"]) + "\n"
            let line = NSMutableAttributedString(string:content,attributes:attributes)
            if style != "source" {
                let range = NSRange(location:0,length:line.length)
                for match in citations?.matches(in:content,range:range) ?? [] {
                    line.addAttributes([.font:NSFont.systemFont(ofSize:10),.baselineOffset:3,.foregroundColor:NSColor.gray],range:match.range)
                }
            }
            result.append(line)
        }
        return result
    }
}

@MainActor final class EmailDraftComposer:NSObject,ObservableObject,NSSharingServiceDelegate {
    @Published var status:String?
    @Published var handedOff = false
    private var service:NSSharingService?
    private let id = UUID()
    private static var active:[UUID:EmailDraftComposer] = [:]
    func open(subject:String,body:String,blocks:[Object],attachments:[EmailAttachment]) {
        guard !handedOff else { return }
        guard Self.active.count<8 else { status = "Finish one of your open email drafts before opening another."; return }
        guard attachments.reduce(0,{$0+$1.bytes})<=15*1024*1024 else { status = "Attachments exceed the combined 15 MB email limit. Save them or deselect audio."; return }
        let files:[Any] = attachments.map{$0.url as Any}
        var items:[Any] = [EmailBodyRenderer.render(blocks:blocks,fallback:body)] + files
        guard let service = NSSharingService(named:.composeEmail) else {
            status = "An email draft could not be opened. Save the files or copy the text to compose it yourself."; return
        }
        if !service.canPerform(withItems:items) { items = [body] + files }
        guard service.canPerform(withItems:items) else {
            status = "An email draft could not be opened. Save the file or copy the text, then add it in your mail app."; return
        }
        for attachment in attachments {
            do {
                // Retention starts again at handoff, not at preview creation.
                try FileManager.default.setAttributes([.modificationDate:Date()],ofItemAtPath:attachment.url.deletingLastPathComponent().path)
            } catch { status = "The attachment is unavailable. Prepare a new preview before opening a draft."; return }
        }
        service.subject = subject.components(separatedBy:.newlines).joined(separator:" ")
        service.recipients = []
        service.delegate = self; self.service = service
        Self.active[id] = self // The user may close this panel while editing in Mail.
        handedOff = true
        status = "Continue in your mail app to review the draft, choose recipients and send."
        service.perform(withItems:items)
    }
    func sharingService(_ sharingService:NSSharingService,didShareItems items:[Any]) {
        status = "Mail has finished handling the draft. Check your mail app for its status."
        service = nil; Self.active[id] = nil
    }
    func sharingService(_ sharingService:NSSharingService,didFailToShareItems items:[Any],error:Error) {
        handedOff = false
        status = "Mail could not complete the handoff. Save the file or copy the text to compose it yourself."
        service = nil; Self.active[id] = nil
    }
}

// One selection snapshot prepares all content for a single compose-service handoff.
struct PreparedEmail {
    let body:String
    let blocks:[Object]
    let attachments:[EmailAttachment]
    init?(kinds:[String],payloads:[String:Object],includeSources:Bool = false) {
        guard !kinds.isEmpty, Set(kinds).count == kinds.count,
              kinds.allSatisfy({ ["summary","transcript","audio"].contains($0) }) else { return nil }
        var files:[EmailAttachment] = []
        for kind in kinds {
            guard let value = payloads[kind], text(value["kind"]) == kind,
                  let file = EmailAttachment(value["attachment"] as? Object ?? [:],exportID:text(value["export_id"])) else { return nil }
            if kind != "summary" { files.append(file) }
        }
        if kinds.contains("summary"), (payloads["summary"]?["include_sources"] as? Bool ?? false) != includeSources { return nil }
        attachments = files
        blocks = kinds.contains("summary") ? payloads["summary"]?["body_blocks"] as? [Object] ?? [] : []
        body = kinds.contains("summary") ? text(payloads["summary"]?["body"]) :
            "Conversation: " + text(payloads[kinds[0]]?["subject"]) + "\n\nThe selected conversation files are attached."
    }
    var emailEligible:Bool { attachments.reduce(0,{$0+$1.bytes})<=15*1024*1024 }
}

struct EmailSharingPanel:View {
    @ObservedObject var core:DesktopClient
    let context:EmailSharingContext
    @Environment(\.dismiss) private var dismiss
    @StateObject private var composer = EmailDraftComposer()
    @State private var selected:Set<String> = ["summary"]
    @State private var includeSources = false
    @State private var pending:[String] = []
    @State private var requestID:String?
    @State private var payloads:[String:Object] = [:]
    @State private var exports:[String] = []
    @State private var retained:Set<String> = []
    @State private var subject = ""
    @State private var message:String?
    @State private var feedback:String?
    @State private var saving = false
    @State private var openWhenReady = false
    private let order = ["summary","transcript","audio"]
    private var kinds:[String] { order.filter{selected.contains($0)} }
    private var revision:Int { (context.detail["revision"] as? NSNumber)?.intValue ?? 0 }
    private var preparing:Bool { requestID != nil }
    private var draft:PreparedEmail? { PreparedEmail(kinds:kinds,payloads:payloads,includeSources:includeSources) }
    private func unavailable(_ kind:String)->String? {
        if kind == "summary" {
            guard let saved = context.detail["summary"] as? Object else { return "No saved summary. Generate notes from the Meeting menu first." }
            if saved["stale"] as? Bool == true { return "Summary is outdated. Generate current notes or uncheck Summary." }
        }
        if kind == "transcript", (context.detail["total"] as? Int ?? 0) == 0 { return "No saved transcript." }
        if kind == "audio", (context.detail["audio"] as? Object)?["available"] as? Bool != true { return "Audio is unavailable or expired." }
        return nil
    }
    private var selectionError:String? { kinds.compactMap{unavailable($0)}.first }
    private var locked:Bool { preparing || saving || composer.handedOff }
    var body:some View {
        VStack(alignment:.leading,spacing:16) {
            HStack(alignment:.top) {
                VStack(alignment:.leading,spacing:5) {
                    Text("Share by email").font(.system(size:28,weight:.medium,design:.serif))
                    Text(context.conversation.text("title")).font(.callout).foregroundStyle(.secondary).lineLimit(2)
                }
                Spacer()
                Button("Done") { dismiss() }.disabled(preparing || saving).keyboardShortcut(.cancelAction)
            }
            Text("Include in one email").font(.headline)
            HStack(spacing:24) {
                ForEach(order,id:\.self) { kind in
                    Toggle(kind.capitalized,isOn:Binding(get:{selected.contains(kind)},set:{ value in
                        if value { selected.insert(kind) } else { selected.remove(kind) }
                        message = nil; feedback = nil
                    })).toggleStyle(.checkbox)
                }
                Spacer()
            }.disabled(locked)
            Text("Summary goes in the message. Transcript and audio are attached automatically.").font(.callout).foregroundStyle(.secondary)
            if selected.contains("summary") {
                Toggle("Include source references",isOn:$includeSources).toggleStyle(.checkbox).disabled(locked)
                    .help("Adds citation numbers and a Sources section to the summary.")
            }
            HStack { Text("Subject").font(.callout); TextField("Subject",text:$subject).textFieldStyle(.roundedBorder).accessibilityLabel("Email subject").disabled(locked) }
            if let selectionError { Text(selectionError).font(.callout).foregroundStyle(ScribeTheme.warning) }
            ScrollView {
                VStack(alignment:.leading,spacing:12) {
                    ForEach(kinds,id:\.self) { kind in
                        VStack(alignment:.leading,spacing:8) {
                            HStack {
                                Label(kind == "summary" ? "Summary · email body" : kind == "transcript" ? "Transcript.txt" : "Conversation audio",systemImage:kind == "audio" ? "waveform" : "doc.text").font(.headline)
                                Spacer()
                                if let file = attachment(kind) {
                                    Text(file.sizeLabel).font(.caption).foregroundStyle(.secondary)
                                    if kind != "audio" { Button("Copy text") { copyText(file) }.disabled(preparing || saving) }
                                    Button("Save…") { save(file) }.disabled(preparing || saving)
                                }
                            }
                            if let value = payloads[kind] {
                                if kind == "summary" {
                                    Text(AttributedString(EmailBodyRenderer.preview(blocks:value["body_blocks"] as? [Object] ?? [],fallback:text(value["preview"]))))
                                        .textSelection(.enabled).frame(maxWidth:.infinity,alignment:.leading)
                                } else {
                                    Text(text(value["preview"])).font(.callout).textSelection(.enabled).frame(maxWidth:.infinity,alignment:.leading)
                                }
                                if value["preview_truncated"] as? Bool == true { Text("Preview shortened; the attachment includes the full text.").font(.caption).foregroundStyle(.secondary) }
                            } else {
                                Text(kind == "audio" ? "Full retained recording, combined and compressed for email. No playback." : "Saved content will be included; no new generation needed.").font(.callout).foregroundStyle(.secondary)
                            }
                        }.padding(14).background(ScribeTheme.panel,in:RoundedRectangle(cornerRadius:10))
                    }
                    if selected.isEmpty { Text("Select at least one item to share.").foregroundStyle(.secondary) }
                }.frame(maxWidth:.infinity,alignment:.leading)
            }.frame(maxHeight:.infinity)
            if preparing { HStack { ProgressView().controlSize(.small); Text("Preparing \(pending.first ?? "email")…").font(.callout).foregroundStyle(.secondary) } }
            if let draft, !draft.emailEligible { Text("Attachments exceed the combined 15 MB email limit. Save them or uncheck Audio.").font(.callout).foregroundStyle(ScribeTheme.warning) }
            if let message { Text(message).font(.callout).foregroundStyle(ScribeTheme.warning) }
            if let feedback { Text(feedback).font(.callout).foregroundStyle(.secondary) }
            if let status = composer.status { Text(status).font(.callout).foregroundStyle(.secondary) }
            Divider()
            HStack {
                Button("Preview") { prepare(open:false) }.disabled(locked || kinds.isEmpty || selectionError != nil || core.busy || !core.connected)
                Spacer()
                Button("Open email draft",systemImage:"envelope") { prepare(open:true) }
                    .buttonStyle(.borderedProminent).tint(ScribeTheme.action)
                    .disabled(locked || kinds.isEmpty || selectionError != nil || core.busy || !core.connected || draft?.emailEligible == false || subject.trimmingCharacters(in:.whitespacesAndNewlines).isEmpty)
            }
            Text("Review recipients and send in your mail app. Readable attachment copies are kept for seven days after handoff, then cleaned up on the next app use.").font(.caption2).foregroundStyle(.secondary)
        }.padding(24).frame(width:720,height:650).background(ScribeTheme.canvas).tint(ScribeTheme.accent)
            .interactiveDismissDisabled(preparing || saving)
            .onAppear { subject = context.conversation.text("title") }
            .onChange(of:includeSources) { _ in
                payloads.removeValue(forKey:"summary")
                message = nil; feedback = nil
            }
            .onChange(of:core.emailExport["export_id"] as? String) { _ in
                let value = core.emailExport
                guard let id = requestID, text(value["response_to"]) == id, text(value["session_id"]) == context.id,
                      let kind = pending.first, text(value["kind"]) == kind else { return }
                let exportID = text(value["export_id"])
                exports.append(exportID)
                guard EmailAttachment(value["attachment"] as? Object ?? [:],exportID:exportID) != nil else {
                    requestID = nil; pending = []; openWhenReady = false; message = "The prepared file is unavailable. Try again."; return
                }
                payloads[kind] = value; finishPreparedItem()
            }
            .onChange(of:core.requests[requestID ?? ""]) { status in
                if status == "accepted" { finishPreparedItem() }
                if ["rejected","unknown"].contains(status ?? "") {
                    requestID = nil; pending = []; openWhenReady = false
                    message = "Email could not be prepared. Check saved content and finish active work, then try again. Nothing was opened."
                }
            }
            .onDisappear {
                let discard = exports.filter{!retained.contains($0)}
                if !discard.isEmpty { core.send(["action":"discard_email_exports","export_ids":discard]) }
            }
    }
    private func attachment(_ kind:String)->EmailAttachment? {
        guard let value = payloads[kind] else { return nil }
        return EmailAttachment(value["attachment"] as? Object ?? [:],exportID:text(value["export_id"]))
    }
    private func prepare(open:Bool) {
        guard !locked, !kinds.isEmpty, selectionError == nil else { return }
        message = nil; feedback = nil; openWhenReady = open
        pending = kinds.filter{attachment($0) == nil}
        prepareNext()
    }
    private func finishPreparedItem() {
        // The result precedes its terminal receipt. Keep the mutation gate intact
        // until both arrive; the next export must never overtake that receipt.
        guard let id = requestID, core.requests[id] == "accepted", let kind = pending.first,
              text(payloads[kind]?["response_to"]) == id else { return }
        requestID = nil; pending.removeFirst(); prepareNext()
    }
    private func prepareNext() {
        guard let kind = pending.first else {
            if openWhenReady, let draft, draft.emailEligible {
                composer.open(subject:subject,body:draft.body,blocks:draft.blocks,attachments:draft.attachments)
                if composer.handedOff { retained = Set(kinds.compactMap{payloads[$0]?["export_id"] as? String}) }
            }
            openWhenReady = false
            return
        }
        guard exports.count<30 else { pending = []; openWhenReady = false; message = "Close this panel before preparing more files."; return }
        requestID = core.send(["action":"prepare_email_export","session_id":context.id,"revision":revision,"kind":kind,"include_sources":kind == "summary" && includeSources])
        if requestID == nil { pending = []; openWhenReady = false; message = "Reconnect and finish active work before preparing email." }
    }
    private func copyText(_ attachment:EmailAttachment) {
        guard attachment.bytes<=2_000_000, let value = try? String(contentsOf:attachment.url,encoding:.utf8) else { message = "Text could not be copied. Try saving the file instead."; return }
        NSPasteboard.general.clearContents(); NSPasteboard.general.setString(value,forType:.string)
        message = nil; feedback = "Copied."
    }
    private func save(_ attachment:EmailAttachment) {
        let panel = NSSavePanel(); panel.nameFieldStringValue = attachment.name
        panel.message = "Save a readable copy."
        guard panel.runModal() == .OK, let destination = panel.url else { return }
        saving = true; message = nil; feedback = nil
        Task {
            do {
                try await Task.detached {
                    let temporary = destination.deletingLastPathComponent().appendingPathComponent(".scribeloop-\(UUID().uuidString)")
                    defer { try? FileManager.default.removeItem(at:temporary) }
                    try FileManager.default.copyItem(at:attachment.url,to:temporary)
                    if FileManager.default.fileExists(atPath:destination.path) { _ = try FileManager.default.replaceItemAt(destination,withItemAt:temporary) }
                    else { try FileManager.default.moveItem(at:temporary,to:destination) }
                }.value
                feedback = "File saved."
            } catch { message = "The file could not be saved. Choose another location and try again." }
            saving = false
        }
    }
}
