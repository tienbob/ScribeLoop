"""Linux native shell state tests."""
from scribeloop_core.api.linux_state import LinuxState, summary_text


def event(kind,**values):
    return {'type':kind,**values}


def test_snapshot_selects_active_session_and_metadata_snapshot_is_paged():
    state=LinuxState()
    state.apply(event('snapshot',sessions=[{'id':'a','title':'First','status':'completed','started_at':2}],
        active_id=None,agreement={'accepted':True,'version':'v1'},people=[],discovery={'status':'available'},
        summary_provider={'configured':False},transcription={'cloud':True}))
    assert state.selected_id=='a' and state.agreement['accepted'] is True
    assert state.discovery['status']=='available'


def test_session_and_partial_events_track_live_capture():
    state=LinuxState()
    state.apply(event('session',session={'id':'live','source':'meeting','title':'Call','status':'recording'}))
    assert state.active_id=='live' and state.is_busy() is True
    state.apply(event('partial',session_id='live',stream='mic',segment={'text':'draft words'}))
    assert state.partials['live']=={'mic':'draft words'}
    state.apply(event('session',session={'id':'live','title':'Call','status':'completed'}))
    assert state.active_id is None and state.is_busy() is False


def test_conversation_page_loads_notes_transcript_and_audio_context():
    state=LinuxState()
    state.select('saved')
    state.apply(event('conversation_page',session_id='saved',offset=100,total=250,revision=2,
        segments=[{'id':'s1','text':'Hello','start_ms':0,'end_ms':1000,'revision':2}],
        notes='Transcript excerpts',summary={'summary':{'overview':{'text':'Plan discussed'}}},
        rolling={'summary':{}},budget={'requests':1},
        speaker_turns=[{'start_ms':0,'end_ms':1000,'label':'Speaker 1'}],
        language_lock={'language':'en'},cloud_usage={'requests':2},
        audio={'available':True,'tracks':['mic'],'archived':False}))
    assert state.page_offset==100 and state.page_total==250
    assert state.pages['saved']['revision']==2
    assert state.page_revision('saved')==2
    assert state.audio_states['saved']['available'] is True
    assert state.segments['saved'][0]['text']=='Hello'
    assert 'Plan discussed' in summary_text(state.saved_summaries['saved'])
    assert state.speaker_turns['saved'][0]['label']=='Speaker 1'


def test_summary_events_track_active_notes_and_page_revision_survives_paging():
    state=LinuxState()
    state.select('saved')
    state.apply(event('summary_started',session_id='saved'))
    assert state.summary_active_id=='saved'
    state.apply(event('summary_error',session_id='saved',message='Provider failed'))
    assert state.summary_active_id is None
    state.apply(event('conversation_page',session_id='saved',offset=0,total=1,revision=3,
        segments=[{'id':'s1','revision':3}]))
    assert state.page_revision('saved')==3
    state.apply(event('summary_started',session_id='saved'))
    state.apply(event('summary_ready',session_id='saved',summary={'summary':{}},
        session={'id':'saved','status':'completed'},budget={'requests':1}))
    assert state.summary_active_id is None
    assert state.page_revision('saved')==3


def test_record_payload_uses_validated_wire_shape():
    from scribeloop_core.api.server import StartRecording
    state=LinuxState()
    payload=state.record_payload(scope='microphone',microphone_id='default',
        title='Meeting',duration_s=300,language='auto',consent_confirmed=True)
    request=StartRecording.model_validate(payload)
    assert request.action=='record' and request.request_id==1
    assert request.scope=='microphone' and request.microphone_id=='default'
    assert request.consent_confirmed is True and request.cloud_consent is True
    assert request.app_id is None and request.candidate_id is None
    assert request.language_hints is None
