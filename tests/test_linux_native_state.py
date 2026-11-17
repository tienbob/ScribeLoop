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


def test_call_candidate_requires_consent_and_fresh_available_discovery():
    state=LinuxState()
    state.discovery={'status':'available','permission':'authorized','candidates':[
        {'id':'old','selectable':False}, {'id':'call','selectable':True}]}
    assert state.call_candidate(set()) is None
    state.agreement={'accepted':True}
    assert state.call_candidate(set())['id']=='call'
    assert state.call_candidate({'call'}) is None
    state.sessions['active']={'status':'recording'}
    assert state.call_candidate(set()) is None
    state.sessions.clear()
    state.discovery['permission']='not_checked'
    assert state.call_candidate(set()) is None


def test_markdown_filename_uses_safe_conversation_title():
    from scribeloop_core.api.linux_native import markdown_filename

    assert markdown_filename('Chrome conversation')=='Chrome conversation.md'
    assert markdown_filename('../Weekly: review?')=='Weekly review.md'
    assert markdown_filename('')=='ScribeLoop notes.md'
    assert len(markdown_filename('x'*200))==103


def test_native_call_prompt_opens_recording_review(monkeypatch):
    from types import SimpleNamespace

    from scribeloop_core.api import linux_native

    class Dialog:
        def __init__(self, **kwargs):
            self.kwargs=kwargs
        def format_secondary_text(self, text):
            self.detail=text
        def add_button(self, *args):
            pass
        def set_keep_above(self, value):
            pass
        def connect(self, event, callback):
            self.response=callback
        def show_all(self):
            pass
        def present(self):
            pass
        def destroy(self):
            pass

    gtk=SimpleNamespace(MessageDialog=Dialog, MessageType=SimpleNamespace(QUESTION=1),
        ButtonsType=SimpleNamespace(NONE=0), ResponseType=SimpleNamespace(CANCEL=0, ACCEPT=1))
    monkeypatch.setattr(linux_native,'Gtk',gtk)
    shell=linux_native.NativeShell.__new__(linux_native.NativeShell)
    shell.state=LinuxState()
    shell.state.agreement={'accepted':True}
    shell.state.discovery={'status':'available','permission':'authorized',
        'candidates':[{'id':'call','name':'Zoom','selectable':True}]}
    shell.offered_calls=set()
    shell.pending_record=None
    shell.call_dialog=None
    shell.window=object()
    sent=[]
    reviewed=[]
    shell.client=SimpleNamespace(send=sent.append)
    shell._recording_dialog=lambda **kwargs: reviewed.append(kwargs)
    shell._offer_call()
    dialog=shell.call_dialog
    assert 'all system audio' in dialog.detail
    dialog.response(dialog,gtk.ResponseType.ACCEPT)
    assert sent==[{'action':'dismiss_candidate','candidate_id':'call'}]
    assert reviewed==[{'call_name':'Zoom'}]
    shell._offer_call()
    assert shell.call_dialog is None


def test_history_selection_keeps_gtk_rows_stable(monkeypatch):
    import pytest

    gi=pytest.importorskip('gi')
    gi.require_version('Gtk','3.0')
    from gi.repository import Gtk
    from scribeloop_core.api import linux_native

    if linux_native.Gtk is None or not Gtk.init_check([])[0]:
        pytest.skip('GTK display is unavailable')
    from types import SimpleNamespace

    monkeypatch.setattr(linux_native.Client,'start',lambda _self: None)
    shell=linux_native.NativeShell(1,'test',SimpleNamespace(poll=lambda:0))
    shell.client.send=lambda _command: True
    shell.on_backend_event({'type':'snapshot','agreement':{'accepted':True,'version':'v1'},
        'sessions':[{'id':'one','title':'One','status':'completed','started_at':1},
                    {'id':'two','title':'Two','status':'completed','started_at':2}],
        'active_id':None,'people':[],'discovery':{'status':'available','apps':[],
            'microphones':[],'candidates':[]},'settings':{},
        'summary_provider':{'configured':False},'transcription':{'configured':True}})
    original_rows={sid:entry[0] for sid,entry in shell._history_rows.items()}
    shell.history.select_row(shell._history_rows['one'][0])
    for _ in range(25):
        shell.on_backend_event({'type':'discovery','discovery':shell.state.discovery})
    assert shell.state.selected_id=='one'
    assert all(shell._history_rows[sid][0] is row for sid,row in original_rows.items())
    shell.window.disconnect_by_func(shell.on_destroy)
    shell.window.destroy()


