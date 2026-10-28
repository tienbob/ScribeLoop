import Foundation

@main struct StreamingStatusSmoke {
    @MainActor static func main() {
        let client = DesktopClient(database:"/tmp/not-opened.sqlcipher")
        let sid = String(repeating:"a",count:32)
        client.activeID = sid
        client.streamingProgress["mic"] = ["session_id":sid,"send_lag_ms":1500,"lag_ms":0]
        precondition(client.streamingLabel?.contains("Catching up") == true)
        client.streamingProgress["mic"] = ["session_id":sid,"send_lag_ms":0,"lag_ms":2500]
        precondition(client.streamingLabel?.contains("Awaiting confirmed text") == true)
        client.streamingProgress["mic"] = ["session_id":sid,"send_lag_ms":0,"lag_ms":0]
        precondition(client.streamingLabel == "Live transcription connected")
        client.activeID = nil
        precondition(client.streamingLabel == nil)
        print("Native streaming labels passed; no core launch, devices or audio output")
    }
}
