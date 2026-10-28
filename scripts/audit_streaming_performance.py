"""Reproducible offline streaming audit. Synthetic PCM; no devices or network."""
from __future__ import annotations

import argparse
import asyncio
import cProfile
import io
import json
import pstats
import resource
import secrets
import statistics
import sys
import tempfile
import threading
import time
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import numpy as np
from scribeloop_core.api.desktop_contract import DesktopContract
from scribeloop_core.api.desktop_settings import DesktopSettings
from scribeloop_core.capture.source import AudioFrame
from scribeloop_core.pipeline import deepgram_meeting as streaming
from scribeloop_core.storage.audio import SessionAudioStore
from scribeloop_core.storage.db import migrate
from scribeloop_core.storage.encrypted import connect_encrypted
from scribeloop_core.storage.sessions import SessionStore
from websockets.exceptions import ConnectionClosedOK


class Keys:
    def __init__(self): self.keys = {}
    def create(self, account):
        self.keys[account] = secrets.token_bytes(32)
        return self.keys[account]
    def get(self, account): return self.keys.get(account)
    def delete(self, account): del self.keys[account]


class Vault:
    def get_password(self, *_): return None


class Socket:
    def __init__(self, clock=None, on_send=None):
        self.clock, self.on_send = clock, on_send
        self.sent = self.reported = 0
        self.closed = self.metadata = False
    def __enter__(self): return self
    def __exit__(self, *_): pass
    def send(self, value):
        if isinstance(value, bytes):
            self.sent += len(value)//32
            if self.on_send: self.on_send(self.sent)
        else:
            assert json.loads(value)['type'] == 'CloseStream'
            self.closed = True
    def recv(self, timeout):
        if self.sent > self.reported:
            start = self.reported/1000
            end = self.sent/1000
            self.reported = self.sent
            return json.dumps({'type':'Results','is_final':True,'start':start,'duration':end-start,
                'channel':{'alternatives':[{'transcript':'synthetic test', 'words':[
                    {'word':'synthetic','start':start,'end':end,'speaker':0}]}]}})
        if self.closed:
            if not self.metadata:
                self.metadata = True
                return json.dumps({'type':'Metadata','duration':self.sent/1000})
            raise ConnectionClosedOK(None, None)
        if self.clock: self.clock.value += timeout
        else: time.sleep(timeout)
        raise TimeoutError


class Budget:
    def reserve(self, *_args, **_kwargs): pass


def distribution(values):
    return {'p50':round(float(np.percentile(values,50)),3),
            'p95':round(float(np.percentile(values,95)),3),
            'max':round(max(values),3)} if values else {}


def scheduler_bench(root, chunk, duration=60):
    """Real encrypted reader, deterministic provider clock, no network/OS sleeps."""
    audio = SessionAudioStore(root/f'packets-{chunk}', Keys())
    sid = uuid4().hex
    audio.create_session(sid)
    with audio.writer(sid,'mic') as writer:
        for start in range(0, duration*16000, chunk):
            count = min(chunk, duration*16000-start)
            writer.write(AudioFrame(np.full(count,.01,dtype='float32'),'mic',round(start/16),0))
    clock = SimpleNamespace(value=0.)
    original_time = streaming.time
    streaming.time = SimpleNamespace(monotonic=lambda:clock.value)
    socket = Socket(clock)
    reads = 0
    done, cancelled = threading.Event(), threading.Event()
    done.set()
    wall = time.perf_counter()
    try:
        with audio.cursor(sid,'mic') as cursor:
            original_read = cursor.read
            def read(**kwargs):
                nonlocal reads
                reads += 1
                return original_read(**kwargs)
            cursor.read = read
            streaming._stream_cursor(cursor,sid,'mic',lambda _:None,cancelled,done,
                config=streaming.DeepgramConfig(api_key='synthetic-only',connector=lambda *a,**k:socket),
                budget=Budget(),language='en',checkpoint=0)
    finally:
        streaming.time = original_time
    return {'packet_samples':chunk,'packet_ms':chunk/16,'audio_s':duration,
        'sent_audio_ms':socket.sent,
        'scheduler_elapsed_s':round(clock.value,3),'audio_per_scheduler_second':round(duration/clock.value,3) if clock.value else None,
        'cursor_reads_and_fsync_calls':reads,'local_compute_wall_s':round(time.perf_counter()-wall,3),
        'scope':'deterministic send scheduler; real SLPA reads; zero provider/network latency'}


