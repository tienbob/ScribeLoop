"""One real batch rerun of pinned public AMI audio, plus cited notes and WER.

Uses production runner/storage/provider classes in an isolated encrypted workspace.
Never reads private meeting data, never captures a microphone, never retries.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import html
import json
import resource
import secrets
import sys
import tempfile
import time
from pathlib import Path

import httpx
import numpy as np
import soundfile as sf
from scribeloop_core.api.desktop_settings import DesktopSettings
from scribeloop_core.capture.source import AudioFrame
from scribeloop_core.evaluation.metrics import NORMALIZATION, score_words
from scribeloop_core.llm.grounded import export_markdown, transcript_hash
from scribeloop_core.llm.rolling import RollingNotes
from scribeloop_core.pipeline.deepgram_meeting import (
    DeepgramConfig,
    DeepgramError,
    DeepgramRecordingRunner,
)
from scribeloop_core.storage.audio import SessionAudioStore
from scribeloop_core.storage.db import migrate
from scribeloop_core.storage.encrypted import connect_encrypted
from scribeloop_core.storage.sessions import SessionStore

FIXTURE = Path('data/corpus-v1/moss/segment-300-600-fp16-sdpa.wav')
BUNDLE_CONFIG = Path('dist/macos/ScribeLoop.app/Contents/Resources/transcription.json')


class EphemeralKeys:
    """Only public test audio; keys live in memory for the temporary workspace."""
    def __init__(self):
        self.values = {}
    def get(self, account):
        return self.values.get(account)
    def create(self, account):
        if account in self.values:
            raise ValueError('Key already exists')
        self.values[account] = secrets.token_bytes(32)
        return self.values[account]
    def delete(self, account):
        del self.values[account]


class RequestCounter(httpx.BaseTransport):
    def __init__(self):
        self.transport = httpx.HTTPTransport(retries=0,trust_env=False)
        self.calls = []
        self.response_body = bytearray()
        self.response_status = None
    def handle_request(self, request):
        request.headers['Accept-Encoding'] = 'identity'
        self.calls.append({'method':request.method,'path':request.url.path,
                           'upload_bytes':int(request.headers['content-length'])})
        response = self.transport.handle_request(request)
        self.response_status = response.status_code
        original, captured = response.stream, self.response_body
        class PublicResponse(httpx.SyncByteStream):
            def __iter__(self):
                for block in original:
                    if response.status_code==200 and len(captured)+len(block)<=32_000_000:
                        captured.extend(block)
                    yield block
            def close(self):
                original.close()
        response.stream = PublicResponse()
        return response
    def close(self):
        self.transport.close()


async def benchmark(output, report):
    pcm, rate = sf.read(FIXTURE,dtype='float32')
    baseline = json.loads(FIXTURE.with_suffix('.json').read_text())
    digest = hashlib.sha256(FIXTURE.read_bytes()).hexdigest()
    if digest != baseline['audio_sha256'] or rate != 16000 or pcm.shape != (300*16000,):
        raise ValueError('Pinned public five-minute fixture does not match baseline')
    reference = FIXTURE.with_suffix('.reference.txt').read_text()
    if score_words(reference,'').reference_words != baseline['reference_words']:
        raise ValueError('Pinned reference does not match baseline')
    # Keys stay inside this process and never enter the report or command arguments.
    api_key = json.loads(BUNDLE_CONFIG.read_text())['api_key']
    settings = DesktopSettings().value
    provider = settings.summary()
    if not provider.configured:
        raise ValueError('Saved summary provider is not configured')
    report.update(source='public AMI ES2004a mixed-headset seconds 300–600',
        audio_sha256=digest,reference_sha256=hashlib.sha256(reference.encode()).hexdigest(),
        audio_s=300,language=settings.language,normalization=NORMALIZATION,
        reference_words=baseline['reference_words'],private_meetings_uploaded=False,
        physical_capture_used=False,local_inference_used=False,
        channels={'loopback':'public meeting audio','mic':'generated digital silence'},
        summary_provider=provider.public())
    counter = RequestCounter()
    config = DeepgramConfig(api_key=api_key,batch_transport=counter,budget_usd=.25)
    stages = {}
    async def publish(event):
        if event['type']=='transcription_progress':
            mode = event['mode']
            stages.setdefault(mode,time.perf_counter())
            print(mode,flush=True)
    conn = connect_encrypted(Path(':memory:'),secrets.token_bytes(32))
    try:
        store = SessionStore(migrate(conn))
        store.accept_agreement(store.AGREEMENT_VERSION)
        with tempfile.TemporaryDirectory(prefix='scribeloop-public-batch-') as root:
            audio = SessionAudioStore(Path(root),EphemeralKeys())
            sid = store.create_recording('Public five-minute batch test',{
                'provider':'deepgram','model':'nova-3','scope':'application'})['id']
            audio.create_session(sid)
            store.set_audio_paths(sid,audio)
            for stream in ('loopback','mic'):
                with audio.writer(sid,stream) as writer:
                    for second in range(300):
                        frame = pcm[second*16000:(second+1)*16000] if stream=='loopback' else np.zeros(16000,dtype=np.float32)
                        writer.write(AudioFrame(frame,stream,second*1000,0))
            store.transition(sid,'completed')
            class ProbeRunner(DeepgramRecordingRunner):
                def _batch(self,*args):
                    try:
                        return super()._batch(*args)
                    except DeepgramError as exc:
                        # These are fixed local adapter messages, never provider bodies.
                        report['batch_validation_error'] = str(exc)
                        raise
            runner = ProbeRunner(store,audio,None,publish,config=config)
            began = time.perf_counter()
            print('Running one five-minute prerecorded request…',flush=True)
            runner.reprocess(sid,language=settings.language,cloud_consent=True)
            await runner.task
            ended = time.perf_counter()
            if counter.response_status==200 and counter.response_body:
                (output/'public-provider-response.json').write_bytes(counter.response_body)
            report.update(batch_pipeline_s=ended-began,actual_asr_requests=counter.calls,
                          asr_usage=store.cloud_usage(sid),session_status=store.get(sid)['status'])
            if store.get(sid)['status']!='completed':
                raise RuntimeError('Batch rerun failed; no retry made')
            rows = store.segments(sid)
            text = ' '.join(row['text'] for row in rows)
            score = score_words(reference,text)
            report.update(wer=score.wer,word_errors=score.errors,segments=len(rows),
                speakers=len({r['local_speaker_id'] for r in rows if r.get('local_speaker_id')}),
                transcript_revision=store.get(sid)['transcript_revision'],
                speed_realtime=300/(ended-began),
                upload_and_response_s=ended-stages['batch_upload'],
                provider_wait_plus_commit_s=ended-stages['batch_wait'])
            (output/'transcript.txt').write_text(text+'\n')
            (output/'segments.json').write_text(json.dumps(rows,indent=2,ensure_ascii=False)+'\n')
            # Save transcription evidence even if the separate notes request fails.
            (output/'results.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
            print('Transcription completed; generating cited notes…',flush=True)
            began_summary = time.perf_counter()
            engine = RollingNotes(store,provider,authorized=lambda:True)
            revision = store.get(sid)['transcript_revision']
            value = await engine.run(sid,rows,revision=revision)
            summary = store.save_grounded_summary(sid,revision,transcript_hash(rows),value,
                                                  provider=provider.provider,model=provider.model)
            report.update(summary_s=time.perf_counter()-began_summary,
                          summary_usage=engine.public(sid),summary_title=value.get('title'),
                          summary_citations_validated=True,status='completed')
            (output/'meeting-notes.md').write_text(export_markdown(store.get(sid),rows,store.revisions(sid),summary))
            table = ('<!doctype html><meta charset="utf-8"><title>Five-minute batch result</title>'
                '<style>body{font:16px system-ui;margin:32px;max-width:1200px}table{border-collapse:collapse}'
                'td,th{border:1px solid #aaa;padding:16px;vertical-align:top}td:nth-child(2){white-space:pre-wrap}</style>'
                '<h1>Five-minute Deepgram batch test</h1><table><tr><th>Sound file name</th>'
                '<th>Transcription</th><th>WER</th></tr><tr><td>'+html.escape(FIXTURE.name)+
                '</td><td>'+html.escape(text)+'</td><td>'+f'{score.wer:.2%}'+'</td></tr></table>')
            (output/'results.html').write_text(table)
    finally:
        conn.close()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-public-upload',action='store_true',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    report = {'status':'started','limitations':[
        'API/pipeline benchmark, not native UI/call detection or live latency.',
        'English mixed-headset public audio; microphone channel is digital silence.',
        'WER uses the established reference and normalization; no DER or speaker-accuracy score.',
        'Conservative app cost reservation is not provider billing.',
        'Only public transcript/notes are written in plaintext; temporary encrypted workspace is removed.']}
    try:
        asyncio.run(benchmark(args.output,report))
    except Exception as exc:  # noqa: BLE001 - never persist provider bodies or credentials
        report.update(status='failed',error_type=type(exc).__name__)
        if isinstance(exc,httpx.HTTPStatusError):
            report['http_status']=exc.response.status_code
    report['peak_client_rss_mb']=resource.getrusage(resource.RUSAGE_SELF).ru_maxrss/(1024**2 if sys.platform=='darwin' else 1024)
    (args.output/'results.json').write_text(json.dumps(report,indent=2,ensure_ascii=False)+'\n')
    print(json.dumps({k:report[k] for k in ('status','batch_pipeline_s','wer','segments','speakers','summary_s','error_type') if k in report}),flush=True)
    print('Results: '+str(args.output),flush=True)
    return 0 if report['status']=='completed' else 1


if __name__=='__main__':
    raise SystemExit(main())
