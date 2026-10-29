"""Automatic flow requires consent and continuous healthy inactivity evidence."""
import asyncio
import time

import pytest
from fastapi.testclient import TestClient
from scribeloop_core.api import server
from scribeloop_core.llm.summary_provider import SummaryProvider
from scribeloop_core.meetings.autocall import AutoCallMonitor
from test_recording import Keys


def test_end_requires_continuous_known_inactivity_and_keep_resets_countdown():
    clock = [0.]
    monitor = AutoCallMonitor(clock=lambda:clock[0],end_grace_s=3)
    monitor.track('session','app')
    def observe(state='inactive',available=True,observed=None):
        return monitor.observe({'status':'available' if available else 'unavailable',
            'activity_observed_at':clock[0] if observed is None else observed,'activity':{'app':state}})
    assert observe('active')[1] is False
    clock[0]=1
    assert observe()[0]['remaining_s'] == 3
    clock[0]=2
    assert observe()[1] is False
    assert observe('unknown')[0]['state'] == 'unknown'
    clock[0]=3
    assert observe()[0]['remaining_s'] == 3
    clock[0]=4
    monitor.keep_recording('session')
    assert observe()[0]['remaining_s'] == 3
    clock[0]=5
    assert observe(available=False)[0]['state'] == 'unknown'
    clock[0]=6
    assert observe()[0]['remaining_s'] == 3
    clock[0]=7
    assert observe()[1] is False
    clock[0]=8
    assert observe()[1] is False
    clock[0]=9
    assert observe()[1] is True
    clock[0]=20
    assert observe()[1] is False, 'Observation gaps reset the end grace'
    clock[0]=26
    assert observe(observed=20)[0]['state'] == 'unknown'
    with pytest.raises(ValueError):
        monitor.keep_recording('another')


def test_opt_in_microphone_release_ends_despite_other_browser_audio_and_keep_overrides():
    clock=[0.]
    monitor=AutoCallMonitor(clock=lambda:clock[0],end_grace_s=3)
    def observe(microphone,activity='active',healthy=True):
        return monitor.observe({'status':'available' if healthy else 'unavailable',
            'activity_observed_at':clock[0],'activity':{'browser':activity},
            'input_activity':{'browser':microphone}})
    monitor.track('sid','browser',stop_on_input_release=True)
    for tick in range(5):
        clock[0]=float(tick)
        assert not observe('inactive')[1], 'No microphone-release inference before input was observed'
    observe('active')
    clock[0]=5
    status,ended=observe('inactive')
    assert status['reason']=='microphone_released' and status['remaining_s']==3 and not ended
    clock[0]=6
    monitor.keep_recording('sid')
    for tick in range(6,20):
        clock[0]=float(tick)
        assert observe('inactive')[0]['state']=='recording', 'Keep recording preserves receive-only listening'
    observe('active')
    clock[0]=20
    assert observe('inactive')[0]['remaining_s']==3
    clock[0]=21
    assert observe('inactive',healthy=False)[0]['state']=='unknown'
    clock[0]=22
    assert observe('inactive')[0]['remaining_s']==3
    clock[0]=23
    assert observe('inactive',activity='inactive')[0]['remaining_s']==2, 'Full inactivity must not restart the grace'
    clock[0]=24
    assert not observe('inactive')[1]
    clock[0]=25
    assert observe('inactive')[1]


def test_default_policy_preserves_muted_receive_only_calls_with_output_activity():
    clock=[0.]
    monitor=AutoCallMonitor(clock=lambda:clock[0],end_grace_s=3)
    monitor.track('sid','browser',input_was_active=True)
    for tick in range(15):
        clock[0]=float(tick)
        state,ended=monitor.observe({'status':'available','activity_observed_at':clock[0],
            'activity':{'browser':'active'},'input_activity':{'browser':'inactive'}})
        assert state['state']=='recording' and not ended


def test_mic_release_policy_uses_known_input_even_when_output_is_unknown():
    clock=[0.]
    monitor=AutoCallMonitor(clock=lambda:clock[0],end_grace_s=3)
    monitor.track('sid','browser',stop_on_input_release=True,input_was_active=True)
    def observe(mic='inactive'):
        return monitor.observe({'status':'available','activity_observed_at':clock[0],
            'activity':{'browser':'unknown'},'input_activity':{'browser':mic}})
    assert observe()[0]['reason']=='microphone_released'
    clock[0]=3
    assert observe()[1]
    assert observe('unknown')[0]['state']=='unknown'


