"""Linux native shell state tests."""
import base64
import stat
from pathlib import Path
from types import SimpleNamespace

import pytest

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


def test_history_row_selection_uses_python_session_attribute():
    from scribeloop_core.api.linux_native import NativeShell

    class State:
        selected_id = 'old-session'

        def select(self, session_id):
            self.selected_id = session_id

        @staticmethod
        def page_payload(session_id, offset):
            return {'session_id':session_id,'offset':offset}

    class Client:
        sent = None

        def send(self, command):
            self.sent = command

    shell = NativeShell.__new__(NativeShell)
    shell.updating_history = False
    shell.state = State()
    shell.client = Client()
    shell._cleanup_playback = lambda: None
    shell.refresh = lambda: None

    NativeShell.on_history_selected(shell, None, SimpleNamespace(session_id='new-session'))

    assert shell.state.selected_id == 'new-session'
    assert shell.client.sent == {'session_id':'new-session','offset':0}


def test_history_refresh_keeps_selected_gtk_row_stable():
    from scribeloop_core.api import linux_native

    Gtk = linux_native.Gtk
    if Gtk is None:
        pytest.skip('GTK bindings are unavailable')
    initialized, _args = Gtk.init_check([])
    if not initialized:
        pytest.skip('No graphical display is available')

    class State:
        selected_id = 'first'
        sessions = [
            {'id':'first','title':'First','status':'completed','started_at':1},
            {'id':'second','title':'Second','status':'completed','started_at':2},
        ]

        def ordered_sessions(self):
            return self.sessions

    shell = linux_native.NativeShell.__new__(linux_native.NativeShell)
    shell.state = State()
    shell.history_count = Gtk.Label(label='0')
    shell.history = Gtk.ListBox()
    shell.history.set_selection_mode(Gtk.SelectionMode.SINGLE)
    shell.history.set_sort_func(shell._sort_history_rows)
    shell.history.connect('row-selected', lambda *_args: None)
    shell.updating_history = False
    shell._history_rows = {}
    shell._history_positions = {}
    window = Gtk.Window()
    window.add(shell.history)
    window.show_all()
    try:
        for index in range(50):
            if index == 10:
                shell.state.sessions.insert(0,
                    {'id':'newest','title':'Newest','status':'recording','started_at':3})
            if index == 30:
                shell.state.sessions = [row for row in shell.state.sessions
                                        if row['id'] != 'second']
            shell._refresh_history()
            while Gtk.events_pending():
                Gtk.main_iteration_do(False)

        selected = shell._history_rows['first'][0]
        rows = [shell.history.get_row_at_index(index).session_id
                for index in range(len(shell.history.get_children()))]
        assert shell.history.get_selected_row() is selected
        assert rows == ['newest','first']
    finally:
        window.destroy()
        while Gtk.events_pending():
            Gtk.main_iteration_do(False)


def test_private_playback_files_are_unique_private_and_cleanable(tmp_path, monkeypatch):
    from scribeloop_core.api.linux_native import private_playback_file

    runtime = tmp_path/'runtime'
    runtime.mkdir(mode=0o700)
    runtime.chmod(0o700)
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(runtime))
    first_directory, first_path = private_playback_file(b'first audio')
    second_directory, second_path = private_playback_file(b'second audio')
    try:
        assert first_path != second_path
        assert first_path.parent != second_path.parent
        assert stat.S_IMODE(first_path.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(first_path.stat().st_mode) == 0o600
        assert first_path.read_bytes() == b'first audio'
        assert second_path.read_bytes() == b'second audio'
    finally:
        first_directory.cleanup()
        second_directory.cleanup()
    assert not first_path.exists()
    assert not second_path.exists()


def test_private_playback_rejects_unsafe_runtime_directory(tmp_path, monkeypatch):
    from scribeloop_core.api.linux_native import private_playback_file

    runtime = tmp_path/'runtime'
    runtime.mkdir(mode=0o755)
    runtime.chmod(0o755)
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(runtime))
    with pytest.raises(PermissionError, match='Unsafe XDG runtime directory'):
        private_playback_file(b'audio')


def test_private_playback_rejects_symlinked_runtime_directory(tmp_path, monkeypatch):
    from scribeloop_core.api.linux_native import private_playback_file

    runtime = tmp_path/'runtime'
    runtime.mkdir(mode=0o700)
    runtime.chmod(0o700)
    alias = tmp_path/'runtime-link'
    alias.symlink_to(runtime, target_is_directory=True)
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(alias))
    with pytest.raises(PermissionError, match='Unsafe XDG runtime directory'):
        private_playback_file(b'audio')


