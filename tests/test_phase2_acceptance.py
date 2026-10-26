"""Phase 2a exit checks using invented text and offline provider transports."""
import json
import re
import sqlite3

import httpx
import pytest
from fastapi.testclient import TestClient
from scribeloop_core.api.server import create_app
from scribeloop_core.llm.grounded import citation_anchor, export_markdown
from scribeloop_core.storage.db import connect, migrate
from scribeloop_core.storage.sessions import SessionStore
from test_grounded_summary import config, summary
from test_grounded_summary import saved as saved  # noqa: PLC0414 - pytest fixture re-export


def test_export_is_corrected_dated_and_citation_complete(saved):
    store,sid,row,_ = saved
    store.correct_speaker(sid,1,row['local_speaker_id'],label='Minh <script>')
    rows,fingerprint,_ = store.summary_input(sid,1)
    value = summary(row)
    value['overview']['text'] = 'Kế hoạch [click](javascript:alert(1)) <script>run()</script>'
    result = store.save_grounded_summary(sid,1,fingerprint,value,provider='offline',model='test')
    md = export_markdown(store.get(sid),rows,store.revisions(sid),result)
    assert 'Started (UTC): ' in md and 'Ended (UTC): ' in md
    assert 'Kế hoạch' in md and 'Minh &lt;script&gt;' in md
    assert '[click](javascript:' not in md and '<script>' not in md
    assert '0:00–0:01' in md and 'Meeting audio' in md
    anchors = set(re.findall(r'<a id="([a-z0-9-]+)"></a>',md))
    citations = set(re.findall(r'\[source\]\(#([a-z0-9-]+)\)',md))
    assert citations <= anchors and citation_anchor(row['id']) in citations
    # An incorrectly cached freshness flag cannot attach unrelated notes to export.
    wrong = {**result,'input_hash':'another-transcript','stale':False}
    assert '## Summary draft' not in export_markdown(store.get(sid),rows,store.revisions(sid),wrong)


def test_summary_storage_failure_keeps_previous_notes_and_title(saved):
    store,sid,row,_ = saved
    _,fingerprint,_ = store.summary_input(sid,1)
    previous = store.save_grounded_summary(sid,1,fingerprint,summary(row),provider='test',model='old')
    original_title = store.get(sid)['title']
    store.conn.execute("CREATE TRIGGER reject_summary BEFORE INSERT ON grounded_summaries "
                       "BEGIN SELECT RAISE(ABORT,'disk write fault'); END")
    with pytest.raises(sqlite3.IntegrityError):
        store.save_grounded_summary(sid,1,fingerprint,summary(row),provider='test',model='new')
    assert store.grounded_summary(sid) == previous
    assert store.get(sid)['title'] == original_title


def test_v2_database_upgrade_preserves_transcript_and_speaker_ids(saved):
    store,sid,row,path = saved
    for table in ('grounded_summaries','revision_speaker_edits','transcript_revisions'):
        store.conn.execute(f'DROP TABLE {table}')
    store.conn.execute('PRAGMA user_version=2')
    store.conn.commit()
    conn = migrate(connect(path))
    upgraded = SessionStore(conn)
    assert upgraded.segments(sid)[0]['id'] == row['id']
    assert upgraded.segments(sid)[0]['local_speaker_id'] == row['local_speaker_id']
    assert upgraded.revisions(sid)[0]['origin'] == 'legacy'
    upgraded.correct_speaker(sid,1,row['local_speaker_id'],label='Alex')
    assert 'Alex' in upgraded.notes(sid)
    upgraded.finish_delete(sid)
    assert not upgraded.revisions(sid) and not upgraded.grounded_summary(sid)
    conn.close()


def test_export_requests_are_unique_and_reject_replaced_or_deleted_selection(saved):
    store,sid,_row,path = saved
    app,token = create_app(database=path,storage='public-demo',fixtures={},backend_factory=lambda:None)
    with TestClient(app) as client, client.websocket_connect('ws://127.0.0.1/ws',headers={'origin':'http://127.0.0.1'}) as ws:
        ws.send_text(token); ws.receive_json()
        command = {'action':'export_markdown','session_id':sid,'revision':1}
        ws.send_json(command); first = ws.receive_json()
        ws.send_json(command); second = ws.receive_json()
        assert first['type'] == second['type'] == 'markdown_export'
        assert first['content'] == second['content'] and first['filename'] != second['filename']
        ws.send_json({**command,'path':'/tmp/unauthorized.md'})
        assert ws.receive_json()['type'] == 'error', 'Backend never accepts an output path'
        store.revise_transcript(sid,[{'stream':'mic','text':'replacement','is_final':True,'start_ms':0,'end_ms':100}])
        ws.send_json(command)
        assert ws.receive_json()['type'] == 'error'
        ws.send_json({**command,'revision':2})
        assert 'replacement' in ws.receive_json()['content']
        store.finish_delete(sid)
        ws.send_json({**command,'revision':2})
        assert ws.receive_json()['type'] == 'error'


@pytest.mark.asyncio
@pytest.mark.parametrize('oversized',[False,True])
async def test_summary_transport_success_and_response_cap(monkeypatch,oversized):
    original = httpx.AsyncClient
    seen = []
    def handler(request):
        seen.append(request)
        assert request.headers['authorization'] == 'Bearer private-test-key'
        body = json.loads(request.content)
        assert 'tools' not in body and body['max_tokens'] == 4000
        if oversized:
            return httpx.Response(200,content=b'x'*200001)
        return httpx.Response(200,json={'choices':[{'message':{'content':'{"summary":"test"}'}}]})
    monkeypatch.setattr(httpx,'AsyncClient',lambda **kw:original(transport=httpx.MockTransport(handler),**kw))
    if oversized:
        with pytest.raises(ValueError,match='too large'):
            await config(None).generate([{'role':'user','content':'invented'}])
    else:
        assert await config(None).generate([{'role':'user','content':'invented'}]) == '{"summary":"test"}'
    assert len(seen) == 1


@pytest.mark.parametrize('fault',['json','citation','unexpected_action'])
def test_invalid_model_output_preserves_previous_draft(saved,fault):
    store,sid,row,_ = saved
    _,fingerprint,_ = store.summary_input(sid,1)
    previous = store.save_grounded_summary(sid,1,fingerprint,summary(row),provider='test',model='old')
    invalid = summary(row)
    if fault == 'json':
        invalid = 'not JSON'
    elif fault == 'citation':
        invalid['action_items'][0]['segment_ids'] = ['foreign-meeting']
    else:
        invalid['run_tool'] = {'name':'send_email','body':'transcript'}
    with pytest.raises(ValueError):
        store.save_grounded_summary(sid,1,fingerprint,invalid,provider='test',model='new')
    assert store.grounded_summary(sid) == previous