def seed_transcript(store, count):
    sid = store.create_recording('Synthetic audit fixture',{'provider':'deepgram','model':'nova-3'})['id']
    speaker = store._speaker(sid,'mic','deepgram:'+'a'*32+':mic:0')
    with store.conn:
        store.conn.execute("INSERT INTO transcript_revisions(session_id,revision,origin) VALUES (?,1,'confirmed')",(sid,))
        store.conn.executemany('INSERT INTO segments '
            '(id,session_id,stream,local_speaker_id,start_ms,end_ms,text,language,source,revision) '
            'VALUES (?,?,?,?,?,?,?,?,?,?)',
            [(uuid4().hex,sid,'mic',speaker,i*600,i*600+500,'Synthetic meeting content for performance measurement.','en','stream',1)
             for i in range(count)])
    return sid


def page_bench(root):
    conn = migrate(connect_encrypted(root/'pages.sqlcipher',secrets.token_bytes(32)))
    store = SessionStore(conn)
    settings = DesktopSettings(Vault())
    contract = DesktopContract(settings,Path('/not-used'))
    app = SimpleNamespace(state=SimpleNamespace(store=store,audio=None))
    output = []
    try:
        for count in (100,1000,5000,16000):
            sid = seed_transcript(store,count)
            timings, statements, payload = [], [], None
            for _ in range(3):
                before = time.perf_counter()
                payload = contract.meeting(app,{'action':'meeting_page','session_id':sid,'offset':-1},settings.value.summary())
                timings.append((time.perf_counter()-before)*1000)
            queries = []
            conn.set_trace_callback(lambda sql, queries=queries:queries.append(sql.split(' ',1)[0]))
            contract.meeting(app,{'action':'meeting_page','session_id':sid,'offset':-1},settings.value.summary())
            conn.set_trace_callback(None)
            statements.append(len(queries))
            output.append({'segments_in_session':count,'returned_segments':len(payload['segments']),
                'page_ms':distribution(timings),'sql_statements_per_page':statistics.median(statements),
                'payload_bytes':len(json.dumps(payload).encode())})
        profile = cProfile.Profile()
        profile.runcall(contract.meeting,app,{'action':'meeting_page','session_id':sid,'offset':-1},settings.value.summary())
        report = io.StringIO()
        pstats.Stats(profile,stream=report).strip_dirs().sort_stats('cumulative').print_stats(15)
        output[-1]['cpu_profile_16000_segments'] = report.getvalue()
        from scribeloop_core.llm.grounded import transcript_hash
        from scribeloop_core.llm.rolling import sections
        rows = store.segments(sid)
        group = sections(rows)[0]
        summary = json.dumps({'overview':{'text':'Synthetic section notes','segment_ids':[group[0]['id']]}})
        with conn:
            conn.execute('INSERT INTO summary_jobs '
                '(id,session_id,revision,node_key,input_hash,provider,model,endpoint,state,attempts,input_bytes,summary_json) '
                "VALUES (?,?,1,'section:0',?,'Test','synthetic','https://test.invalid','completed',1,100,?)",
                (uuid4().hex,sid,transcript_hash(group),summary))
            conn.execute('INSERT INTO grounded_summaries '
                '(id,session_id,revision,input_hash,provider,model,summary_json,created_at) '
                "VALUES (?,?,1,?,'Test','synthetic',?,0)",(uuid4().hex,sid,transcript_hash(rows),summary))
        def page():
            start = time.perf_counter()
            value = contract.meeting(app,{'action':'meeting_page','session_id':sid,'offset':-1},settings.value.summary())
            return value, (time.perf_counter()-start)*1000
        value, cold = page()
        assert value['rolling'] and not value['summary']['stale']
        warm = [page()[1] for _ in range(3)]
        store.append(sid,{'text':'Synthetic later section','start_ms':10000000,'end_ms':10001000,'is_final':True},stream='mic')
        # Grounded final notes must become stale; the closed section stays valid.
        value, appended = page()
        assert value['rolling'] and value['summary']['stale']
        after_append_warm = [page()[1] for _ in range(3)]
        with conn:
            conn.execute('UPDATE segments SET text=? WHERE id=?',('Changed synthetic source',group[0]['id']))
        value, edited = page()
        assert value['rolling'] is None and value['summary']['stale']
        after_edit_warm = [page()[1] for _ in range(3)]
        output[-1]['existing_notes_cache'] = {'cold_validation_ms':round(cold,3),
            'warm_page_ms':distribution(warm),'first_page_after_append_ms':round(appended,3),
            'warm_page_after_append_ms':distribution(after_append_warm),
            'first_page_after_source_edit_ms':round(edited,3),'warm_stale_page_ms':distribution(after_edit_warm),
            'closed_section_survives_future_append':True,'source_edit_invalidates_section':True,
            'append_invalidates_grounded_final':True}
    finally:
        conn.close()
    return output


