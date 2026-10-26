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


def test_consent_auto_stop_notes_and_topic_title(tmp_path,monkeypatch,require_sqlcipher):
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
            self.store.append(sid,{'text':'We decided to launch the garden project.','is_final':True,'start_ms':0,'end_ms':1000})
            await self.publish({'type':'session','session':self.store.transition(sid,'processing')})
            self.store.create_transcript_notes(sid)
            await self.publish({'type':'session','session':self.store.transition(sid,'completed')})
            self.active_id=None
        def stop(self,sid):
            assert sid == self.active_id
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
            state['activity']='inactive'
            # Disconnecting the notebook cannot prevent auto-stop/summary.
        deadline=time.monotonic()+5
        while time.monotonic()<deadline and not state['summaries']:
            time.sleep(.02)
        assert state['summaries'] == 1 and state['calls'] == 1
        with client.websocket_connect('ws://127.0.0.1/ws',headers={'origin':'http://127.0.0.1'}) as ws:
            ws.send_text(token)
            snapshot=ws.receive_json()
            assert snapshot['sessions'][0]['id'] == sid
            assert snapshot['sessions'][0]['title'] == 'Garden project launch'
            assert snapshot['grounded_summaries'][sid]['stale'] is False
            assert state['summaries'] == 1