def test_native_playback_replacement_and_eos_remove_plaintext(tmp_path, monkeypatch):
    from scribeloop_core.api.linux_native import NativeShell

    runtime = tmp_path/'runtime'
    runtime.mkdir(mode=0o700)
    runtime.chmod(0o700)
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(runtime))

    class Player:
        def __init__(self):
            self.states = []
            self.uri = None

        def set_state(self, state):
            self.states.append(state)

        def set_property(self, _name, value):
            self.uri = value

    class Label:
        def set_text(self, value):
            self.text = value

    shell = NativeShell.__new__(NativeShell)
    shell.state = SimpleNamespace(selected_id='session')
    shell.player = Player()
    shell.gst = SimpleNamespace(State=SimpleNamespace(NULL='null', PLAYING='playing'),
        MessageType=SimpleNamespace(EOS='eos', ERROR='error'))
    shell.gst_error = Label()

    shell._start_playback({'session_id':'session',
        'wav_base64':base64.b64encode(b'first').decode('ascii')})
    first_path = shell.audio_path
    assert first_path.is_file()
    assert Path(shell.player.uri.removeprefix('file://')).exists()

    shell._start_playback({'session_id':'session',
        'wav_base64':base64.b64encode(b'second').decode('ascii')})
    second_path = shell.audio_path
    assert not first_path.exists()
    assert second_path.is_file()

    shell._on_playback_message(None, SimpleNamespace(type='eos'))
    assert not second_path.exists()
    assert shell.audio_path is None
    assert shell.audio_directory is None


def test_native_playback_start_failure_removes_plaintext(tmp_path, monkeypatch):
    from scribeloop_core.api.linux_native import NativeShell

    runtime = tmp_path/'runtime'
    runtime.mkdir(mode=0o700)
    runtime.chmod(0o700)
    monkeypatch.setenv('XDG_RUNTIME_DIR', str(runtime))

    class Player:
        def set_state(self, state):
            if state == 'playing':
                raise RuntimeError('playback failed')

        def set_property(self, _name, _value):
            pass

    class Label:
        def set_text(self, value):
            self.text = value

    shell = NativeShell.__new__(NativeShell)
    shell.state = SimpleNamespace(selected_id='session')
    shell.player = Player()
    shell.gst = SimpleNamespace(State=SimpleNamespace(NULL='null', PLAYING='playing'))
    shell.gst_error = Label()
    shell._start_playback({'session_id':'session',
        'wav_base64':base64.b64encode(b'audio').decode('ascii')})

    assert list(runtime.iterdir()) == []
    assert shell.audio_path is None
    assert shell.audio_directory is None
    assert 'RuntimeError' in shell.gst_error.text


def test_system_capture_status_discloses_output_scope():
    from scribeloop_core.api.linux_native import recording_provider_missing, session_status_text

    system_session = {'title':'Call','status':'recording',
        'capture':{'scope':'system'}}
    assert 'Capturing all selected-output audio' in session_status_text(system_session)
    system_session['status'] = 'completed'
    assert 'Capturing all selected-output audio' not in session_status_text(system_session)
    microphone_session = {'title':'Call','status':'recording',
        'capture':{'scope':'microphone'}}
    assert 'Capturing all selected-output audio' not in session_status_text(microphone_session)


def test_missing_transcription_provider_is_actionable_after_agreement():
    from scribeloop_core.api.linux_native import recording_provider_missing

    state = SimpleNamespace(agreement={'accepted':True},
        transcription={'configured':False}, is_busy=lambda:False)
    assert recording_provider_missing(state)
    state.transcription['configured'] = True
    assert not recording_provider_missing(state)
    state.transcription['configured'] = False
    state.is_busy = lambda:True
    assert not recording_provider_missing(state)


def test_provider_preset_selects_its_url_and_custom_preserves_endpoint():
    from scribeloop_core.api.desktop_settings import SUMMARY_PRESETS
    from scribeloop_core.api.linux_native import provider_endpoint

    for provider, url in SUMMARY_PRESETS.items():
        assert provider_endpoint(provider, SUMMARY_PRESETS, 'https://custom.example/v1') == url
    assert provider_endpoint('Custom', SUMMARY_PRESETS,
        'https://custom.example/v1') == 'https://custom.example/v1'