def send_cancellation_bench(root):
    audio = SessionAudioStore(root/'blocked-send',Keys())
    sid = uuid4().hex
    audio.create_session(sid)
    with audio.writer(sid,'mic') as writer:
        writer.write(AudioFrame(np.zeros(1600,dtype='float32'),'mic',0,0))
    entered, release, cancelled, done = (threading.Event() for _ in range(4))
    done.set()
    errors = []
    class BlockedSocket(Socket):
        aborted = False
        def abort(self):
            self.aborted = True
            release.set()
        def send(self, value):
            if isinstance(value,bytes):
                entered.set()
                release.wait(3)
                if self.aborted:
                    raise InterruptedError('Synthetic write aborted')
            super().send(value)
    def run():
        try:
            with audio.cursor(sid,'mic') as cursor:
                streaming._stream_cursor(cursor,sid,'mic',lambda _:None,cancelled,done,
                    config=streaming.DeepgramConfig(api_key='synthetic-only',connector=lambda *a,**k:BlockedSocket()),
                    budget=Budget(),language='en',checkpoint=0)
        except Exception as error:  # noqa: BLE001 - report any bounded fixture failure by type only
            errors.append(type(error).__name__)
    worker = threading.Thread(target=run)
    worker.start()
    try:
        if not entered.wait(2): raise RuntimeError('Blocked-send fixture did not enter send')
        cancelled.set()
        worker.join(.2)
        blocked = worker.is_alive()
    finally:
        release.set()
        worker.join(4)
    if worker.is_alive(): raise RuntimeError('Fixture did not release worker')
    return {'worker_still_blocked_200ms_after_cancel':blocked,'errors':errors,
        'scope':'bounded fake blocked send, released by harness; no network stall was induced'}


