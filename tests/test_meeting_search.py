import json

from scribeloop_core.storage.db import SCHEMA_VERSION, connect, migrate
from scribeloop_core.storage.search import MeetingSearch, _summary_text
from scribeloop_core.storage.sessions import SessionStore


def meeting(store,title,words,*,at=1_700_000_000_000):
    row=store.create_recording(title,{})
    store.conn.execute('UPDATE sessions SET started_at=?,status=? WHERE id=?',(at,'completed',row['id']))
    for index,value in enumerate(words):
        store.append(row['id'],{'text':value,'start_ms':index*1000,'end_ms':index*1000+800,
                     'is_final':True,'speaker_key':f"deepgram:{row['id']}:mic:1"},stream='mic')
    store.conn.commit()
    return row['id']


def test_search_migration_and_selected_revision_invalidation():
    conn=migrate(connect(':memory:')); store=SessionStore(conn)
    assert conn.execute('PRAGMA user_version').fetchone()[0] == SCHEMA_VERSION
    sid=meeting(store,'Pricing review',['The enterprise price is forty dollars'])
    search=MeetingSearch(store)
    result=search.query('enterprise price')
    assert result['index']['complete'] and result['results'][0]['session_id']==sid
    assert result['results'][0]['source_type']=='transcript'
    assert result['results'][0]['revision']==1
    old=store.segments(sid)[0]
    with conn:
        conn.execute('INSERT INTO transcript_revisions(session_id,revision,origin) VALUES (?,?,?)',(sid,2,'replacement'))
        conn.execute('INSERT INTO segments(id,session_id,stream,local_speaker_id,start_ms,end_ms,text,language,confidence,source,revision) '
                     'VALUES (?,?,?,?,?,?,?,?,?,?,?)',('b'*32,sid,'mic',old['local_speaker_id'],0,800,
                      'The approved price is fifty dollars','en',1.0,'offline',2))
    assert not search.query('forty dollars')['results']
    updated=search.query('fifty dollars')['results']
    assert updated and updated[0]['revision']==2
    assert conn.execute('SELECT COUNT(*) FROM search_documents WHERE session_id=?',(sid,)).fetchone()[0]==2


def test_search_title_summary_accent_filters_and_delete():
    conn=migrate(connect(':memory:')); store=SessionStore(conn)
    old=meeting(store,'Kế hoạch quý',['Chúng ta thống nhất ngân sách'],at=1_600_000_000_000)
    new=meeting(store,'Customer planning',['Alice owns the launch'],at=1_800_000_000_000)
    revision=store.get(new)['transcript_revision']
    with conn:
        conn.execute('INSERT INTO grounded_summaries(id,session_id,revision,input_hash,provider,model,summary_json,created_at) '
                     'VALUES (?,?,?,?,?,?,?,?)',('c'*32,new,revision,'wrong','test','test',
                      json.dumps({'overview':{'text':'Renewal decision approved','citation_ids':[]}}),1))
    search=MeetingSearch(store)
    # A stale grounded summary is intentionally excluded.
    assert not search.query('Renewal decision')['results']
    assert search.query('ke hoach')['results'][0]['session_id']==old
    assert not search.query('ngan sach',date_from=1_700_000_000_000)['results']
    assert search.query('Alice',meeting_id=new)['results']
    store.begin_delete(old); store.finish_delete(old)
    assert not search.query('ke hoach')['results']
    if search.fts:
        assert not conn.execute("SELECT rowid FROM search_documents_fts WHERE search_documents_fts MATCH 'ke'").fetchall()


def test_interrupted_sync_is_idempotent():
    conn=migrate(connect(':memory:')); store=SessionStore(conn)
    meeting(store,'One',['alpha']); meeting(store,'Two',['beta'])
    search=MeetingSearch(store)
    first=search.sync(limit=1)
    assert not first['complete'] and first['pending']==1
    second=search.sync()
    assert second['complete'] and second['pending']==0
    count=conn.execute('SELECT COUNT(*) FROM search_documents').fetchone()[0]
    search.sync()
    assert conn.execute('SELECT COUNT(*) FROM search_documents').fetchone()[0]==count


def test_summary_search_text_excludes_internal_ids_and_citations():
    value={'overview':{'id':'private-node-id','text':'The launch is approved',
                       'citation_ids':['private-citation-id']},'decision_id':'internal'}
    assert _summary_text(value) == 'The launch is approved'
