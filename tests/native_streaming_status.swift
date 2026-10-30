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
        client.activeID = sid
        client.applyCallStatus(["session_id":sid,"state":"ending","remaining_s":12])
        precondition(client.callEnding && client.callStatus?.contains("12 seconds") == true)
        client.applyCallStatus(["session_id":sid,"state":"unknown"])
        precondition(!client.callEnding && client.callStatus?.contains("Press Stop") == true)
        client.applyCallStatus(["session_id":sid,"state":"recording"])
        precondition(!client.callEnding && client.callStatus == nil)
        client.applyCallStatus(["session_id":"other","state":"ending","remaining_s":1])
        precondition(client.callStatus == nil)
        print("Native streaming labels passed; no core launch, devices or audio output")
    }
}
