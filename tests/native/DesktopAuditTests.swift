import Foundation

/// Runs synchronously: transport tasks never execute; no recording or audio output.
@main struct DesktopAuditTests {
    @MainActor static func main() {
        let client = DesktopClient()
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
        print("Desktop audit regressions passed: saturated ledger, recovery, emergency Stop, history pagination")
    }
}
