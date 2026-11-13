"""Native Linux shell for the macOS-style notebook layout."""
from __future__ import annotations

import base64
import json
import os
import stat
import tempfile
import threading
import time
from contextlib import suppress
from pathlib import Path

try:
    import gi

    gi.require_version('Gdk','3.0')
    gi.require_version('Gtk','3.0')
    from gi.repository import Gdk, GLib, Gtk
except (ImportError,ValueError):
    # The shell reducer and packaging checks import this module without a GUI.
    Gdk = GLib = Gtk = None

from websockets.sync.client import connect

from .linux_state import (
    LinuxState,
    session_label,
    speaker_label,
    summary_text,
    timestamp,
)

CSS = b"""
* { color:#202d29; font-family:"Cantarell"; }
window { color:#202d29; background:#f2f4ef; }
dialog { color:#202d29; background:#f2f4ef; }
dialog label, label { color:#202d29; }
entry, textview, textview text, spinbutton, combobox, combobox button {
    color:#202d29; background-color:#ffffff; caret-color:#bb563b;
}
entry { border:1px solid #cbd5ce; border-radius:5px; padding:8px 10px; }
entry:focus, textview:focus, spinbutton:focus { border-color:#34745d; }
button { color:#26352f; background:#ffffff; border:1px solid #d1dbd4;
    border-radius:6px; padding:7px 12px; font-weight:600; }
button:hover { background:#e9efea; border-color:#9caf9f; }
button:active { background:#dce7df; }
button:disabled { color:#87918b; background:#edf0ec; border-color:#e0e5e0; }
headerbar.header { color:#f8faf8; background:#1f3a32; border:0;
    min-height:58px; box-shadow:none; }
headerbar.header label { color:#f8faf8; }
headerbar.header button { color:#f8faf8; background:#2b4b40; border-color:#3b5d50; }
headerbar.header button:hover { background:#385b4d; }
headerbar.header .accent-button { color:#ffffff; background:#bd563c; border-color:#bd563c; }
headerbar.header .accent-button:hover { background:#a94630; }
.brand { font-family:"Cantarell"; font-size:19px; font-weight:800; color:#ffffff; }
.subtitle { font-size:9px; font-weight:700; color:#b9d1c4; }
.sidebar { color:#26352f; background:#e8ede7; border-right:1px solid #d4ddd5; }
.section-label { font-size:10px; font-weight:800; color:#65756b; }
.library-count { color:#315b49; background:#d6e5d9; border-radius:12px; padding:2px 8px; }
.workspace { background:#f2f4ef; }
.eyebrow { color:#a34c35; font-size:10px; font-weight:800; }
.page-title { color:#1e322b; font-size:24px; font-weight:800; }
.detail-status, .status-label { color:#68776e; font-size:11px; }
.connection-status { color:#51665a; font-size:10px; }
.card { color:#202d29; background:#ffffff; border-radius:6px;
    border:1px solid #dce3dd; padding:14px; }
.section-heading { color:#20352c; font-size:15px; font-weight:800; }
.section-caption { color:#748078; font-size:11px; }
.view-tabs button { color:#66766d; background:transparent; border:0;
    border-bottom:2px solid transparent; border-radius:0; padding:8px 18px; }
.view-tabs button:hover { color:#203d31; background:#e5ebe5; }
.view-tabs button:checked { color:#24513e; background:#e5ece5; border-bottom-color:#bd563c; }
.accent-button { color:#ffffff; background:#2d684f; border-color:#2d684f; font-weight:800; }
.accent-button:hover { color:#ffffff; background:#24563f; border-color:#24563f; }
.danger-button { color:#9e432f; background:#fff3ee; border-color:#e8c8bc; }
.history-row { background:transparent; border-radius:5px; padding:8px; }
list row { color:#26352f; background:transparent; border-radius:5px; }
list row:hover { background:#dde7de; }
list row:selected { color:#1e392e; background:#d3e2d5; }
.history-title { color:#24352e; font-size:13px; font-weight:700; }
.history-meta { color:#748078; font-size:10px; }
.notes-editor { color:#283630; background:#ffffff; font-size:13px; }
.transcript-row { color:#26352f; background:#ffffff; border:1px solid #e0e6e1;
    border-radius:5px; padding:9px 11px; }
.partial-row { color:#4b4034; background:#fff5e8; border:1px dashed #e2c89f;
    border-radius:5px; padding:9px 11px; }
.speaker { font-size:10px; font-weight:800; color:#347258; }
.text { font-size:13px; color:#26352f; }
.dialog-panel { color:#202d29; background:#ffffff; border-radius:7px; padding:16px; }
.form-grid { color:#25352e; }
.dialog-help { color:#66766d; font-size:11px; }
.dialog-status { color:#9b4934; font-size:11px; font-weight:700; }
checkbutton, checkbutton label { color:#26352f; }
separator { background:#dce3dd; }
"""


def private_playback_file(data):
    runtime_dir = os.environ.get('XDG_RUNTIME_DIR')
    if runtime_dir:
        info = Path(runtime_dir).lstat()
        if (not stat.S_ISDIR(info.st_mode) or info.st_uid != os.getuid()
                or info.st_mode & 0o077):
            raise PermissionError('Unsafe XDG runtime directory')
    directory = tempfile.TemporaryDirectory(prefix='scribeloop-playback-', dir=runtime_dir)
    try:
        with tempfile.NamedTemporaryFile(mode='wb', prefix='audio-', suffix='.wav',
                dir=directory.name, delete=False) as stream:
            stream.write(data)
            stream.flush()
            path = Path(stream.name)
        return directory, path
    except BaseException:
        directory.cleanup()
        raise


