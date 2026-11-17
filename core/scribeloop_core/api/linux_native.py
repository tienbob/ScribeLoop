"""Native Linux shell for the macOS-style notebook layout."""
from __future__ import annotations

import base64
import json
import os
import threading
import time
from contextlib import suppress
from pathlib import Path

try:
    import gi

    gi.require_version('Gtk','3.0')
    gi.require_version('Gdk','3.0')
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
* { font-family:"Lato"; color:#283831; }
window, .shell-root { background:#f2f4f1; }
.header { background:#fbfcfa; border-bottom:1px solid #dce3dd; padding:2px 8px; }
.brand { font-size:16px; font-weight:800; color:#20382c; }
.subtitle { font-size:9px; font-weight:800; letter-spacing:1.3px; color:#76847b; }
.sidebar { background:#fbfcfa; border-right:1px solid #dce3dd; }
.section-heading { font-size:15px; font-weight:800; color:#26382f; }
.count-pill { background:#edf1ed; border-radius:10px; padding:3px 8px; font-size:11px; font-weight:800; color:#66766d; }
.history-list { background:transparent; }
.history-list row { background:transparent; border-radius:5px; border-left:3px solid transparent; margin:2px 0; }
.history-list row:hover { background:#f0f4f0; }
.history-list row:selected { background:#e9f1eb; border-left-color:#bb654a; }
.history-row { padding:8px 10px; }
.history-title { font-size:13px; font-weight:700; color:#293a32; }
.history-meta { font-size:11px; color:#78857d; }
.workspace { background:#f2f4f1; }
.eyebrow { font-size:10px; font-weight:800; letter-spacing:1.5px; color:#b15f47; }
.page-title { font-size:27px; font-weight:800; color:#20332a; }
.detail-status { font-size:12px; color:#718077; }
.view-tabs { background:#e7ece7; border-radius:7px; padding:3px; }
.view-tabs button { color:#617168; background:transparent; border:0; border-radius:5px; padding:7px 16px; font-weight:700; }
.view-tabs button:hover { color:#273a30; background:#f6f8f6; }
.view-tabs button:checked { color:#244b37; background:#fff; box-shadow:0 1px 3px rgba(27,48,36,.12); }
button { color:#33433a; background:#fff; border:1px solid #d5ded6; border-radius:5px; padding:6px 11px; min-height:34px; font-weight:700; }
button:hover { background:#f2f6f2; border-color:#b9c9bd; }
button:active { background:#e6ede7; }
entry, spinbutton { background:#fff; color:#2b3b33; border:1px solid #d5ded6; border-radius:4px; padding:7px; }
entry:focus, spinbutton:focus { border-color:#6e9a7d; }
checkbutton { color:#43534a; }
menu { background:#fbfcfa; color:#2b3b33; border:1px solid #d5ded6; border-radius:4px; padding:4px; }
menu menuitem { background:#fbfcfa; color:#2b3b33; padding:7px 10px; }
menu menuitem:hover, menu menuitem:selected { background:#e9f1eb; color:#20382c; }
.accent-button { background:#285b42; border-color:#285b42; color:#fff; }
.accent-button label, .accent-button image { color:#fff; }
.accent-button:hover { background:#204c37; border-color:#204c37; color:#fff; }
.accent-button:hover label, .accent-button:hover image { color:#fff; }
.danger-button { background:#fbf5f2; border-color:#ead6ce; color:#a04e3d; }
.danger-button label, .danger-button image { color:#a04e3d; }
.danger-button:hover { background:#f6e8e2; border-color:#dfb7a9; }
.status-label { color:#728078; font-size:11px; }
.connection-status { color:#65756b; font-size:11px; }
.notes-view, .transcript-view, .audio-view { background:transparent; }
textview.notes-editor, textview.notes-editor text { background-color:#fff; color:#2b3c33; }
.notes-editor { border:1px solid #e0e6e0; border-radius:5px; padding:24px; font-size:14px; }
.action-bar { padding-top:6px; }
.transcript-list { background:#fff; border:1px solid #e0e6e0; border-radius:5px; }
.transcript-item { background:#fff; border-bottom:1px solid #edf0ed; }
.transcript-row { background:transparent; padding:12px 14px; }
.partial-row { background:#f7f0e5; border-left:3px solid #c79551; padding:12px 14px; }
.speaker { font-size:11px; font-weight:800; color:#b45f46; }
.text { font-size:14px; color:#2a3a32; }
.pager { padding:7px 2px; }
.audio-status { color:#718077; font-size:12px; }
.audio-toolbar { background:#fff; border:1px solid #e0e6e0; border-radius:5px; padding:12px; }
.field-label { color:#718077; font-size:11px; font-weight:700; }
.dialog-panel { background:#fbfcfa; padding:12px; }
.dialog-panel label { color:#53645a; font-size:12px; }
.dialog-copy { color:#394a40; font-size:13px; }
.markdown-export { background:#f2f4f1; color:#283831; }
.markdown-export filechooser { background:#fbfcfa; color:#283831; }
.markdown-export placessidebar { background:#f2f4f1; color:#43534a; }
.markdown-export placessidebar row { color:#43534a; }
.markdown-export placessidebar row:selected { background:#e9f1eb; color:#20382c; }
.markdown-export treeview.view { background:#fbfcfa; color:#283831; }
.markdown-export treeview.view:selected { background:#dfece2; color:#20382c; }
.markdown-export treeview.view:selected:focus { background:#d6e7da; color:#20382c; }
.markdown-export treeview.view header button { background:#fff; color:#43534a; }
.markdown-export label { color:#43534a; }
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


def markdown_filename(title):
    safe=''.join(character if character.isalnum() or character in ' ._-' else ' '
                 for character in str(title or ''))
    safe=' '.join(safe.split()).strip(' .')[:100].rstrip(' .')
    return f'{safe or "ScribeLoop notes"}.md'


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
        self.audio_tracks_key=None
        self.audio_pending=None
        self.audio_durations={}
        self.audio_start=0
        self.audio_playing=False
        self.audio_paused=False
        self.audio_seek_timer=None
        self.audio_seek_resume=False
        self.updating_history=False
        self._history_rows={}
        self._history_positions={}
        self.agreement_shown=False
        self.offered_calls=set()
        self.call_dialog=None
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
        self._icon_button(self.new_button,'document-new-symbolic','Start a new recording')
        self._icon_button(self.stop_button,'media-playback-stop-symbolic','Stop the active recording')
        self._icon_button(self.prefs_button,'preferences-system-symbolic','Open settings')
        self.window.set_titlebar(header)

        root=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=0)
        add_class(root,'shell-root')
        self.window.add(root)
        root.pack_start(self._sidebar(),False,False,0)
        main=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=12)
        add_class(main,'workspace')
        main.set_margin_top(22);main.set_margin_bottom(18)
        main.set_margin_start(24);main.set_margin_end(24)
        root.pack_start(main,True,True,0)

        self.page_kicker=make_label('MEETING NOTEBOOK',classes=('eyebrow',))
        self.detail_title=make_label('Ready when you are',classes=('page-title',))
        self.detail_status=make_label('Connecting…',classes=('detail-status',))
        self.stack=Gtk.Stack()
        self.stack.set_hexpand(True);self.stack.set_vexpand(True)
        switcher=Gtk.StackSwitcher();switcher.set_stack(self.stack)
        add_class(switcher,'view-tabs')
        switcher.set_halign(Gtk.Align.CENTER)
        main.pack_start(self.page_kicker,False,False,0)
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

        self._load_css()
        self.window.show_all()
        self.refresh()
        self.client.start()

    def _header_title(self):
        box=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=1)
        title=make_label('ScribeLoop',classes=('brand',))
        subtitle=make_label('LINUX NATIVE NOTEBOOK',classes=('subtitle',))
        box.pack_start(title,False,False,0)
        box.pack_start(subtitle,False,False,0)
        return box

    @staticmethod
    def _icon_button(button,icon_name,tooltip):
        button.set_image(Gtk.Image.new_from_icon_name(icon_name,Gtk.IconSize.BUTTON))
        button.set_always_show_image(True)
        button.set_tooltip_text(tooltip)

    def _sidebar(self):
        sidebar=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=12)
        sidebar.set_size_request(286,-1)
        sidebar.set_margin_top(20);sidebar.set_margin_bottom(14)
        sidebar.set_margin_start(14);sidebar.set_margin_end(12)
        add_class(sidebar,'sidebar')
        label=make_label('Conversations',classes=('section-heading',))
        label.set_halign(Gtk.Align.START)
        self.history_count=make_label('0',classes=('count-pill',))
        count_box=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        count_box.pack_start(label,False,False,0)
        count_box.pack_end(self.history_count,False,False,0)
        self.history=Gtk.ListBox()
        add_class(self.history,'history-list')
        self.history.set_selection_mode(Gtk.SelectionMode.SINGLE)
        self.history.set_sort_func(self._sort_history_rows)
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
        add_class(view,'notes-view')
        self.notes_text=Gtk.TextView()
        add_class(self.notes_text,'notes-editor')
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
        self._icon_button(self.generate_button,'document-edit-symbolic','Generate notes')
        self._icon_button(self.export_button,'document-save-symbolic','Export notes as Markdown')
        actions=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=8)
        add_class(actions,'action-bar')
        actions.set_halign(Gtk.Align.END)
        actions.pack_start(self.generate_button,False,False,0)
        actions.pack_start(self.export_button,False,False,0)
        view.pack_start(notes_scroll,True,True,0)
        view.pack_start(actions,False,False,0)
        return view

    def _transcript_view(self):
        view=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=8)
        add_class(view,'transcript-view')
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
        self._icon_button(self.earlier_button,'go-previous-symbolic','Show earlier transcript')
        self._icon_button(self.later_button,'go-next-symbolic','Show later transcript')
        self.transcript_window=make_label('',classes=('status-label',))
        pager=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=8)
        add_class(pager,'pager')
        pager.pack_start(self.earlier_button,False,False,0)
        pager.pack_start(self.transcript_window,False,False,0)
        pager.pack_end(self.later_button,False,False,0)
        view.pack_start(pager,False,False,0)
        view.pack_start(transcript_scroll,True,True,0)
        return view

    def _audio_view(self):
        view=Gtk.Box(orientation=Gtk.Orientation.VERTICAL,spacing=10)
        add_class(view,'audio-view')
        self.audio_status=make_label('Audio is unavailable.',classes=('audio-status',))
        self.audio_status.set_halign(Gtk.Align.START)
        self.audio_status.set_line_wrap(True)
        self.track_combo=Gtk.ComboBoxText()
        self.track_combo.connect('changed',self.on_audio_track_changed)
        self.play_button=Gtk.Button(label='Play')
        self.play_button.connect('clicked',self.on_play_audio)
        self.audio_stop_button=Gtk.Button(label='Stop')
        self.audio_stop_button.connect('clicked',self.on_stop_audio)
        self.archive_button=Gtk.Button(label='Archive audio')
        self.archive_button.connect('clicked',self.on_archive_audio)
        self._icon_button(self.play_button,'media-playback-start-symbolic','Play selected audio')
        self._icon_button(self.audio_stop_button,'media-playback-stop-symbolic','Stop playback')
        self._icon_button(self.archive_button,'folder-symbolic','Archive or restore audio')
        row=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=8)
        add_class(row,'audio-toolbar')
        row.pack_start(Gtk.Label(label='Track'),False,False,0)
        row.pack_start(self.track_combo,False,False,0)
        row.pack_start(self.play_button,False,False,0)
        row.pack_start(self.audio_stop_button,False,False,0)
        row.pack_end(self.archive_button,False,False,0)
        progress=Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL,spacing=8)
        self.audio_position=make_label('0:00 / 0:00',classes=('status-label',))
        self.audio_scale=Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL,0,1,1)
        self.audio_scale.set_draw_value(False)
        self.audio_scale.set_hexpand(True)
        self.audio_scale.connect('change-value',self.on_audio_seek)
        progress.pack_start(self.audio_scale,True,True,0)
        progress.pack_start(self.audio_position,False,False,0)
        self.gst_error=make_label('',classes=('status-label',))
        view.pack_start(self.audio_status,False,False,0)
        view.pack_start(row,False,False,0)
        view.pack_start(progress,False,False,0)
        view.pack_start(self.gst_error,False,False,0)
        self._init_playback()
        self.audio_timer=GLib.timeout_add(500,self._update_audio_position)
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
                bus=self.player.get_bus()
                bus.add_signal_watch()
                bus.connect('message',self._on_playback_message)
        except Exception as exc:  # noqa: BLE001 - show transport class, not system paths
            self.player=None
            self.gst_error.set_text(f'Audio playback unavailable ({type(exc).__name__}).')

    def _stop_playback(self):
        if self.player is not None:
            self.player.set_state(self.gst.State.NULL)
        if self.audio_path is not None:
            self.audio_path.close()
            self.audio_path=None

    def _on_playback_message(self,_bus,message):
        if message.type==self.gst.MessageType.ERROR:
            self._reset_audio()
            self.gst_error.set_text('Playback failed. Check your audio output and GStreamer installation.')
        elif message.type==self.gst.MessageType.EOS:
            self._stop_playback()
            next_start=self.audio_start+60000
            duration=self._audio_duration()
            if self.audio_playing and next_start<duration:
                self._request_audio_window(next_start)
            else:
                self.audio_playing=False
                self.audio_paused=False
                self._set_audio_play_button(False)
                self.audio_scale.set_value(duration/1000)
                self._update_audio_position()
                self.gst_error.set_text('Playback finished.')

    def on_destroy(self,*_args):
        GLib.source_remove(self.audio_timer)
        if self.player is not None:
            with suppress(Exception):
                self._reset_audio()
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
        if kind in {'audio_info','audio_window'} and event.get('request_id')==self.audio_pending \
                and event.get('session_id')==self.state.selected_id:
            self.audio_pending=None
            if kind=='audio_info':
                self.audio_durations=event.get('track_durations') or {}
                self._set_audio_duration()
                self._request_audio_window(int(self.audio_scale.get_value()*1000))
            else:
                self.audio_start=event['start_ms']
                self._start_playback(event)
        if kind=='error' and event.get('request_id')==self.audio_pending and self.audio_pending is not None:
            self._reset_audio()
            self.gst_error.set_text(event.get('message') or 'Audio could not be loaded.')
        if kind=='connection_lost' and (self.audio_playing or self.audio_paused
            or self.audio_pending is not None or self.audio_seek_timer is not None):
            self._reset_audio()
            self.gst_error.set_text('Playback stopped. Reconnect to load audio.')
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
        self._offer_call()

    def _offer_call(self):
        if self.call_dialog is not None or self.pending_record is not None:
            return
        candidate=self.state.call_candidate(self.offered_calls)
        if not candidate:
            return
        self.offered_calls.add(candidate['id'])
        dialog=Gtk.MessageDialog(transient_for=self.window,modal=True,
            message_type=Gtk.MessageType.QUESTION,buttons=Gtk.ButtonsType.NONE,
            text='Possible call detected')
        dialog.format_secondary_text(
            f"Take notes for {candidate.get('name', 'this call')}? "
            'Recording uses your microphone and all system audio, and requires permission.')
        dialog.add_button('Not now',Gtk.ResponseType.CANCEL)
        dialog.add_button('Review recording',Gtk.ResponseType.ACCEPT)
        dialog.set_keep_above(True)
        self.call_dialog=dialog
        def response(dialog,response_id):
            self.call_dialog=None
            self.client.send({'action':'dismiss_candidate','candidate_id':candidate['id']})
            dialog.destroy()
            if response_id==Gtk.ResponseType.ACCEPT:
                self._recording_dialog(call_name=candidate.get('name'))
        dialog.connect('response',response)
        dialog.show_all()
        dialog.present()

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
                    add_class(row,'history-item')
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
        add_class(container,'transcript-item')
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
        add_class(container,'transcript-item')
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
        tracks=tuple(audio.get('tracks') or [])
        key=(sid,tracks)
        if key!=self.audio_tracks_key:
            self._reset_audio()
            self.audio_session=sid
            self.audio_durations={}
            self.audio_tracks_key=key
            self.track_combo.remove_all()
            if tracks:
                self.track_combo.append('conversation','Conversation')
                for track in tracks:
                    self.track_combo.append(track,'Microphone' if track=='mic' else 'Meeting audio')
                self.track_combo.set_active_id('conversation')
            self._set_audio_duration()
        available=bool(audio.get('available') and not audio.get('archived'))
        if not available and (self.audio_playing or self.audio_paused or self.audio_pending is not None):
            self._reset_audio()
        self.track_combo.set_sensitive(available)
        self.audio_scale.set_sensitive(available and bool(self.audio_durations))
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
        self.play_button.set_sensitive(available and self.player is not None)
        self.audio_stop_button.set_sensitive(available and self.player is not None)
        self.archive_button.set_sensitive(bool(audio.get('available')))

    def _start_playback(self,event):
        if self.player is None or event.get('session_id')!=self.state.selected_id:
            return
        try:
            data=base64.b64decode(event.get('wav_base64',''),validate=True)
            self._stop_playback()
            self.audio_path=os.fdopen(os.memfd_create('scribeloop-playback',os.MFD_CLOEXEC),'w+b')
            self.audio_path.write(data)
            self.audio_path.flush()
            self.player.set_property('uri',f'file:///proc/self/fd/{self.audio_path.fileno()}')
            if self.player.set_state(self.gst.State.PLAYING)==self.gst.StateChangeReturn.FAILURE:
                raise RuntimeError('GStreamer could not start')
            self.audio_playing=True
            self.audio_paused=False
            self._set_audio_play_button(True)
            self.gst_error.set_text('Playing audio.')
        except Exception as exc:  # noqa: BLE001 - show transport class only
            self._reset_audio()
            self.gst_error.set_text(f'Playback could not start ({type(exc).__name__}).')

    def on_history_selected(self,listbox,row):
        if self.updating_history or row is None:
            return
        sid=row.session_id
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
        session=self.state.sessions.get(sid) or {}
        filename=markdown_filename(session.get('title'))
        content=str(event.get('content') or '')
        dialog=Gtk.FileChooserDialog(title='Save notes as Markdown',transient_for=self.window,
            action=Gtk.FileChooserAction.SAVE)
        add_class(dialog,'markdown-export')
        dialog.set_modal(True)
        dialog.set_default_size(980,700)
        dialog.set_do_overwrite_confirmation(True)
        download_dir=GLib.get_user_special_dir(GLib.UserDirectory.DIRECTORY_DOWNLOAD)
        if not download_dir or not Path(download_dir).is_dir():
            download_dir=str(Path.home())
        dialog.set_current_folder(download_dir)
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
        if not sid or self.player is None:
            return
        if self.audio_seek_timer is not None:
            self._reset_audio()
        if self.audio_playing:
            if self.audio_pending is not None:
                self._reset_audio()
                self.gst_error.set_text('Playback stopped.')
                return
            self.player.set_state(self.gst.State.PAUSED)
            self.audio_playing=False
            self.audio_paused=True
            self._set_audio_play_button(False)
            self.gst_error.set_text('Paused.')
        elif self.audio_paused:
            if self.player.set_state(self.gst.State.PLAYING)==self.gst.StateChangeReturn.FAILURE:
                self._reset_audio()
                self.gst_error.set_text('Audio output is unavailable.')
                return
            self.audio_playing=True
            self.audio_paused=False
            self._set_audio_play_button(True)
            self.gst_error.set_text('Playing audio.')
        elif self.audio_pending is None:
            self.audio_playing=True
            self._set_audio_play_button(True)
            if self.audio_durations:
                position=int(self.audio_scale.get_value()*1000)
                self._request_audio_window(0 if position>=self._audio_duration() else position)
            else:
                self._request_audio({'action':'audio_info','session_id':sid})

    def _request_audio(self,command):
        self.audio_pending=self.state.next_request_id()
        command['request_id']=self.audio_pending
        self.gst_error.set_text('Loading audio...')
        if not self.client.send(command):
            self._reset_audio()
            self.gst_error.set_text('Reconnect to load audio.')

    def _request_audio_window(self,start_ms):
        self._request_audio({'action':'audio_window','session_id':self.state.selected_id,
            'stream':self.track_combo.get_active_id(),'start_ms':start_ms})

    def _audio_duration(self):
        track=self.track_combo.get_active_id()
        return max(self.audio_durations.values(),default=0) if track=='conversation' \
            else self.audio_durations.get(track,0)

    def _set_audio_duration(self):
        duration=self._audio_duration()
        self.audio_scale.set_range(0,max(1,duration/1000))
        self.audio_scale.set_value(0)
        self.audio_scale.set_sensitive(duration>0)
        self._update_audio_position()

    def _update_audio_position(self):
        if self.audio_playing and self.audio_path is not None:
            success,position=self.player.query_position(self.gst.Format.TIME)
            if success:
                self.audio_scale.set_value(min(self._audio_duration()/1000,
                    self.audio_start/1000+position/self.gst.SECOND))
        position=int(self.audio_scale.get_value())
        duration=int(self._audio_duration()/1000)
        self.audio_position.set_text(f'{position//60}:{position%60:02d} / {duration//60}:{duration%60:02d}')
        return True

    def _reset_audio(self):
        if self.audio_seek_timer is not None:
            GLib.source_remove(self.audio_seek_timer)
            self.audio_seek_timer=None
        self.audio_seek_resume=False
        self.audio_pending=None
        self.audio_playing=False
        self.audio_paused=False
        self._stop_playback()
        self._set_audio_play_button(False)

    def _set_audio_play_button(self,playing):
        self.play_button.set_label('Pause' if playing else 'Play')
        self._icon_button(self.play_button,
            'media-playback-pause-symbolic' if playing else 'media-playback-start-symbolic',
            'Pause playback' if playing else 'Play audio')

    def on_stop_audio(self,*_args):
        self._reset_audio()
        self.audio_scale.set_value(0)
        self._update_audio_position()
        self.gst_error.set_text('Playback stopped.')

    def on_audio_track_changed(self,*_args):
        if not hasattr(self,'audio_scale'):
            return
        self._reset_audio()
        self._set_audio_duration()

    def on_audio_seek(self,_scale,_scroll,value):
        if not self.audio_durations:
            return False
        resume=self.audio_playing or self.audio_seek_resume
        self._reset_audio()
        self.audio_scale.set_value(value)
        self._update_audio_position()
        if resume and value*1000<self._audio_duration():
            self.audio_seek_resume=True
            self.audio_seek_timer=GLib.timeout_add(250,self._finish_audio_seek,int(value*1000))
        return False

    def _finish_audio_seek(self,start_ms):
        self.audio_seek_timer=None
        self.audio_seek_resume=False
        self.audio_playing=True
        self._request_audio_window(start_ms)
        return False

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
            'Recording is explicit. Cloud transcription sends audio to your selected provider.',
            classes=('dialog-copy',),wrap=True)
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

    def _recording_dialog(self,call_name=None):
        dialog=Gtk.Dialog(title='New recording',transient_for=self.window,modal=True)
        dialog.set_default_size(560,420)
        add_class(dialog.get_content_area(),'dialog-panel')
        grid=Gtk.Grid(column_spacing=10,row_spacing=10)
        grid.set_margin_top(10);grid.set_margin_bottom(10)
        grid.set_margin_start(10);grid.set_margin_end(10)
        source=Gtk.ComboBoxText()
        source.append_text('Microphone only')
        source.append_text('All system audio + microphone')
        source.set_active(1 if call_name else 0)
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
        title=Gtk.Entry();title.set_text(f'{call_name} conversation' if call_name else 'Meeting conversation');title.set_max_length(160)
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
        dialog.show_all()

    def _settings_dialog(self):
        dialog=Gtk.Dialog(title='Provider settings',transient_for=self.window,modal=True)
        dialog.set_default_size(560,380)
        add_class(dialog.get_content_area(),'dialog-panel')
        grid=Gtk.Grid(column_spacing=10,row_spacing=10)
        grid.set_margin_top(10);grid.set_margin_bottom(10)
        grid.set_margin_start(10);grid.set_margin_end(10)
        settings=self.state.settings or {}
        provider=Gtk.Entry();provider.set_text(str(settings.get('summary_provider') or 'OpenAI'));provider.set_max_length(80)
        endpoint=Gtk.Entry();endpoint.set_text(str(settings.get('summary_endpoint') or ''));endpoint.set_max_length(500)
        model=Gtk.Entry();model.set_text(str(settings.get('summary_model') or ''));model.set_max_length(100)
        summary_key=Gtk.Entry();summary_key.set_visibility(False);summary_key.set_max_length(2048)
        deepgram_key=Gtk.Entry();deepgram_key.set_visibility(False);deepgram_key.set_max_length(512)
        detection=Gtk.CheckButton(label='Suggest recording when a possible call is detected')
        detection.set_active(settings.get('detection_enabled',True))
        grid.attach(Gtk.Label(label='Notes provider'),0,0,1,1);grid.attach(provider,1,0,1,1)
        grid.attach(Gtk.Label(label='Notes API URL'),0,1,1,1);grid.attach(endpoint,1,1,1,1)
        grid.attach(Gtk.Label(label='Notes model'),0,2,1,1);grid.attach(model,1,2,1,1)
        grid.attach(Gtk.Label(label='Notes API key'),0,3,1,1);grid.attach(summary_key,1,3,1,1)
        grid.attach(Gtk.Label(label='Deepgram API key'),0,4,1,1);grid.attach(deepgram_key,1,4,1,1)
        grid.attach(detection,0,5,2,1)
        dialog.get_content_area().pack_start(grid,True,True,8)
        dialog.add_button('Cancel',Gtk.ResponseType.CANCEL)
        save=dialog.add_button('Save settings',Gtk.ResponseType.ACCEPT)
        add_class(save,'accent-button')
        def response(dialog,response_id):
            if response_id!=Gtk.ResponseType.ACCEPT:
                dialog.destroy();return
            payload={'summary_provider':provider.get_text(),
                'summary_endpoint':endpoint.get_text(),'summary_model':model.get_text(),
                'detection_enabled':detection.get_active()}
            if summary_key.get_text():
                payload['summary_key']=summary_key.get_text()
            if deepgram_key.get_text():
                payload['deepgram_key']=deepgram_key.get_text()
            self.client.send({'action':'save_settings','settings':payload})
            dialog.destroy()
        dialog.connect('response',response)
        dialog.show_all()


def run_native(port,token,child):
    if Gtk is None or Gdk is None or GLib is None:
        raise RuntimeError('GTK 3 runtime is required for the Ubuntu shell')
    shell=NativeShell(port,token,child)
    shell.refresh()
    Gtk.main()
    return shell

