"""Native Linux shell for the macOS-style notebook layout."""
from __future__ import annotations

import base64
import json
import tempfile
import threading
import time
from contextlib import suppress
from pathlib import Path

import gi

gi.require_version('Gtk','3.0')
from gi.repository import Gdk, GLib, Gtk
from websockets.sync.client import connect

from .linux_state import (
    LinuxState,
    session_label,
    speaker_label,
    summary_text,
    timestamp,
)

CSS = b"""
window { background:#f7f4ee; }
.header { background:#efe9dd; border-bottom:1px solid #ddd5c4; }
.brand { font-size:20px; font-weight:700; color:#26332a; }
.subtitle { font-size:10px; font-weight:700; color:#6d7a70; }
.sidebar { background:#f2eee6; border-right:1px solid #ddd5c4; }
.section-label { font-size:10px; font-weight:700; color:#6d7a70; }
.card { background:white; border-radius:10px; border:1px solid #e3ded2; padding:12px; }
.accent-button { background:#2f6f4f; color:white; font-weight:700; }
.danger-button { color:#a33b31; }
.status-label { color:#6d7a70; font-size:11px; }
.transcript-row { background:white; border-radius:10px; border:1px solid #e3ded2; padding:8px; }
.partial-row { background:#f6f2e8; border-radius:10px; border:1px dashed #d9d1bd; padding:8px; }
.speaker { font-size:11px; font-weight:700; color:#2f6f4f; }
.text { font-size:13px; color:#26332a; }
"""


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
        self.audio_session=None
        self.updating_history=False
        self.agreement_shown=False
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
        main.set_margin_top(12);main.set_margin_bottom(12)
        main.set_margin_start(16);main.set_margin_end(16)
        root.pack_start(main,True,True,0)

        self.detail_title=make_label('Ready when you are',classes=('brand',))
        self.detail_status=make_label('Connecting…',classes=('status-label',))
        self.stack=Gtk.Stack()
        self.stack.set_hexpand(True);self.stack.set_vexpand(True)
        switcher=Gtk.StackSwitcher();switcher.set_stack(self.stack)
        switcher.set_halign(Gtk.Align.CENTER)
        main.pack_start(self.detail_title,False,False,0)
        main.pack_start(self.detail_status,False,False,0)
        main.pack_start(switcher,False,False,0)
        main.pack_start(self.stack,True,True,0)
        self.stack.add_titled(self._notes_view(),'notes','Notes')
        self.stack.add_titled(self._transcript_view(),'transcript','Transcript')
        self.stack.add_titled(self._audio_view(),'audio','Audio')
        self.stack.set_visible_child_name('notes')

        self.status=make_label('Connecting to the local backend…',classes=('status-label',))
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
        sidebar.set_size_request(300,-1)
        sidebar.set_margin_top(12);sidebar.set_margin_bottom(12)
        sidebar.set_margin_start(12);sidebar.set_margin_end(12)
        add_class(sidebar,'sidebar')
        label=make_label('CONVERSATIONS',classes=('section-label',))
        label.set_halign(Gtk.Align.START)
        self.history_count=make_label('0',classes=('section-label',))
        count_box=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        count_box.pack_start(label,False,False,0)
        count_box.pack_end(self.history_count,False,False,0)
        self.history=Gtk.ListBox()
        self.history.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.history.connect('row-selected',self.on_history_selected)
        scrolled=Gtk.ScrolledWindow()
        scrolled.set_vexpand(True);scrolled.set_hexpand(True)
        scrolled.set_policy(Gtk.PolicyType.NEVER,Gtk.PolicyType.AUTOMATIC)
        scrolled.add(self.history)
        sidebar.pack_start(count_box,False,False,0)
        sidebar.pack_start(scrolled,True,True,0)
        return sidebar

    def _notes_view(self):
        view=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=10)
        add_class(view,'card')
        self.notes_text=Gtk.TextView()
        self.notes_text.set_editable(False);self.notes_text.set_cursor_visible(False)
        self.notes_text.set_wrap_mode(Gtk.WrapMode.WORD)
        self.notes_text.set_left_margin(4);self.notes_text.set_right_margin(4)
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
        view=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8)
        self.transcript_list=Gtk.ListBox()
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
        pager.pack_start(self.earlier_button,False,False,0)
        pager.pack_start(self.transcript_window,False,False,0)
        pager.pack_end(self.later_button,False,False,0)
        view.pack_start(pager,False,False,0)
        view.pack_start(transcript_scroll,True,True,0)
        return view

    def _audio_view(self):
        view=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=10)
        add_class(view,'card')
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
        except Exception as exc:  # noqa: BLE001 - show transport class, not system paths
            self.player=None
            self.gst_error.set_text(f'Audio playback unavailable ({type(exc).__name__}).')

    def on_destroy(self,*_args):
        if self.player is not None:
            with suppress(Exception):
                self.player.set_state(self.gst.State.NULL)
        if self.child.poll() is None:
            try:
                self.child.stdin.write('STOP\n');self.child.stdin.flush()
            except Exception:  # noqa: BLE001,S110 - shutdown is best-effort
                pass
        Gtk.main_quit()

    def on_backend_event(self,event):
        kind=event.get('type')
        if kind in {'snapshot','conversation_page','session','segment','partial','transcript_replaced',
                    'speakers_corrected','people_changed','agreement','settings','discovery',
                    'audio_state','cloud_usage','speaker_turns','language_locked','rolling_notes',
                    'summary_started','summary_ready','summary_error','session_deleted'}:
            self.state.apply(event)
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
        selected=self.state.selected_id
        self.updating_history=True
        clear_box(self.history)
        for session in rows:
            sid=session['id']
            row=Gtk.ListBoxRow()
            row.set_data('session_id',sid)
            box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=2)
            box.set_margin_top(6);box.set_margin_bottom(6)
            box.set_margin_start(8);box.set_margin_end(8)
            title=make_label(str(session.get('title') or 'Untitled conversation'),
                classes=('text',),wrap=True,xalign=0)
            status=make_label(str(session.get('status') or '').replace('_',' ').title(),
                classes=('status-label',),xalign=0)
            box.pack_start(title,False,False,0)
            box.pack_start(status,False,False,0)
            row.add(box)
            self.history.add(row)
            if sid==selected:
                self.history.select_row(row)
        self.history.show_all()
        self.updating_history=False

    def _refresh_detail(self):
        session=self.state.selected_session()
        if session:
            self.detail_title.set_text(str(session.get('title') or 'Untitled conversation'))
            self.detail_status.set_text(session_label(session))
        else:
            self.detail_title.set_text('Ready when you are')
            self.detail_status.set_text('No conversation selected')

    def _refresh_actions(self):
        busy=self.state.is_busy()
        accepted=self.state.agreement.get('accepted') is True
        configured=self.state.transcription.get('configured') is True
        summary_ready=self.state.summary_provider.get('configured') is True
        self.new_button.set_sensitive(accepted and configured and not busy)
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
            path=Path(tempfile.gettempdir())/'scribeloop-native-playback.wav'
            path.write_bytes(data)
            self.audio_path=path
            self.player.set_state(self.gst.State.NULL)
            self.player.set_property('uri',path.as_uri())
            self.player.set_state(self.gst.State.PLAYING)
            self.gst_error.set_text('')
        except Exception as exc:  # noqa: BLE001 - show transport class only
            self.gst_error.set_text(f'Playback could not start ({type(exc).__name__}).')

    def on_history_selected(self,listbox,row):
        if self.updating_history or row is None:
            return
        sid=row.get_data('session_id')
        if sid and sid!=self.state.selected_id:
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
        add_class(dialog.get_content_area(),'card')
        label=make_label('Use ScribeLoop only when you have permission to record the people captured. '
            'Recording is explicit. Cloud transcription sends audio to your selected provider.',
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
        self._recording_dialog()

    def _recording_dialog(self):
        dialog=Gtk.Dialog(title='New recording',transient_for=self.window,modal=True)
        dialog.set_default_size(560,420)
        add_class(dialog.get_content_area(),'card')
        grid=Gtk.Grid(column_spacing=10,row_spacing=10)
        grid.set_margin_top(10);grid.set_margin_bottom(10)
        grid.set_margin_start(10);grid.set_margin_end(10)
        source=Gtk.ComboBoxText()
        source.append_text('Microphone only')
        source.append_text('All system audio + microphone')
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
        status=make_label('',classes=('status-label',))
        grid.attach(Gtk.Label(label='Capture source'),0,0,1,1);grid.attach(source,1,0,1,1)
        grid.attach(Gtk.Label(label='Microphone'),0,1,1,1);grid.attach(microphone,1,1,1,1)
        grid.attach(Gtk.Label(label='Limit'),0,2,1,1);grid.attach(duration,1,2,1,1)
        grid.attach(Gtk.Label(label='Language'),0,3,1,1);grid.attach(language,1,3,1,1)
        grid.attach(Gtk.Label(label='Title'),0,4,1,1);grid.attach(title,1,4,1,1)
        grid.attach(consent,0,5,2,1)
        grid.attach(status,0,6,2,1)
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

    def _settings_dialog(self):
        dialog=Gtk.Dialog(title='Provider settings',transient_for=self.window,modal=True)
        dialog.set_default_size(560,380)
        add_class(dialog.get_content_area(),'card')
        grid=Gtk.Grid(column_spacing=10,row_spacing=10)
        grid.set_margin_top(10);grid.set_margin_bottom(10)
        grid.set_margin_start(10);grid.set_margin_end(10)
        settings=self.state.settings or {}
        provider=Gtk.Entry();provider.set_text(str(settings.get('summary_provider') or 'OpenAI'));provider.set_max_length(80)
        endpoint=Gtk.Entry();endpoint.set_text(str(settings.get('summary_endpoint') or ''));endpoint.set_max_length(500)
        model=Gtk.Entry();model.set_text(str(settings.get('summary_model') or ''));model.set_max_length(100)
        summary_key=Gtk.Entry();summary_key.set_visibility(False);summary_key.set_max_length(2048)
        deepgram_key=Gtk.Entry();deepgram_key.set_visibility(False);deepgram_key.set_max_length(512)
        grid.attach(Gtk.Label(label='Notes provider'),0,0,1,1);grid.attach(provider,1,0,1,1)
        grid.attach(Gtk.Label(label='Notes API URL'),0,1,1,1);grid.attach(endpoint,1,1,1,1)
        grid.attach(Gtk.Label(label='Notes model'),0,2,1,1);grid.attach(model,1,2,1,1)
        grid.attach(Gtk.Label(label='Notes API key'),0,3,1,1);grid.attach(summary_key,1,3,1,1)
        grid.attach(Gtk.Label(label='Deepgram API key'),0,4,1,1);grid.attach(deepgram_key,1,4,1,1)
        dialog.get_content_area().pack_start(grid,True,True,8)
        dialog.add_button('Cancel',Gtk.ResponseType.CANCEL)
        save=dialog.add_button('Save settings',Gtk.ResponseType.ACCEPT)
        add_class(save,'accent-button')
        def response(dialog,response_id):
            if response_id!=Gtk.ResponseType.ACCEPT:
                dialog.destroy();return
            payload={'summary_provider':provider.get_text(),
                'summary_endpoint':endpoint.get_text(),'summary_model':model.get_text()}
            if summary_key.get_text():
                payload['summary_key']=summary_key.get_text()
            if deepgram_key.get_text():
                payload['deepgram_key']=deepgram_key.get_text()
            self.client.send({'action':'save_settings','settings':payload})
            dialog.destroy()
        dialog.connect('response',response)
        dialog.show_all()


def run_native(port,token,child):
    shell=NativeShell(port,token,child)
    shell.refresh()
    Gtk.main()
    return shell