async def paced_bench(root, duration=8):
    """Actual recorder, SQLCipher persistence, DSP and provider workers; paced PCM."""
    from scribeloop_core.capture.microphone_processing import MicrophoneProcessing

    conn = migrate(connect_encrypted(root/'paced.sqlcipher',secrets.token_bytes(32)))
    store = SessionStore(conn)
    write_lags, send_lags, loop_lags = [], [], []
    started = None
    counts = {}
    first_text = None
    class Audio(SessionAudioStore):
        def writer(self,sid,stream):
            writer = super().writer(sid,stream)
            original = writer.write
            def write(frame):
                original(frame)
                write_lags.append((time.monotonic()-frame.wall_ts)*1000)
            writer.write = write
            return writer
    audio = Audio(root/'paced-audio',Keys())
    class Source:
        def __init__(self): self.stopped = threading.Event()
        def frames(self):
            nonlocal started
            started = time.monotonic()
            chunk = 171
            for sample in range(0,duration*16000,chunk):
                delay = started+sample/16000-time.monotonic()
                if delay>0: self.stopped.wait(delay)
                if self.stopped.is_set(): break
                count = min(chunk,duration*16000-sample)
                for stream in ('mic','loopback'):
                    yield AudioFrame(np.full(count,.01,dtype='float32'),stream,round(sample/16),time.monotonic())
        def request_stop(self): self.stopped.set()
        def close(self): self.stopped.set()
    source = Source()
    async def publish(event):
        nonlocal first_text
        kind = event['type']
        counts[kind] = counts.get(kind,0)+1
        if kind in {'segment','partial'} and first_text is None:
            first_text = time.monotonic()
    def sent(milliseconds):
        if started is not None:
            send_lags.append(max(0.,time.monotonic()-started-milliseconds/1000)*1000)
    runner = streaming.DeepgramRecordingRunner(store,audio,lambda *a,**k:source,publish,
        config=streaming.DeepgramConfig(api_key='synthetic-only',connector=lambda *a,**k:Socket(on_send=sent)))
    async def ticker():
        while True:
            before = time.monotonic()
            await asyncio.sleep(.005)
            loop_lags.append(max(0.,time.monotonic()-before-.005)*1000)
    tick = asyncio.create_task(ticker())
    cpu = time.process_time()
    wall = time.monotonic()
    try:
        sid = runner.start(None,{'cloud_consent':True,'language':'en',
            'microphone_processing':MicrophoneProcessing(noise_filter='standard').model_dump()},
            'Synthetic paced audit',consent_confirmed=True)['id']
        async with asyncio.timeout(40):
            await runner.task
        return {'audio_s':duration,'packet_samples':171,'streams':2,'dsp':'standard; encrypted raw mic retained',
            'elapsed_s':round(time.monotonic()-wall,3),'process_cpu_s':round(time.process_time()-cpu,3),
            'capture_to_encrypted_write_ms':distribution(write_lags),'capture_clock_to_sent_end_lag_ms':distribution(send_lags),
            'event_loop_extra_delay_ms':distribution(loop_lags),
            'first_saved_text_ms':round((first_text-started)*1000,3) if first_text else None,
            'status':store.get(sid)['status'],'events':counts,
            'scope':'8-second local paced dual-stream simulation; fake zero-latency provider; no SwiftUI or hardware'}
    finally:
        tick.cancel()
        await runner.close()
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    with tempfile.TemporaryDirectory(prefix='scribeloop-stream-audit-') as folder:
        root = Path(folder)
        report = {'mode':'offline synthetic audit; no capture/playback/network/provider calls',
            'scheduler':[scheduler_bench(root,size) for size in (170,171,320,1600)],
            'desktop_page_scaling':page_bench(root),'paced_pipeline':asyncio.run(paced_bench(root)),
            'blocked_send_cancellation':send_cancellation_bench(root)}
    usage = resource.getrusage(resource.RUSAGE_SELF)
    report['process_peak_rss_mib'] = round(usage.ru_maxrss/(1024**2 if sys.platform=='darwin' else 1024),2)
    report['acceptance_checks'] = {
        'scheduler_no_small_packet_throttle':all(r['scheduler_elapsed_s']<1 for r in report['scheduler']),
        'page_sql_bounded':all(r['sql_statements_per_page']<80 for r in report['desktop_page_scaling']),
        'paced_send_lag_p95_under_250ms':report['paced_pipeline']['capture_clock_to_sent_end_lag_ms']['p95']<250,
        'paced_finish_within_1s_of_audio':report['paced_pipeline']['elapsed_s']<report['paced_pipeline']['audio_s']+1,
        'blocked_send_cancelled':not report['blocked_send_cancellation']['worker_still_blocked_200ms_after_cancel'],
        'source_edit_invalidates_section':report['desktop_page_scaling'][-1]['existing_notes_cache']['source_edit_invalidates_section'],
        'append_invalidates_grounded_final':report['desktop_page_scaling'][-1]['existing_notes_cache']['append_invalidates_grounded_final']}
    args.output.parent.mkdir(parents=True,exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2)+'\n')
    print(json.dumps(report,indent=2))
    if not all(report['acceptance_checks'].values()):
        raise SystemExit('Streaming performance acceptance check failed; inspect the report')


if __name__ == '__main__': main()