@pytest.mark.parametrize('speech,manual,invalid_first,busy',[
    (True,False,False,False),(False,False,False,False),
    (True,True,True,False),(True,False,False,True),(True,True,'always',False)])
def test_consent_auto_stop_notes_and_topic_title(tmp_path,monkeypatch,require_sqlcipher,speech,manual,invalid_first,busy):
    state = {'activity':'active','dismissed':False,'calls':0,'summaries':0}
    class Discovery:
        def __init__(self,publish):
            pass
        async def start(self):
            pass
        async def close(self):
            pass
        def snapshot(self):
            return {'status':'available','permission':'authorized','devices_available':True,
                'activity':{'test.app':state['activity']},'activity_observed_at':time.monotonic(),
                'apps':[],'microphones':[],'candidates':[] if state['dismissed'] else [
                    {'id':'episode','app_id':'test.app','name':'Teams','kind':'meeting','selectable':True,'evidence':['input_active','output_active'],'confidence':'possible'}]}
        async def select(self,**kwargs):
            if kwargs['candidate_id'] != 'episode' or state['dismissed']:
                raise ValueError('Expired')
            return {'scope':'application','app_id':'test.app','name':'Teams','microphone_id':'default'}
        async def dismiss(self,candidate_id):
            state['dismissed']=True
    class Cloud:
        def public(self):
            return {'provider':'deepgram','model':'nova-3','configured':True,'cloud':True}
    class Recorder:
        def __init__(self,store,audio,source,publish):
            self.store,self.publish = store,publish
            self.active_id=self.task=None
            self.cloud=Cloud()
            self.stopped=asyncio.Event()
        def start(self,selection,metadata,title,*,consent_confirmed):
            assert consent_confirmed is True
            assert metadata['cloud_consent'] is True
            state['calls']+=1
            session=self.store.create_recording(title,metadata)
            self.active_id=session['id']
            self.task=asyncio.create_task(self.run(session['id']))
            return session
        async def run(self,sid):
            await self.publish({'type':'session','session':self.store.transition(sid,'recording')})
            await self.stopped.wait()
            if speech:
                self.store.append(sid,{'text':'We decided to launch the garden project.','is_final':True,'start_ms':0,'end_ms':1000})
            await self.publish({'type':'session','session':self.store.transition(sid,'processing')})
            self.store.create_transcript_notes(sid)
            await self.publish({'type':'session','session':self.store.transition(sid,'completed')})
            self.active_id=None
        def stop(self,sid,*,reason='manual_stop'):
            assert sid == self.active_id
            self.store.record_end(sid,reason)
            self.stopped.set()
        async def close(self):
            if self.task and not self.task.done():
                self.task.cancel()
                try:
                    await self.task
                except asyncio.CancelledError:
                    pass
    async def summarize(messages):
        import json
        state['summaries']+=1
        if busy and state['summaries']==1:
            while not state.get('release'):
                await asyncio.sleep(.01)
        if invalid_first and not state.get('fix') and (state['summaries']==1 or invalid_first=='always'):
            return {'invalid':'known received malformed response'}
        rows=json.loads(messages[1]['content'])['untrusted_transcript']
        item={'text':'Garden project launch','segment_ids':[rows[0]['id']]}
        return {'title':item,'overview':item,'key_points':[],'decisions':[item],
                'action_items':[],'open_questions':[],'uncertainties':[]}
    provider=SummaryProvider(settings={'SUMMARY_BASE_URL':'https://test.example/v1',
        'SUMMARY_MODEL':'chosen','SUMMARY_API_KEY':'fake-key'},request=summarize)
    monkeypatch.setattr(server,'AutoCallMonitor',lambda:AutoCallMonitor(end_grace_s=.01))
    app,token=server.create_app(database=tmp_path/'auto.sqlcipher',storage='encrypted',key_store=Keys(),
        recording_factory=Recorder,discovery_factory=Discovery,summary_provider=provider)
    grant={'action':'accept_call','candidate_id':'episode','app_id':'test.app','consent_confirmed':True,
        'automatic_summary':True,'summary_provider':provider.provider,'summary_model':provider.model,
        'summary_endpoint':provider.base_url}
    with TestClient(app) as client:
        with client.websocket_connect('ws://127.0.0.1/ws',headers={'origin':'http://127.0.0.1'}) as ws:
            ws.send_text(token)
            assert ws.receive_json()['sessions'] == [] and state['calls'] == 0
            ws.send_json({**grant,'consent_confirmed':False})
            assert ws.receive_json()['type'] == 'error' and state['calls'] == 0
            client.portal.call(app.state.store.accept_agreement, app.state.store.AGREEMENT_VERSION)
            if busy:
                def previous_meeting():
                    store=app.state.store
                    previous=store.create_recording('Previous',{})['id']
                    store.append(previous,{'text':'A previous meeting.','is_final':True,'start_ms':0,'end_ms':1000})
                    store.transition(previous,'completed')
                    return previous
                previous=client.portal.call(previous_meeting)
                ws.send_json({'action':'summarize','session_id':previous,'revision':1,
                    'provider':provider.provider,'model':provider.model,'text_consent':True})
                for _ in range(20):
                    if ws.receive_json()['type']=='summary_started': break
                else: raise AssertionError('Previous summary did not start')
            ws.send_json({**grant,'summary_endpoint':'https://changed.example/v1'})
            assert ws.receive_json()['type'] == 'error' and state['calls'] == 0
            ws.send_json(grant)
            for _ in range(20):
                event=ws.receive_json()
                if event['type'] == 'session' and event['session']['status'] == 'recording':
                    sid=event['session']['id']
                    assert event['session']['title'].startswith('Teams ·')
                    break
            else:
                raise AssertionError('Recording did not start')
            if manual:
                ws.send_json({'action':'stop','session_id':sid})
            else:
                state['activity']='inactive'
            # Disconnecting the notebook cannot prevent auto-stop/summary.
        if busy:
            deadline=time.monotonic()+5
            while time.monotonic()<deadline and client.portal.call(app.state.store.get,sid)['status']!='completed':
                time.sleep(.02)
            assert state['summaries']==1, 'Final notes must wait for the busy summary'
            assert not client.portal.call(lambda:app.state.store.conn.execute(
                "SELECT 1 FROM events WHERE session_id=? AND type='automatic_summary_attempt'",(sid,)).fetchone())
            state['release']=True
        expected_summaries=(1+int(bool(invalid_first))+int(busy)) if speech else 0
        deadline=time.monotonic()+5
        while time.monotonic()<deadline and not ((client.portal.call(app.state.store.get,sid).get('summary_failure')
                if invalid_first=='always' else client.portal.call(app.state.store.grounded_summary,sid)) if speech else
                client.portal.call(app.state.store.get,sid).get('notes_status')):
            time.sleep(.02)
        assert state['summaries'] == expected_summaries and state['calls'] == 1
        with client.websocket_connect('ws://127.0.0.1/ws',headers={'origin':'http://127.0.0.1'}) as ws:
            ws.send_text(token)
            snapshot=ws.receive_json()
            current=next(row for row in snapshot['sessions'] if row['id']==sid)
            assert current['end_reason'] == ('manual_stop' if manual else 'auto_app_inactive')
            if speech:
                if invalid_first=='always':
                    assert current['summary_failure']['code']=='summary_invalid_output'
                    assert snapshot['grounded_summaries'].get(sid) is None
                    state['fix']=True
                    ws.send_json({'action':'summarize','session_id':sid,'revision':1,
                        'provider':provider.provider,'model':provider.model,'text_consent':True})
                    for _ in range(30):
                        event=ws.receive_json()
                        if event['type']=='summary_ready':
                            assert event['session']['summary_failure'] is None
                            break
                    else: raise AssertionError('Explicit retry did not recover summary')
                    expected_summaries+=1
                else:
                    assert current['title'] == 'Garden project launch'
                    assert current['summary_failure'] is None
                    assert snapshot['grounded_summaries'][sid]['stale'] is False
            else:
                assert snapshot['sessions'][0]['notes_status'] == 'Call ended early — nothing to summarize'
                assert not client.portal.call(lambda:app.state.store.conn.execute(
                    "SELECT 1 FROM events WHERE session_id=? AND type='automatic_summary_attempt'",(sid,)).fetchone())
                client.portal.call(app.state.store.append,sid,{'text':'Recovered speech',
                    'is_final':True,'start_ms':0,'end_ms':1000})
                assert client.portal.call(app.state.store.get,sid)['notes_status'] is None
            assert state['summaries'] == expected_summaries