def session_status_text(session):
    text = session_label(session)
    capture = session.get('capture') or {}
    if (session.get('status') in {'preparing', 'recording', 'stopping'}
            and capture.get('scope') == 'system'):
        return f'{text} · Capturing all selected-output audio'
    return text


def recording_provider_missing(state):
    return (state.agreement.get('accepted') is True and not state.is_busy()
            and state.transcription.get('configured') is not True)


def provider_endpoint(provider, presets, current_endpoint):
    return presets.get(provider, current_endpoint)


def clear_box(box):
    for child in list(box.get_children()):
        box.remove(child)


def add_class(widget,*classes):
    context=widget.get_style_context()
    for name in classes:
        context.add_class(name)


def make_label(text='',*,classes=(),wrap=False,selectable=False,xalign=0):
    label=Gtk.Label(label=text)
    label.set_xalign(xalign)
    label.set_line_wrap(wrap)
    label.set_selectable(selectable)
    for name in classes:
        label.get_style_context().add_class(name)
    return label


class Client:
    def __init__(self,port,token,on_event):
        self.url=f'ws://127.0.0.1:{port}/ws'
        self.token=token
        self.on_event=on_event
        self.ws=None
        self.send_lock=threading.Lock()
        self.started=time.monotonic()

    def start(self):
        threading.Thread(target=self._run,name='scribeloop-native-client',daemon=True).start()

    def _run(self):
        delay=.5
        while time.monotonic()-self.started < 3600:
            try:
                with connect(self.url,open_timeout=5,close_timeout=2,max_size=16_000_000) as ws:
                    self.ws=ws
                    ws.send(self.token)
                    delay=.5
                    while True:
                        raw=ws.recv()
                        if isinstance(raw,bytes):
                            raw=raw.decode('utf-8','replace')
                        GLib.idle_add(self.on_event,json.loads(raw))
            except Exception as exc:  # noqa: BLE001 - sanitize transport details
                self.ws=None
                GLib.idle_add(self.on_event,{'type':'connection_lost',
                    'message':f'Connection interrupted ({type(exc).__name__}). Retrying…'})
                time.sleep(delay)
                delay=min(5.0,delay*2)

    def send(self,command):
        ws=self.ws
        if ws is None:
            GLib.idle_add(self.on_event,{'type':'error','message':'Reconnecting to the local backend.'})
            return False
        try:
            with self.send_lock:
                ws.send(json.dumps(command))
            return True
        except Exception as exc:  # noqa: BLE001 - report transport class only
            self.ws=None
            GLib.idle_add(self.on_event,{'type':'error',
                'message':f'Command could not be sent ({type(exc).__name__}).'})


