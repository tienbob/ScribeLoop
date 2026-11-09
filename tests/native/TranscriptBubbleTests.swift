import Foundation

@main struct TranscriptBubbleTests {
    @MainActor static func main() {
        func row(_ id:String,_ speaker:String,_ stream:String,_ start:Int,_ end:Int,person:String = "")->Record {
            Record(raw:["id":id,"display_speaker_id":speaker,"stream":stream,"start_ms":start,"end_ms":end,"person_id":person,"speaker_label":"Same name"])
        }
        let rows = [row("a","one","mic",0,1000),row("b","one","loopback",1000,2000),
                    row("c","two","mic",2100,3000),row("d","one","mic",3100,4000)]
        let turns = TranscriptTurn.grouped(rows)
        precondition(turns.map{$0.rows.map{$0.id}} == [["a","b"],["c"],["d"]],"Turns must follow person changes, never microphone changes or repeated labels")
        precondition(turns.flatMap{$0.rows.map{$0.id}} == rows.map{$0.id},"Every segment retains its citation anchor and order")
        precondition(TranscriptTurn.grouped([row("a","","mic",0,1000),row("b","","mic",1000,2000)]).count == 2,"Unknown speakers cannot be guessed to be the same person")
        precondition(TranscriptTurn.grouped([row("a","one","mic",0,1000),row("b","one","mic",17000,18000)]).count == 2,"Long silence starts a fresh turn")
        precondition(TranscriptTurn.grouped([row("a","one","mic",0,1000,person:"alice"),row("b","two","loopback",1000,2000,person:"alice")]).count == 1,"Explicit person links unify streams")
        precondition(TranscriptTurn.grouped([]).isEmpty)
        let first = Record(raw:["id":"first","display_speaker_id":"one","stream":"mic","person_number":1,"start_ms":0,"end_ms":100])
        let second = Record(raw:["id":"second","display_speaker_id":"two","stream":"mic","person_number":2,"start_ms":100,"end_ms":200])
        let remote = Record(raw:["id":"remote","display_speaker_id":"one","stream":"loopback","person_number":1,"start_ms":100,"end_ms":200])
        precondition(TranscriptTurn.grouped([first,second]).map{$0.alignsRight} == [true,false],"Standalone recordings alternate sides by person")
        for scope in ["application","system"] {
            precondition(TranscriptTurn.grouped([first,second,remote],scope:scope).map{$0.alignsRight} == [true,true,false],"Calls always place microphone speech right and remote audio left")
            precondition(TranscriptTurn.grouped([first,remote],scope:scope).count == 2,"A call bubble cannot span both sides even for a linked person")
        }
        let client = DesktopClient()
        client.detail = ["segments":rows.map{$0.raw}]
        precondition(client.rows.map{$0.id} == rows.map{$0.id})
        precondition(client.transcriptTurns.map{$0.rows.map{$0.id}} == [["a","b"],["c"],["d"]])
        var updates = 0
        let subscription = client.objectWillChange.sink { updates += 1 }
        client.transcriptUserScrolled(nearBottom:false)
        let initial = updates
        for _ in 0..<1000 { client.transcriptUserScrolled(nearBottom:false) }
        precondition(updates == initial,"Identical scroll state must not publish 1000 whole-app redraws")
        client.detail = ["session":["capture":["scope":"application"]],"segments":[first.raw,second.raw,remote.raw]]
        precondition(client.transcriptTurns.map{$0.alignsRight} == [true,true,false])
        client.detail = [:]
        precondition(client.rows.isEmpty && client.transcriptTurns.isEmpty,"Switching/clearing a conversation invalidates the cache")
        withExtendedLifetime(subscription) {}
        print("Transcript bubbles passed: speaker turns across sources, distinct speakers on one source, unknowns, silence, linked people and citation preservation.")
    }
}
