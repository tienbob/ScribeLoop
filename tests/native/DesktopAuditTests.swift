import Foundation

/// Runs synchronously: transport tasks never execute; no recording or audio output.
@main struct DesktopAuditTests {
    @MainActor static func main() {
        let client = DesktopClient()
        client.settings = ["transcription_engine":"ollama","language":"auto"]
        client.selectedID = "saved"
        client.detail = ["transcript_provider":"local-ollama"]
        precondition(client.savedTranscriptionCommand(action:"reprocess",sessionID:"saved")?["model"] as? String == "ollama")
        client.settings["transcription_engine"] = "deepgram"
        precondition(client.savedTranscriptionCommand(action:"resume",sessionID:"saved")?["model"] as? String == "ollama","Resume must retain the saved provider")
        precondition(client.savedTranscriptionCommand(action:"reprocess",sessionID:"saved")?["model"] as? String == "deepgram")
        var save = PreferencesSaveProgress(initialValues:"before",initialDetection:true)
        save.accept(action:"save_settings",values:"after",detection:false)
        precondition(save.initialValues == "after" && save.initialDetection == true)
        precondition(save.failure(uncertain:false).contains("already saved"))
        precondition(save.failure(uncertain:true).contains("uncertain"))
        save.accept(action:"set_detection",values:"after",detection:false)
        precondition(!save.initialDetection,"Retry should contain only changes not previously acknowledged")
        client.auditReceive(["type":"local_model_status","state":"unavailable","message":"The audio model could not load."])
        precondition(client.localModelMessage == "The audio model could not load.")
        precondition(!client.checkingLocalModel)
        client.auditReceive(["type":"local_model_status","state":"loading","status":"Loading the local model…"])
        precondition(client.checkingLocalModel && client.localModelMessage == "Loading the local model…")
        client.checkOllama(model:"")
        precondition(!client.checkingLocalModel && client.localModelMessage.contains("Enter"))
        let transport = URLSession(configuration:.ephemeral)
        let socket = transport.webSocketTask(with:URL(string:"ws://127.0.0.1:1/ws")!)
        defer { transport.invalidateAndCancel() }
        client.auditConnect(socket)  // Deliberately never resume the socket.
        let mutation = client.send(["action":"pause_detection"])!
        for n in 0..<255 { client.requests["legacy-read-\(n)"] = "unknown" }
        precondition(client.requests.count == 256)
        precondition(client.send(["action":"save_settings"]) == nil)
        for _ in 0..<300 {
            precondition(client.send(["action":"request_status","request_id":mutation]) != nil)
            precondition(client.send(["action":"history_page"]) != nil)
        }
        precondition(client.requests.count == 256, "Reads must not fill the receipt ledger")
        for action in ["stop_recording","stop","revoke_agreement"] {
            let first = client.send(["action":action])!
            precondition(client.send(["action":action]) == first, "Coalesce a pending emergency action")
            client.auditReceive(["type":"receipt","request_id":first,"status":"unknown"])
            let retry = client.send(["action":action])!
            precondition(retry != first && client.requests[first] == nil)
            client.auditReceive(["type":"receipt","request_id":retry,"status":"accepted"])
        }
        client.auditReceive(["type":"receipt","request_id":"untracked-read","status":"accepted"])
        precondition(client.requests["untracked-read"] == nil)
        client.auditReceive(["type":"receipt","request_id":mutation,"status":"accepted"])
        let settings = client.send(["action":"save_settings"])!
        client.auditReceive(["type":"receipt","request_id":settings,"status":"accepted"])
        client.requests = Dictionary(uniqueKeysWithValues:(0..<256).map { ("full-\($0)","unknown") })
        precondition(client.send(["action":"save_settings"]) == nil, "Capacity still protects ordinary mutations")
        precondition(client.send(["action":"stop_recording"]) != nil, "Capacity alone cannot block Stop")

        client.sessions = []; client.selectedID = "0"; client.activeID = "0"
        for page in 0..<5 {
            let rows: [Object] = (page*30..<page*30+30).map { ["id":String($0),"status":"completed"] }
            client.auditReceive(["type":"history_page","sessions":rows,"next_cursor":[page]])
        }
        precondition(client.sessions.count == 150 && client.sessions.first?.id == "0")
        precondition(client.selected?.id == "0" && client.activeID == "0")
        client.auditReceive(["type":"session","session":["id":"new","status":"completed"]])
        precondition(client.sessions.count == 151 && client.sessions.last?.id == "149")
        client.auditReceive(["type":"history_page","sessions":[["id":"149"]]])
        precondition(client.sessions.count == 151)
        client.selectedID = "scroll-test"
        func page(_ offset: Int, _ ids: [String], total: Int = 150) {
            client.auditReceive(["type":"meeting_page","response_to":client.auditPageRequest!,
                "session":["id":"scroll-test"],"offset":offset,"total":total,
                "segments":ids.map { ["id":$0] }])
        }
        client.loadPage(0)
        page(0,["line-1"])
        let initialScroll = client.pageSerial
        client.loadPage(0,scroll:false)
        page(0,["line-1","line-2"])
        precondition(client.rows.count == 2 && client.pageSerial == initialScroll,
            "New transcript lines must not request a scroll to the top")
        client.loadPage(100)
        page(100,["line-101"])
        precondition(client.pageSerial == initialScroll + 1,"Explicit paging still scrolls")
        precondition(!client.pageScrollToLatest,"Earlier/Later navigates to the first row")
        client.loadPage(0,anchor:"line-1")
        page(0,["line-1"])
        precondition(client.citationID == "line-1" && client.pageSerial == initialScroll + 2)
        client.loadPage(0,scroll:false)
        page(0,["line-1","line-2"])
        precondition(client.citationID == "line-1" && client.pageSerial == initialScroll + 2,
            "Refresh must not repeatedly jump back to a citation")
        client.loadPage(-1)
        page(100,["line-101","line-150"])
        precondition(client.pageScrollToLatest && client.citationID == nil,
            "Latest must scroll to the bottom, not the first row of the last page")
        precondition(client.transcriptFollow.following && client.transcriptRefreshOffset == -1)
        client.transcriptUserScrolled(nearBottom:false)
        precondition(!client.transcriptFollow.following && client.transcriptRefreshOffset == 100)
        client.auditReceive(["type":"partial","session_id":"scroll-test","stream":"mic","segment":["text":"new speech"]])
        precondition(client.transcriptFollow.unread && !client.transcriptFollow.following)
        let heldScroll = client.pageSerial
        client.loadPage(client.transcriptRefreshOffset,scroll:false)
        page(100,["line-101","line-150"])
        precondition(client.pageSerial == heldScroll && !client.transcriptFollow.following)
        client.transcriptUserScrolled(nearBottom:true)
        precondition(client.transcriptFollow.following && !client.transcriptFollow.unread)
        client.loadPage(0)
        page(0,["line-1"])
        client.transcriptUserScrolled(nearBottom:true)
        precondition(!client.transcriptFollow.following,"Bottom of an older page must not resume live following")
        client.loadPage(-1)
        page(100,(101...200).map { "line-\($0)" },total:200)
        // A latest-page refresh can cross a 100-row boundary while the user scrolls up.
        client.loadPage(-1,scroll:false)
        client.transcriptUserScrolled(nearBottom:false)
        page(200,["line-201"],total:201)
        precondition(client.offset == 100 && client.rows.last?.id == "line-200",
            "An in-flight rollover response must not replace the page being read")
        page(100,(101...200).map { "line-\($0)" },total:201)
        client.transcriptUserScrolled(nearBottom:true)
        precondition(!client.transcriptFollow.following,"A frozen old page cannot silently jump across rollover")
        client.loadPage(-1)
        page(200,["line-201"],total:201)
        precondition(client.transcriptFollow.following && client.transcriptRefreshOffset == -1)
        client.loadPage(-1)
        page(0,(1...100).map { "line-\($0)" },total:100)
        client.loadPage(-1,scroll:false)
        page(100,["line-101"],total:101)
        precondition(client.transcriptFollow.following && client.offset == 100,
            "Following crosses the first 100-row boundary")
        client.loadPage(0,anchor:"line-1")
        let navigationRequest = client.auditPageRequest
        client.loadPage(100,scroll:false)
        precondition(client.auditPageRequest == navigationRequest,
            "Automatic refresh must not supersede pending citation navigation")
        page(0,["line-1"])
        precondition(client.citationID == "line-1" && !client.transcriptFollow.following)
        client.auditReceive(["type":"recording_state","active_id":"pause-test","paused":true])
        precondition(client.recordingPaused && client.activeID == "pause-test")
        client.auditReceive(["type":"recording_state","active_id":"pause-test","paused":false])
        precondition(!client.recordingPaused)
        client.auditReceive(["type":"recording_state","paused":false])
        precondition(client.activeID == nil && !client.recordingPaused)
        print("Desktop audit regressions passed: ledger, Stop, history, transcript scroll navigation")
    }
}