class NativeShell:
    def __init__(self,port,token,child):
        self.state=LinuxState()
        self.child=child
        self.selected_source=None
        self.pending_record=None
        self.audio_path=None
        self.audio_directory=None
        self.audio_session=None
        self.updating_history=False
        self._history_rows={}
        self._history_positions={}
        self.agreement_shown=False
        self.settings_dialog=None
        self.settings_save_pending=False
        self.settings_save_status=None
        self.client=Client(port,token,self.on_backend_event)

        self.window=Gtk.Window(title='ScribeLoop')
        self.window.set_default_size(1200,820)
        add_class(self.window,'window')
        self.window.connect('destroy',self.on_destroy)

        header=Gtk.HeaderBar()
        header.set_show_close_button(True)
        header.set_title('ScribeLoop')
        header.set_custom_title(self._header_title())
        add_class(header,'header')
        self.new_button=Gtk.Button(label='New recording')
        self.new_button.connect('clicked',self.on_new_recording)
        add_class(self.new_button,'accent-button')
        self.stop_button=Gtk.Button(label='Stop')
        self.stop_button.connect('clicked',self.on_stop)
        add_class(self.stop_button,'danger-button')
        self.prefs_button=Gtk.Button(label='Settings')
        self.prefs_button.connect('clicked',self.on_settings)
        header.pack_start(self.new_button)
        header.pack_start(self.stop_button)
        header.pack_end(self.prefs_button)
        self.window.set_titlebar(header)

        root=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=0)
        self.window.add(root)
        root.pack_start(self._sidebar(),False,False,0)
        main=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=12)
        add_class(main,'workspace')
        main.set_margin_top(20);main.set_margin_bottom(14)
        main.set_margin_start(22);main.set_margin_end(22)
        root.pack_start(main,True,True,0)

        eyebrow=make_label('MEETING NOTEBOOK',classes=('eyebrow',))
        eyebrow.set_halign(Gtk.Align.START)
        self.detail_title=make_label('Ready when you are',classes=('page-title',))
        self.detail_title.set_halign(Gtk.Align.START)
        self.detail_status=make_label('Connecting…',classes=('detail-status',))
        self.detail_status.set_halign(Gtk.Align.START)
        self.stack=Gtk.Stack()
        self.stack.set_hexpand(True);self.stack.set_vexpand(True)
        switcher=Gtk.StackSwitcher();switcher.set_stack(self.stack)
        add_class(switcher,'view-tabs')
        switcher.set_halign(Gtk.Align.CENTER)
        main.pack_start(eyebrow,False,False,0)
        main.pack_start(self.detail_title,False,False,0)
        main.pack_start(self.detail_status,False,False,0)
        main.pack_start(switcher,False,False,0)
        main.pack_start(self.stack,True,True,0)
        self.stack.add_titled(self._notes_view(),'notes','Notes')
        self.stack.add_titled(self._transcript_view(),'transcript','Transcript')
        self.stack.add_titled(self._audio_view(),'audio','Audio')
        self.stack.set_visible_child_name('notes')

        self.status=make_label('Connecting to the local backend…',classes=('connection-status',))
        self.status.set_halign(Gtk.Align.START)
        main.pack_start(self.status,False,False,0)

        self.window.show_all()
        self._load_css()
        self.refresh()
        self.client.start()

    def _header_title(self):
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=1)
        title=make_label('ScribeLoop',classes=('brand',))
        subtitle=make_label('LINUX NATIVE NOTEBOOK',classes=('subtitle',))
        box.pack_start(title,False,False,0)
        box.pack_start(subtitle,False,False,0)
        return box

    def _sidebar(self):
        sidebar=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=10)
        sidebar.set_size_request(278,-1)
        sidebar.set_margin_top(20);sidebar.set_margin_bottom(14)
        sidebar.set_margin_start(14);sidebar.set_margin_end(14)
        add_class(sidebar,'sidebar')
        label=make_label('LIBRARY',classes=('section-label',))
        label.set_halign(Gtk.Align.START)
        self.history_count=make_label('0',classes=('section-label',))
        add_class(self.history_count,'library-count')
        count_box=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        count_box.pack_start(label,False,False,0)
        count_box.pack_end(self.history_count,False,False,0)
        library_title=make_label('Recent conversations',classes=('section-heading',))
        library_title.set_halign(Gtk.Align.START)
        sidebar.pack_start(count_box,False,False,2)
        sidebar.pack_start(library_title,False,False,4)
        self.history=Gtk.ListBox()
        self.history.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.history.set_sort_func(self._sort_history_rows)
        self.history.connect('row-selected',self.on_history_selected)
        scrolled=Gtk.ScrolledWindow()
        scrolled.set_vexpand(True);scrolled.set_hexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER,Gtk.PolicyType.AUTOMATIC)
        scrolled.add(self.history)
        sidebar.pack_start(scrolled,True,True,0)
        return sidebar

    def _notes_view(self):
        view=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=12)
        add_class(view,'card')
        heading=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=3)
        heading.pack_start(make_label('Meeting notes',classes=('section-heading',)),False,False,0)
        heading.pack_start(make_label('Summary and source references',classes=('section-caption',)),False,False,0)
        view.pack_start(heading,False,False,0)
        self.notes_text=Gtk.TextView()
        add_class(self.notes_text,'notes-editor')
        self.notes_text.set_editable(False);self.notes_text.set_cursor_visible(False)
        self.notes_text.set_wrap_mode(Gtk.WrapMode.WORD)
        self.notes_text.set_left_margin(12);self.notes_text.set_right_margin(12)
        self.notes_text.set_top_margin(12);self.notes_text.set_bottom_margin(12)
        notes_scroll=Gtk.ScrolledWindow()
        notes_scroll.set_vexpand(True);notes_scroll.set_hexpand(True)
        notes_scroll.set_policy(Gtk.PolicyType.NEVER,Gtk.PolicyType.AUTOMATIC)
        notes_scroll.add(self.notes_text)
        self.generate_button=Gtk.Button(label='Generate summary')
        self.generate_button.connect('clicked',self.on_summarize)
        self.export_button=Gtk.Button(label='Download Markdown')
        self.export_button.connect('clicked',self.on_export)
        actions=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=8)
        actions.pack_start(self.generate_button,False,False,0)
        actions.pack_start(self.export_button,False,False,0)
        view.pack_start(notes_scroll,True,True,0)
        view.pack_start(actions,False,False,0)
        return view

    def _transcript_view(self):
        view=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=10)
        self.transcript_list=Gtk.ListBox()
        add_class(self.transcript_list,'transcript-list')
        self.transcript_list.set_selection_mode(Gtk.SelectionMode.NONE)
        self.transcript_list.set_activate_on_single_click(False)
        transcript_scroll=Gtk.ScrolledWindow()
        transcript_scroll.set_vexpand(True);transcript_scroll.set_hexpand(True)
        transcript_scroll.set_policy(Gtk.PolicyType.NEVER,Gtk.PolicyType.AUTOMATIC)
        transcript_scroll.add(self.transcript_list)
        self.earlier_button=Gtk.Button(label='Earlier')
        self.earlier_button.connect('clicked',self.on_earlier)
        self.later_button=Gtk.Button(label='Later')
        self.later_button.connect('clicked',self.on_later)
        self.transcript_window=make_label('',classes=('status-label',))
        pager=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=8)
        add_class(pager,'transcript-toolbar')
        pager.pack_start(self.earlier_button,False,False,0)
        pager.pack_start(self.transcript_window,False,False,0)
        pager.pack_end(self.later_button,False,False,0)
        view.pack_start(pager,False,False,0)
        view.pack_start(transcript_scroll,True,True,0)
        return view

    def _audio_view(self):
        view=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=12)
        add_class(view,'card')
        heading=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=3)
        heading.pack_start(make_label('Audio archive',classes=('section-heading',)),False,False,0)
        heading.pack_start(make_label('Listen to retained meeting audio',classes=('section-caption',)),False,False,0)
        view.pack_start(heading,False,False,0)
        self.audio_status=make_label('Audio is unavailable.',classes=('status-label',))
        self.audio_status.set_halign(Gtk.Align.START)
        self.audio_status.set_line_wrap(True)
        self.track_combo=Gtk.ComboBoxText()
        self.minute_spin=Gtk.SpinButton.new_with_range(0,180,1)
        self.play_button=Gtk.Button(label='Play selected minute')
        self.play_button.connect('clicked',self.on_play_audio)
        self.archive_button=Gtk.Button(label='Archive audio')
        self.archive_button.connect('clicked',self.on_archive_audio)
        row=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=8)
        add_class(row,'audio-toolbar')
        row.pack_start(Gtk.Label(label='Track'),False,False,0)
        row.pack_start(self.track_combo,False,False,0)
        row.pack_start(Gtk.Label(label='Minute'),False,False,0)
        row.pack_start(self.minute_spin,False,False,0)
        row.pack_start(self.play_button,False,False,0)
        row.pack_end(self.archive_button,False,False,0)
        self.gst_error=make_label('',classes=('status-label',))
        view.pack_start(self.audio_status,False,False,0)
        view.pack_start(row,False,False,0)
        view.pack_start(self.gst_error,False,False,0)
        self._init_playback()
        return view

    def _load_css(self):
        screen=Gdk.Screen.get_default()
        provider=Gtk.CssProvider()
        provider.load_from_data(CSS)
        Gtk.StyleContext.add_provider_for_screen(screen,provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION)

    def _init_playback(self):
        self.player=None
        try:
            import gi
            gi.require_version('Gst','1.0')
            from gi.repository import Gst
            Gst.init(None)
            self.gst=Gst
            self.player=Gst.ElementFactory.make('playbin','scribeloop-player')
            if self.player is None:
                self.gst_error.set_text('GStreamer playback is unavailable.')
            else:
                self.playback_bus=self.player.get_bus()
                self.playback_bus.add_signal_watch()
                self.playback_bus.connect('message',self._on_playback_message)
        except Exception as exc:  # noqa: BLE001 - show transport class, not system paths
            self.player=None
            self.gst_error.set_text(f'Audio playback unavailable ({type(exc).__name__}).')

    def _cleanup_playback(self):
        player=getattr(self,'player',None)
        if player is not None:
            with suppress(Exception):
                player.set_state(self.gst.State.NULL)
        directory=getattr(self,'audio_directory',None)
        self.audio_directory=None
        self.audio_path=None
        self.audio_session=None
        if directory is not None:
            with suppress(OSError):
                directory.cleanup()

    def _on_playback_message(self,_bus,message):
        if message.type==self.gst.MessageType.EOS:
            self._cleanup_playback()
        elif message.type==self.gst.MessageType.ERROR:
            error,_debug=message.parse_error()
            self.gst_error.set_text(f'Playback failed ({type(error).__name__}).')
            self._cleanup_playback()

    def on_destroy(self,*_args):
        self._cleanup_playback()
        if self.child.poll() is None:
            try:
                self.child.stdin.write('STOP\n');self.child.stdin.flush()
            except Exception:  # noqa: BLE001,S110 - shutdown is best-effort
                pass
        Gtk.main_quit()

    def on_backend_event(self,event):
        kind=event.get('type')
        selected_before=self.state.selected_id
        if kind=='snapshot':
            self.status.set_text('Connected to the local backend.')
        if kind in {'snapshot','conversation_page','session','segment','partial','transcript_replaced',
                    'speakers_corrected','people_changed','agreement','settings','discovery',
                    'audio_state','cloud_usage','speaker_turns','language_locked','rolling_notes',
                    'summary_started','summary_ready','summary_error','session_deleted'}:
            self.state.apply(event)
            if self.state.selected_id!=selected_before:
                self._cleanup_playback()
        if kind=='settings' and self.settings_save_pending:
            self.settings_save_pending=False
            self.status.set_text('Settings saved.')
            if self.settings_dialog is not None:
                self.settings_dialog.destroy()
                self.settings_dialog=None
                self.settings_save_status=None
        if kind=='error' and event.get('context')=='settings':
            self.settings_save_pending=False
            message=event.get('message') or 'Settings could not be saved. Check the fields and try again.'
            self.status.set_text(message)
            if self.settings_save_status is not None:
                self.settings_save_status.set_text(message)
        if kind=='source_selected' and self.pending_record is not None \
                and event.get('request_id')==self.pending_record.get('request_id'):
            self.selected_source=event.get('source')
            command=self.pending_record.get('record')
            self.pending_record=None
            self.client.send(command)
        if kind=='audio_playback':
            self._start_playback(event)
        if kind=='markdown_export':
            self._save_markdown(event)
        if kind=='summary_started':
            self.status.set_text('Generating notes…')
        if kind=='summary_ready':
            self.status.set_text('Notes saved. Review its sources.')
        if kind=='summary_error':
            self.status.set_text(event.get('message') or 'Notes could not finish. Generate summary can retry.')
        if kind=='snapshot' and self.state.selected_id:
            self.client.send(self.state.page_payload(self.state.selected_id,0))
        if kind=='snapshot' and self.state.agreement.get('accepted') is not True \
                and not self.agreement_shown:
            self.agreement_shown=True
            self._show_agreement()
        if kind in {'connection_lost','error'}:
            self.status.set_text(event.get('message') or 'Connection error.')
        self.refresh()

    def refresh(self):
        self._refresh_history()
        self._refresh_detail()
        self._refresh_notes()
        self._refresh_transcript()
        self._refresh_audio()
        self._refresh_actions()

    def _refresh_history(self):
        rows=self.state.ordered_sessions()
        self.history_count.set_text(str(len(rows)))
        self._history_positions={session['id']:position
                                 for position,session in enumerate(rows)}
        self.updating_history=True
        try:
            for sid in tuple(self._history_rows):
                if sid not in self._history_positions:
                    row,_,_=self._history_rows.pop(sid)
                    self.history.remove(row)
            for session in rows:
                sid=session['id']
                entry=self._history_rows.get(sid)
                if entry is None:
                    row=Gtk.ListBoxRow()
                    row.session_id=sid
                    box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=2)
                    add_class(box,'history-row')
                    box.set_margin_top(6);box.set_margin_bottom(6)
                    box.set_margin_start(8);box.set_margin_end(8)
                    title=make_label('',classes=('history-title',),wrap=True,xalign=0)
                    status=make_label('',classes=('history-meta',),xalign=0)
                    box.pack_start(title,False,False,0)
                    box.pack_start(status,False,False,0)
                    row.add(box)
                    self.history.add(row)
                    entry=(row,title,status)
                    self._history_rows[sid]=entry
                row,title,status=entry
                title.set_text(str(session.get('title') or 'Untitled conversation'))
                status.set_text(str(session.get('status') or '').replace('_',' ').title())
            self.history.invalidate_sort()
            selected=self._history_rows.get(self.state.selected_id)
            selected_row=selected[0] if selected else None
            if selected_row is not None and self.history.get_selected_row() is not selected_row:
                self.history.select_row(selected_row)
            elif selected_row is None and self.history.get_selected_row() is not None:
                self.history.unselect_all()
            self.history.show_all()
        finally:
            self.updating_history=False

    def _sort_history_rows(self,left,right,_user_data=None):
        positions=self._history_positions
        left_position=positions.get(getattr(left,'session_id',''),len(positions))
        right_position=positions.get(getattr(right,'session_id',''),len(positions))
        return (left_position>right_position)-(left_position<right_position)

    def _refresh_detail(self):
        session=self.state.selected_session()
        if session:
            self.detail_title.set_text(str(session.get('title') or 'Untitled conversation'))
            self.detail_status.set_text(session_status_text(session))
        else:
            self.detail_title.set_text('Ready when you are')
            self.detail_status.set_text('No conversation selected')

    def _refresh_actions(self):
        busy=self.state.is_busy()
        accepted=self.state.agreement.get('accepted') is True
        summary_ready=self.state.summary_provider.get('configured') is True
        self.new_button.set_sensitive(accepted and not busy)
        self.stop_button.set_sensitive(busy and self.state.active_id is not None)
        self.generate_button.set_sensitive(accepted and summary_ready and not busy and
            self.state.summary_active_id is None and self.state.selected_id is not None)
        self.export_button.set_sensitive(accepted and not busy and self.state.selected_id is not None)

    def _refresh_notes(self):
        sid=self.state.selected_id
        saved=self.state.saved_summaries.get(sid)
        text=summary_text(saved)
        if not text:
            text=self.state.notes.get(sid,'')
        if not text:
            text='Notes appear here after recording. Generate summary can retry explicitly.'
        self.notes_text.get_buffer().set_text(text)

    def _refresh_transcript(self):
        sid=self.state.selected_id
        rows=sorted(self.state.segments.get(sid,[]),key=lambda row:(row.get('start_ms') or 0))
        turns=self.state.speaker_turns.get(sid,[])
        clear_box(self.transcript_list)
        for row in rows:
            self.transcript_list.add(self._transcript_row(row,turns))
        partials=self.state.partials.get(sid,{})
        if isinstance(partials,dict):
            for stream,text in partials.items():
                if text:
                    self.transcript_list.add(self._partial_row(stream,text))
        elif partials:
            self.transcript_list.add(self._partial_row('replay',partials))
        self.transcript_list.show_all()
        total=self.state.pages.get(sid,{}).get('total',len(rows))
        if rows or total:
            self.transcript_window.set_text(f'{self.state.page_offset+1}–{self.state.page_offset+len(rows)} of {total}')
        else:
            self.transcript_window.set_text('')
        self.earlier_button.set_sensitive(self.state.page_offset>0)
        self.later_button.set_sensitive(self.state.page_offset+500<total)

    def _transcript_row(self,row,turns):
        container=Gtk.ListBoxRow()
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=3)
        add_class(box,'transcript-row')
        stream='Microphone' if row.get('stream')=='mic' else 'Meeting audio'
        speaker=speaker_label(row,turns)
        meta=make_label(f'{timestamp(row.get("start_ms"))} · {stream} · {speaker}',
            classes=('speaker',),xalign=0)
        text=make_label(str(row.get('text') or ''),classes=('text',),wrap=True,selectable=True,xalign=0)
        box.pack_start(meta,False,False,0)
        box.pack_start(text,False,False,0)
        container.add(box)
        return container

    def _partial_row(self,stream,text):
        container=Gtk.ListBoxRow()
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=2)
        add_class(box,'partial-row')
        meta=make_label('Recognizing…',classes=('speaker',),xalign=0)
        body=make_label(str(text),classes=('text',),wrap=True,xalign=0)
        box.pack_start(meta,False,False,0)
        box.pack_start(body,False,False,0)
        container.add(box)
        return container

    def _refresh_audio(self):
        sid=self.state.selected_id
        audio=self.state.audio_states.get(sid,{})
        self.track_combo.remove_all()
        for track in audio.get('tracks') or []:
            self.track_combo.append_text('Microphone' if track=='mic' else 'Meeting audio')
        if audio.get('tracks'):
            self.track_combo.set_active(0)
        self.track_combo.set_sensitive(bool(audio.get('available') and not audio.get('archived')))
        self.minute_spin.set_sensitive(self.track_combo.get_sensitive())
        self.archive_button.set_label('Unarchive audio' if audio.get('archived') else 'Archive audio')
        if audio.get('available'):
            if audio.get('archived'):
                self.audio_status.set_text('Audio is archived. Unarchive to play it again.')
            else:
                self.audio_status.set_text('Audio is available for playback.')
        elif audio.get('expired_at'):
            self.audio_status.set_text('Audio has expired. Transcript and notes are preserved.')
        else:
            self.audio_status.set_text('Audio is unavailable.')
        self.play_button.set_sensitive(bool(audio.get('available') and not audio.get('archived')))
        self.archive_button.set_sensitive(bool(audio.get('available')))

    def _start_playback(self,event):
        if self.player is None or event.get('session_id')!=self.state.selected_id:
            return
        try:
            data=base64.b64decode(event.get('wav_base64',''),validate=True)
            directory,path=private_playback_file(data)
            self._cleanup_playback()
            self.audio_directory=directory
            self.audio_path=path
            self.audio_session=event.get('session_id')
            self.player.set_state(self.gst.State.NULL)
            self.player.set_property('uri',path.as_uri())
            self.player.set_state(self.gst.State.PLAYING)
            self.gst_error.set_text('')
        except Exception as exc:  # noqa: BLE001 - show transport class only
            self._cleanup_playback()
            self.gst_error.set_text(f'Playback could not start ({type(exc).__name__}).')

    def on_history_selected(self,listbox,row):
        if self.updating_history or row is None:
            return
        sid=getattr(row,'session_id',None)
        if sid and sid!=self.state.selected_id:
            self._cleanup_playback()
            self.state.select(sid)
            self.client.send(self.state.page_payload(sid,0))
            self.refresh()

    def on_earlier(self,*_args):
        sid=self.state.selected_id
        if sid:
            self.client.send(self.state.page_payload(sid,max(0,self.state.page_offset-500)))

    def on_later(self,*_args):
        sid=self.state.selected_id
        if sid:
            self.client.send(self.state.page_payload(sid,self.state.page_offset+500))

    def on_stop(self,*_args):
        if self.state.active_id:
            self.client.send({'action':'stop','session_id':self.state.active_id})

    def on_summarize(self,*_args):
        sid=self.state.selected_id
        revision=self.state.page_revision(sid)
        provider=self.state.summary_provider
        if sid and revision:
            self.client.send({'action':'summarize','session_id':sid,'revision':revision,
                'provider':provider.get('provider'),'model':provider.get('model'),
                'text_consent':self.state.agreement.get('accepted') is True})

    def on_export(self,*_args):
        sid=self.state.selected_id
        revision=self.state.page_revision(sid)
        if sid and revision:
            self.client.send({'action':'export_markdown','session_id':sid,'revision':revision})

    def _save_markdown(self,event):
        sid=event.get('session_id')
        if sid and sid!=self.state.selected_id:
            return
        filename=str(event.get('filename') or 'scribeloop-export.md')
        content=str(event.get('content') or '')
        dialog=Gtk.FileChooserDialog(title='Save Markdown export',transient_for=self.window,
            action=Gtk.FileChooserAction.SAVE)
        dialog.set_modal(True)
        dialog.set_do_overwrite_confirmation(True)
        dialog.set_current_name(filename)
        dialog.add_buttons('Cancel',Gtk.ResponseType.CANCEL,'Save',Gtk.ResponseType.ACCEPT)
        dialog.show()
        def response(dialog,response_id):
            path=None
            try:
                if response_id==Gtk.ResponseType.ACCEPT:
                    target=dialog.get_filename()
                    if target:
                        path=Path(target)
                        path.write_text(content,encoding='utf-8')
            except OSError as exc:
                self.status.set_text(f'Markdown export could not be saved ({type(exc).__name__}).')
            else:
                if path is not None:
                    self.status.set_text('Markdown export saved.')
            dialog.destroy()
        dialog.connect('response',response)

    def on_play_audio(self,*_args):
        sid=self.state.selected_id
        index=self.track_combo.get_active()
        tracks=self.state.audio_states.get(sid,{}).get('tracks') or []
        if sid and 0<=index<len(tracks):
            self.client.send({'action':'play_audio','session_id':sid,'stream':tracks[index],
                'start_ms':int(self.minute_spin.get_value()*60000)})

    def on_archive_audio(self,*_args):
        sid=self.state.selected_id
        audio=self.state.audio_states.get(sid,{})
        if sid and audio.get('available'):
            self.client.send({'action':'archive_audio','session_id':sid,
                'archived':audio.get('archived') is not True})

    def on_settings(self,*_args):
        self._settings_dialog()

    def _show_agreement(self):
        dialog=Gtk.Dialog(title='First-use agreement',transient_for=self.window,modal=True)
        dialog.set_default_size(620,460)
        add_class(dialog.get_content_area(),'dialog-panel')
        label=make_label('Use ScribeLoop only when you have permission to record the people captured. '
            'Recording is explicit. System audio captures everything sent to the selected output, '
            'including notifications, music, and other calls. Cloud transcription sends audio to '
            'your selected provider.',
            classes=('text',),wrap=True)
        dialog.get_content_area().pack_start(label,True,True,12)
        dialog.add_button('Not now',Gtk.ResponseType.CANCEL)
        agree=dialog.add_button('Agree and continue',Gtk.ResponseType.ACCEPT)
        add_class(agree,'accent-button')
        dialog.show_all()
        def response(dialog,response_id):
            if response_id==Gtk.ResponseType.ACCEPT and self.state.agreement.get('version'):
                self.client.send({'action':'accept_agreement',
                    'version':self.state.agreement.get('version')})
            dialog.destroy()
        dialog.connect('response',response)

    def on_new_recording(self,*_args):
        if self.state.agreement.get('accepted') is not True:
            self._show_agreement()
            return
        if self.state.is_busy():
            self.status.set_text('Stop the active recording or processing before starting another.')
            return
        if recording_provider_missing(self.state):
            self.status.set_text('Set up a Deepgram transcription key in Settings before recording. '
                'The desktop app does not load credentials from .env files.')
            self._settings_dialog()
            return
        self._recording_dialog()

    def _recording_dialog(self):
        dialog=Gtk.Dialog(title='New recording',transient_for=self.window,modal=True)
        dialog.set_default_size(590,460)
        add_class(dialog.get_content_area(),'dialog-panel')
        grid=Gtk.Grid(column_spacing=10,row_spacing=10)
        add_class(grid,'form-grid')
        grid.set_margin_top(10);grid.set_margin_bottom(10)
        grid.set_margin_start(10);grid.set_margin_end(10)
        source=Gtk.ComboBoxText()
        source.append_text('Microphone only')
        source.append_text('All output audio + microphone')
        source.set_active(0)
        microphone=Gtk.ComboBoxText()
        mics=self.state.discovery.get('microphones') or []
        if not mics:
            microphone.append_text('System default microphone')
            microphone.set_active(0)
        for mic in mics:
            microphone.append_text(str(mic.get('name') or mic.get('id') or 'Microphone'))
        microphone.set_active(0)
        duration=Gtk.ComboBoxText()
        for label in ('5 minutes','1 hour','3 hours'):
            duration.append_text(label)
        duration.set_active(0)
        language=Gtk.ComboBoxText()
        languages=self.state.transcription.get('languages') or {'auto':'Auto'}
        for code,name in languages.items():
            language.append(str(code),str(name))
        language.set_active(0)
        title=Gtk.Entry();title.set_text('Meeting conversation');title.set_max_length(160)
        consent=Gtk.CheckButton(label='Everyone captured has agreed.')
        status=make_label('',classes=('dialog-status',),wrap=True)
        scope_help=make_label('',classes=('dialog-help',),wrap=True)
        def update_scope_help(_source):
            scope_help.set_text('Captures all sound sent to the selected output, including '
                'notifications, music, and other calls. App-specific isolation is unavailable.'
                if source.get_active()==1 else 'Captures microphone input only.')
        source.connect('changed',update_scope_help)
        update_scope_help(source)
        grid.attach(Gtk.Label(label='Capture source'),0,0,1,1);grid.attach(source,1,0,1,1)
        grid.attach(Gtk.Label(label='Microphone'),0,1,1,1);grid.attach(microphone,1,1,1,1)
        grid.attach(Gtk.Label(label='Limit'),0,2,1,1);grid.attach(duration,1,2,1,1)
        grid.attach(Gtk.Label(label='Language'),0,3,1,1);grid.attach(language,1,3,1,1)
        grid.attach(Gtk.Label(label='Title'),0,4,1,1);grid.attach(title,1,4,1,1)
        grid.attach(consent,0,5,2,1)
        grid.attach(scope_help,0,6,2,1)
        grid.attach(status,0,7,2,1)
        dialog.get_content_area().pack_start(grid,True,True,8)
        dialog.add_button('Cancel',Gtk.ResponseType.CANCEL)
        record=dialog.add_button('Record & take notes',Gtk.ResponseType.ACCEPT)
        add_class(record,'accent-button')

        def response(dialog,response_id):
            if response_id!=Gtk.ResponseType.ACCEPT:
                dialog.destroy();return
            scope='microphone' if source.get_active()==0 else 'system'
            mics=self.state.discovery.get('microphones') or []
            mic_id=mics[microphone.get_active()].get('id') if mics else 'default'
            duration_s=(300,3600,10800)[max(0,duration.get_active())]
            language_code=language.get_active_id() or 'auto'
            if not consent.get_active():
                status.set_text('Confirm participant permission before recording.')
                return
            record_payload=self.state.record_payload(scope=scope,microphone_id=mic_id,
                title=title.get_text(),duration_s=duration_s,language=language_code,
                consent_confirmed=True)
            select={'action':'select_source','request_id':self.state.next_request_id(),
                'scope':scope,'app_id':None,'microphone_id':mic_id,'candidate_id':None}
            self.pending_record={'request_id':select['request_id'],'record':record_payload}
            self.client.send(select)
            dialog.destroy()
        dialog.connect('response',response)
        dialog.show_all()

    def _settings_dialog(self):
        dialog=Gtk.Dialog(title='Provider settings',transient_for=self.window,modal=True)
        dialog.set_default_size(620,480)
        add_class(dialog.get_content_area(),'dialog-panel')
        grid=Gtk.Grid(column_spacing=10,row_spacing=10)
        add_class(grid,'form-grid')
        grid.set_margin_top(10);grid.set_margin_bottom(10)
        grid.set_margin_start(10);grid.set_margin_end(10)
        settings=self.state.settings or {}
        presets=settings.get('summary_presets') or {}
        detection=Gtk.CheckButton(label='Suggest likely meetings from audio activity')
        detection.set_active(settings.get('detection_enabled') is True)
        provider=Gtk.ComboBoxText()
        for name in (*presets.keys(),'Custom'):
            provider.append(name,name)
        current_provider=str(settings.get('summary_provider') or 'OpenAI')
        if current_provider not in presets and current_provider!='Custom':
            current_provider='Custom'
        provider.set_active_id(current_provider)
        endpoint=Gtk.Entry();endpoint.set_text(str(settings.get('summary_endpoint') or ''));endpoint.set_max_length(500)
        endpoint.set_editable(current_provider=='Custom')
        model=Gtk.Entry();model.set_text(str(settings.get('summary_model') or ''));model.set_max_length(100)
        summary_key=Gtk.Entry();summary_key.set_visibility(False);summary_key.set_max_length(2048)
        deepgram_key=Gtk.Entry();deepgram_key.set_visibility(False);deepgram_key.set_max_length(512)
        key_previews=settings.get('summary_key_previews') or {}
        def update_summary_key_placeholder(provider_name):
            saved=key_previews.get(provider_name) or (
                settings.get('summary_key_preview') if provider_name==settings.get('summary_provider') else '')
            summary_key.set_placeholder_text('Saved key · leave blank to keep' if saved
                                             else 'Enter notes API key')
        update_summary_key_placeholder(current_provider)
        deepgram_key.set_placeholder_text('Saved key · leave blank to keep' if settings.get('deepgram_configured')
                          else 'Enter Deepgram API key')
        def provider_changed(_provider):
            selected=provider.get_active_id() or 'Custom'
            endpoint.set_text(provider_endpoint(selected,presets,endpoint.get_text()))
            endpoint.set_editable(selected=='Custom')
            update_summary_key_placeholder(selected)
        provider.connect('changed',provider_changed)
        grid.attach(Gtk.Label(label='Notes provider'),0,0,1,1);grid.attach(provider,1,0,1,1)
        grid.attach(Gtk.Label(label='Notes API URL'),0,1,1,1);grid.attach(endpoint,1,1,1,1)
        grid.attach(Gtk.Label(label='Notes model'),0,2,1,1);grid.attach(model,1,2,1,1)
        grid.attach(Gtk.Label(label='Notes API key'),0,3,1,1);grid.attach(summary_key,1,3,1,1)
        grid.attach(Gtk.Label(label='Deepgram API key'),0,4,1,1);grid.attach(deepgram_key,1,4,1,1)
        grid.attach(detection,0,5,2,1)
        explanation=make_label('When enabled, ScribeLoop checks audio-stream metadata for known meeting apps. It never records until you choose Record.',
            classes=('dialog-help',),wrap=True)
        grid.attach(explanation,0,6,2,1)
        save_status=make_label('',classes=('dialog-status',),wrap=True)
        grid.attach(save_status,0,7,2,1)
        dialog.get_content_area().pack_start(grid,True,True,8)
        dialog.add_button('Cancel',Gtk.ResponseType.CANCEL)
        save=dialog.add_button('Save settings',Gtk.ResponseType.ACCEPT)
        add_class(save,'accent-button')
        self.settings_dialog=dialog
        self.settings_save_status=save_status
        def response(dialog,response_id):
            if response_id!=Gtk.ResponseType.ACCEPT:
                if self.settings_dialog is dialog:
                    self.settings_dialog=None
                    self.settings_save_status=None
                dialog.destroy();return
            payload={'summary_provider':provider.get_active_id() or 'Custom',
                'summary_endpoint':endpoint.get_text(),'summary_model':model.get_text(),
                'detection_enabled':detection.get_active()}
            if summary_key.get_text():
                payload['summary_key']=summary_key.get_text()
            if deepgram_key.get_text():
                payload['deepgram_key']=deepgram_key.get_text()
            self.settings_save_status.set_text('Saving settings…')
            self.settings_save_pending=True
            if self.client.send({'action':'save_settings','settings':payload}) is not True:
                self.settings_save_pending=False
                self.settings_save_status.set_text('Not connected. Reconnect before saving settings.')
        dialog.connect('response',response)
        dialog.show_all()


def run_native(port,token,child):
    if Gtk is None or Gdk is None or GLib is None:
        raise RuntimeError('GTK 3 runtime is required for the Ubuntu shell')
    shell=NativeShell(port,token,child)
    shell.refresh()
    Gtk.main()
    return shell