def test_native_audio_playback_continues_and_ignores_cancelled_requests(monkeypatch):
    import base64
    import io
    import time
    import wave
    from types import SimpleNamespace

    import pytest

    gi=pytest.importorskip('gi')
    gi.require_version('Gtk','3.0')
    gi.require_version('Gst','1.0')
    from gi.repository import GLib, Gst, Gtk
    from scribeloop_core.api import linux_native

    if linux_native.Gtk is None or not Gtk.init_check([])[0]:
        pytest.skip('GTK display is unavailable')
    monkeypatch.setattr(linux_native.Client,'start',lambda _self: None)
    shell=linux_native.NativeShell(1,'test',SimpleNamespace(poll=lambda:0))
    if shell.player is None:
        pytest.skip('GStreamer playbin is unavailable')
    sink=Gst.ElementFactory.make('fakesink',None)
    if sink is None:
        pytest.skip('GStreamer fakesink is unavailable')
    sink.set_property('sync',False)
    sink.set_property('signal-handoffs',True)
    decoded=[]
    sink.connect('handoff',lambda _sink,buffer,_pad: decoded.append(buffer.get_size()))
    shell.player.set_property('audio-sink',sink)
    sent=[]
    shell.client.send=lambda command: sent.append(command.copy()) or True
    shell.state.select('saved')
    shell.state.audio_states['saved']={'available':True,'tracks':['mic','loopback'],'archived':False}
    shell._refresh_audio()
    assert shell.track_combo.get_active_id()=='conversation'
    shell.track_combo.set_active_id('mic')
    shell._refresh_audio()
    assert shell.track_combo.get_active_id()=='mic'
    shell.track_combo.set_active_id('conversation')
    shell.on_play_audio()
    info=sent[-1]
    assert info['action']=='audio_info'
    shell.on_play_audio()
    shell.on_backend_event({'type':'audio_info','session_id':'saved','request_id':info['request_id'],
        'track_durations':{'mic':61000,'loopback':61000}})
    assert len(sent)==1
    shell.on_play_audio()
    info=sent[-1]
    shell.on_backend_event({'type':'audio_info','session_id':'saved','request_id':info['request_id'],
        'track_durations':{'mic':61000,'loopback':61000}})
    request=sent[-1]
    assert request['action']=='audio_window' and request['stream']=='conversation'
    shell.on_audio_seek(shell.audio_scale,None,.2)
    shell.on_audio_seek(shell.audio_scale,None,.3)
    assert sent[-1]==request
    GLib.source_remove(shell.audio_seek_timer)
    shell._finish_audio_seek(300)
    request=sent[-1]
    assert request['start_ms']==300
    output=io.BytesIO()
    with wave.open(output,'wb') as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(16000)
        wav.writeframes(b'\xff\x1f'*1600)
    shell.on_backend_event({'type':'audio_window','session_id':'saved','request_id':request['request_id'],
        'start_ms':300,'stream':'conversation','wav_base64':base64.b64encode(output.getvalue()).decode()})
    context=GLib.MainContext.default()
    deadline=time.monotonic()+3
    while time.monotonic()<deadline and sent[-1]==request:
        context.iteration(False)
    assert sent[-1]['action']=='audio_window' and sent[-1]['start_ms']==60300
    assert sum(decoded)>0
    pending=sent[-1]
    shell.on_stop_audio()
    shell.on_backend_event({'type':'audio_window','session_id':'saved','request_id':pending['request_id'],
        'start_ms':60300,'stream':'conversation','wav_base64':base64.b64encode(output.getvalue()).decode()})
    assert shell.audio_path is None and not shell.audio_playing
    shell.on_play_audio()
    request=sent[-1]
    shell.on_backend_event({'type':'audio_window','session_id':'saved','request_id':request['request_id'],
        'start_ms':0,'stream':'conversation','wav_base64':base64.b64encode(b'invalid audio').decode()})
    deadline=time.monotonic()+3
    while time.monotonic()<deadline and shell.audio_path is not None:
        context.iteration(False)
    assert shell.audio_path is None and 'Playback failed' in shell.gst_error.get_text()
    GLib.source_remove(shell.audio_timer)
    shell.window.disconnect_by_func(shell.on_destroy)
    shell.window.destroy()
