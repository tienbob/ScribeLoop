"""The web snapshot stays metadata-only; one bounded page supplies conversation detail."""
from fastapi.testclient import TestClient
from scribeloop_core.api.server import create_app


def connect(client, token):
    return client.websocket_connect('ws://127.0.0.1/ws', headers={'origin':'http://127.0.0.1'})


def test_web_snapshot_is_paged_and_loads_one_bounded_conversation():
    app, token = create_app(storage='memory')
    with TestClient(app, base_url='http://127.0.0.1') as client:
        def seed():
            sid = app.state.store.create_recording('Paged conversation',{})['id']
            for index in range(501):
                app.state.store.append(sid,{'text':f'Segment {index}','start_ms':index*1000,
                    'end_ms':index*1000+999,'is_final':True},stream='mic')
            app.state.store.transition(sid,'completed')
            app.state.store.create_transcript_notes(sid)
            return sid
        sid = client.portal.call(seed)
        with connect(client,token) as ws:
            ws.send_text(token)
            snapshot = ws.receive_json()
            assert snapshot['type']=='snapshot' and snapshot['paged'] is True
            for field in ('segments','notes','speaker_turns','grounded_summaries','rolling_notes',
                          'cloud_usage','language_locks','checkpoints'):
                assert field not in snapshot
            assert snapshot['sessions'][0]['id']==sid
            ws.send_json({'action':'conversation_page','session_id':sid})
            page = ws.receive_json()
            assert page['type']=='conversation_page' and page['total']==501
            assert page['offset']==0 and len(page['segments'])==500
            assert page['notes'] and page['segments'][0]['text']=='Segment 0'
            ws.send_json({'action':'conversation_page','session_id':sid,'offset':500})
            last = ws.receive_json()
            assert last['offset']==500 and len(last['segments'])==1
            assert last['segments'][0]['text']=='Segment 500'
            anchor = page['segments'][1]['id']
            ws.send_json({'action':'conversation_page','session_id':sid,'anchor':anchor})
            cited = ws.receive_json()
            assert cited['offset']==0 and cited['segments'][1]['id']==anchor
            ws.send_json({'action':'conversation_page','session_id':'f'*32})
            assert ws.receive_json()['type']=='error'
            ws.send_json({'action':'conversation_page','session_id':sid,'extra':True})
            assert ws.receive_json()['type']=='error'
