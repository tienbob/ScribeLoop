import Foundation
import AppKit
import Darwin

@main struct EmailSharingTests {
    @MainActor static func main() throws {
        let root = URL(fileURLWithPath:NSTemporaryDirectory()).resolvingSymlinksInPath().appendingPathComponent("scribeloop-email-\(getuid())")
        try FileManager.default.createDirectory(at:root,withIntermediateDirectories:true,attributes:[.posixPermissions:0o700])
        let id = UUID().uuidString.replacingOccurrences(of:"-",with:"").lowercased()
        let folder = root.appendingPathComponent(id)
        try FileManager.default.createDirectory(at:folder,withIntermediateDirectories:false,attributes:[.posixPermissions:0o700])
        defer { try? FileManager.default.removeItem(at:folder) }
        let file = folder.appendingPathComponent("Summary.txt")
        try Data("Synthetic notes".utf8).write(to:file)
        try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:file.path)
        let count = (try FileManager.default.attributesOfItem(atPath:file.path)[.size] as! NSNumber).intValue
        let payload:Object = ["path":file.path,"name":"Summary.txt","bytes":count]
        precondition(EmailAttachment(payload,exportID:id)?.bytes == count)
        precondition(EmailAttachment(payload,exportID:"../outside") == nil)
        precondition(EmailAttachment(payload.merging(["bytes":999]) {_,new in new},exportID:id) == nil)
        precondition(EmailAttachment(payload.merging(["name":"Secrets.html"]) {_,new in new},exportID:id) == nil)
        try FileManager.default.setAttributes([.posixPermissions:0o644],ofItemAtPath:file.path)
        precondition(EmailAttachment(payload,exportID:id) == nil,"Only private export files may be handed off")
        try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:file.path)
        let alias = folder.appendingPathComponent("Transcript.txt")
        try FileManager.default.createSymbolicLink(at:alias,withDestinationURL:file)
        precondition(EmailAttachment(["path":alias.path,"name":"Transcript.txt","bytes":count],exportID:id) == nil)
        try FileManager.default.removeItem(at:alias)
        let transcript = folder.appendingPathComponent("Transcript.txt")
        let audio = folder.appendingPathComponent("Conversation.m4a")
        try Data("Synthetic transcript".utf8).write(to:transcript)
        try Data(repeating:0,count:64).write(to:audio)
        for url in [transcript,audio] { try FileManager.default.setAttributes([.posixPermissions:0o600],ofItemAtPath:url.path) }
        func export(_ kind:String,_ url:URL)->Object {
            let bytes = (try! FileManager.default.attributesOfItem(atPath:url.path)[.size] as! NSNumber).intValue
            return ["kind":kind,"export_id":id,"subject":"Test meeting","body":"Saved summary body","attachment":["path":url.path,"name":url.lastPathComponent,"bytes":bytes]]
        }
        let values:[String:Object] = ["summary":export("summary",file),"transcript":export("transcript",transcript),"audio":export("audio",audio)]
        let all = PreparedEmail(kinds:["summary","transcript","audio"],payloads:values)!
        precondition(all.body == "Saved summary body")
        precondition(all.attachments.map{$0.name} == ["Transcript.txt","Conversation.m4a"],"Both files must enter one draft; summary belongs in its body")
        let pair = PreparedEmail(kinds:["transcript","audio"],payloads:values)!
        precondition(pair.attachments.count == 2 && pair.emailEligible)
        precondition(!pair.body.contains("Saved summary body"),"Unchecked summary must not be shared")
        precondition(PreparedEmail(kinds:["summary"],payloads:values)!.attachments.isEmpty)
        precondition(PreparedEmail(kinds:[],payloads:values) == nil)
        precondition(PreparedEmail(kinds:["audio","audio"],payloads:values) == nil)
        precondition(PreparedEmail(kinds:["transcript","audio"],payloads:["transcript":values["transcript"]!]) == nil,"Partial preparation must not open a partial draft")
        var referenced = values
        var summaryWithSources = values["summary"]!
        summaryWithSources["include_sources"] = true
        summaryWithSources["body"] = "Saved summary [1]\nSources\n[1] 0:00"
        referenced["summary"] = summaryWithSources
        precondition(PreparedEmail(kinds:["summary"],payloads:referenced) == nil,"A cached cited summary cannot enter a source-free draft")
        precondition(PreparedEmail(kinds:["summary"],payloads:values,includeSources:true) == nil,"Enabling sources requires a newly prepared cited summary")
        precondition(PreparedEmail(kinds:["summary"],payloads:referenced,includeSources:true)!.body.contains("Sources"))
        let handle = try FileHandle(forWritingTo:audio)
        try handle.truncate(atOffset:16*1024*1024); try handle.close()
        let oversized = values.merging(["audio":export("audio",audio)]) {_,new in new}
        precondition(!PreparedEmail(kinds:["transcript","audio"],payloads:oversized)!.emailEligible)
        let blocks:[Object] = [["style":"title","text":"Kiểm tra tiếng Việt"],
            ["style":"paragraph","text":"Saved overview [1, 2]"],["style":"heading","text":"Key points"],
            ["style":"bullet","text":"Literal <img src=https://example.test> content [1]"],
            ["style":"references","text":"Sources"],["style":"source","text":"[1] 0:00–0:20 · Person 1"]]
        let styled = EmailBodyRenderer.render(blocks:blocks,fallback:"fallback")
        precondition(styled.string.contains("<img src=https://example.test>"),"Content is literal text, never HTML")
        let titleFont = styled.attribute(.font,at:0,effectiveRange:nil) as! NSFont
        let sourceRange = (styled.string as NSString).range(of:"[1] 0:00")
        let sourceFont = styled.attribute(.font,at:sourceRange.location,effectiveRange:nil) as! NSFont
        precondition(titleFont.pointSize>sourceFont.pointSize && sourceFont.pointSize==11)
        let citationRange = (styled.string as NSString).range(of:"[1, 2]")
        precondition((styled.attribute(.baselineOffset,at:citationRange.location,effectiveRange:nil) as? NSNumber)?.intValue==3)
        let sourceStyle = styled.attribute(.paragraphStyle,at:sourceRange.location,effectiveRange:nil) as! NSParagraphStyle
        precondition(sourceStyle.paragraphSpacing==3,"References must not use the oversized paragraph spacing")
        precondition(EmailBodyRenderer.render(blocks:[["style":"html","text":"invalid"]],fallback:"Original text").string=="Original text")
        let preview = EmailBodyRenderer.preview(blocks:[["style":"paragraph","text":String(repeating:"x",count:20000)]],fallback:"")
        precondition(preview.length<=12001,"Rich preview remains bounded")
        let rtf = try styled.data(from:NSRange(location:0,length:styled.length),documentAttributes:[.documentType:NSAttributedString.DocumentType.rtf])
        precondition(!rtf.isEmpty,"Styled email body must serialize as rich text")
        print("Email sharing validation passed: multi-content drafts, summary body, both attachments, partial failure, size limits, private files and symlink rejection. No mail service invoked.")

    }
}
