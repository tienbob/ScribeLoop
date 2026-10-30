"""Generate synthetic business audio locally, then one real ASR and notes request.

Public fictional content only. Never opens the user's meeting database or saves
provider settings. Run with --allow-synthetic-upload to authorize cloud processing.
"""
from __future__ import annotations

import argparse
import asyncio
import html
import json
import secrets
import subprocess
import tempfile
import time
from pathlib import Path

import numpy as np
import soundfile as sf
from benchmark_deepgram_batch import EphemeralKeys, RequestCounter
from scipy.signal import resample_poly
from scribeloop_core.api.desktop_settings import DesktopSettings
from scribeloop_core.capture.source import AudioFrame
from scribeloop_core.evaluation.metrics import score_words
from scribeloop_core.llm.grounded import export_markdown, transcript_hash
from scribeloop_core.llm.rolling import RollingNotes
from scribeloop_core.pipeline.deepgram_meeting import DeepgramConfig, DeepgramRecordingRunner
from scribeloop_core.storage.audio import SessionAudioStore
from scribeloop_core.storage.db import migrate
from scribeloop_core.storage.encrypted import connect_encrypted

GUIDE = ('Write concise executive meeting notes. Prioritize commercial decisions, budget '
         'limits, launch gates and risks. Make each action specific with its explicitly '
         'named owner and deadline. Separate rejected proposals from approved decisions. '
         'Preserve unresolved disagreements and never treat a conditional launch as guaranteed.')
TURNS = [
    ('Maya Chen · Operations', 'Karen', 'This is Maya Chen, operations lead. Today is October twenty seventh, twenty twenty six. We need a decision on the Northstar enterprise onboarding pilot. Our original proposal was to launch to all twelve customers on November sixteenth. Before we commit, I want finance, engineering, and customer readiness on the table.'),
    ('Jordan Ellis · Finance', 'Daniel', 'Jordan Ellis from finance here. The current estimate is ninety five thousand dollars, but the approved ceiling is eighty thousand dollars, including ten thousand dollars of contingency. There is no approval for permanent headcount. I propose dropping the analytics dashboard from the pilot to bring the forecast down to seventy two thousand dollars.'),
    ('Priya Shah · Engineering', 'Moira', 'Priya Shah, engineering. Removing the dashboard saves about fifteen thousand dollars, but it does not solve the reliability issue. Our load test passed at two hundred concurrent users and failed at five hundred. I recommend a two customer pilot with Atlas Retail and Beacon Logistics, capped at one hundred active users each. A twelve customer launch is not technically ready.'),
    ('Maya Chen · Operations', 'Karen', 'Agreed on the narrower scope. The decision is a two customer pilot, not a twelve customer launch. We will target November sixteenth only if the security review and a five hundred user load test pass. The dashboard is excluded from the pilot. The budget ceiling remains eighty thousand dollars, and we will use seventy two thousand as the working forecast.'),
    ('Jordan Ellis · Finance', 'Daniel', 'I agree with the scope decision, but I do not want the eighteen thousand dollars of potential annual savings presented as confirmed savings. That is a sales estimate with no signed vendor amendment. I will send a revised forecast, including support costs and the contingency line, by October twenty ninth, twenty twenty six. Please assign that action to me, Jordan Ellis.'),
    ('Priya Shah · Engineering', 'Moira', 'I will own the remediation plan and rerun the five hundred user load test by November fifth, twenty twenty six. If the test still fails, my recommendation will be to move the pilot to November thirtieth. That later date is a fallback proposal, not a decision today. We also need security approval for the data retention policy before production access.'),
    ('Maya Chen · Operations', 'Karen', 'I will coordinate the security review and obtain a written go or no go by November sixth, twenty twenty six. The security team has not approved the policy yet. I will also ask legal whether customer consent needs an updated contract clause. We do not have an answer or a legal deadline yet, so that should stay an open question.'),
    ('Jordan Ellis · Finance', 'Daniel', 'There is a commercial dependency as well. Beacon has asked for twenty four hour support, which is not included in the seventy two thousand dollar forecast. I am against promising that service until we have a priced support proposal. We should not quietly assume the existing team will absorb weekend coverage.'),
    ('Maya Chen · Operations', 'Karen', 'Then our decision is no twenty four hour support commitment for this pilot unless finance approves a separate proposal. I will prepare a customer communication draft by November second, twenty twenty six, but nobody should send it before security and legal clearance. The draft must describe a conditional pilot rather than a guaranteed general release.'),
    ('Priya Shah · Engineering', 'Moira', 'For success criteria, I suggest ninety nine point nine percent availability, no critical security findings, and onboarding completed within two business days for both pilot customers. We can measure those during a two week observation period. I still disagree with publishing an availability service level agreement before the load test is complete.'),
    ('Maya Chen · Operations', 'Karen', 'We approve those as internal pilot success criteria only. They are not contractual service level commitments. We will review the pilot after two weeks. The unresolved points are the retention clause, the price of extended support, and who will cover the on call rota. We have not assigned an owner or deadline for the rota yet.'),
    ('Jordan Ellis · Finance', 'Daniel', 'To close, I own the revised forecast due October twenty ninth. Priya owns the load test due November fifth. Maya owns the security decision due November sixth and the customer communication draft due November second. The launch remains conditional. Please keep the speculative annual savings and the November thirtieth fallback out of the list of approved decisions.'),
]


def make_audio(output):
    pieces, timeline, position = [], [], 0
    with tempfile.TemporaryDirectory(prefix='scribeloop-business-voices-') as temp:
        for i,(speaker,voice,text) in enumerate(TURNS):
            path=Path(temp)/f'{i}.aiff'
            subprocess.run(['say','-v',voice,'-r','175','-o',str(path),text],check=True)
            pcm,rate=sf.read(path,dtype='float32')
            if pcm.ndim>1:
                pcm=pcm.mean(axis=1)
            from math import gcd
            divisor=gcd(rate,16000)
            pcm=resample_poly(pcm,16000//divisor,rate//divisor).astype(np.float32)
            timeline.append({'speaker':speaker,'start_ms':round(position/16),
                             'end_ms':round((position+len(pcm))/16),'text':text})
            pieces.extend((pcm,np.zeros(8000,dtype=np.float32)))
            position+=len(pcm)+8000
    audio=np.concatenate(pieces)
    sf.write(output/'business-meeting.wav',audio,16000,subtype='PCM_16')
    (output/'script.json').write_text(json.dumps(timeline,indent=2)+'\n')
    (output/'script.txt').write_text('\n\n'.join(f'{r["speaker"]}: {r["text"]}' for r in timeline)+'\n')
    return audio


async def run(output):
    from scribeloop_core.storage.sessions import SessionStore
    pcm=make_audio(output)
    print(f'Generated {len(pcm)/16000:.1f}s of fictional business audio',flush=True)
    settings=DesktopSettings().value
    provider=settings.model_copy(update={'summary_instructions':GUIDE}).summary()
    if not provider.configured:
        raise ValueError('Configure a summary provider in the app first')
    key=json.loads(Path('dist/macos/ScribeLoop.app/Contents/Resources/transcription.json').read_text())['api_key']
    counter=RequestCounter()
    conn=connect_encrypted(Path(':memory:'),secrets.token_bytes(32))
    try:
        store=SessionStore(migrate(conn))
        store.accept_agreement(store.AGREEMENT_VERSION)
        with tempfile.TemporaryDirectory(prefix='scribeloop-business-audit-') as directory:
            audio=SessionAudioStore(Path(directory),EphemeralKeys())
            sid=store.create_recording('Synthetic business pilot planning',{'scope':'microphone','provider':'deepgram'})['id']
            audio.create_session(sid)
            for i in range(0,len(pcm),16000):
                if i==0:
                    writer=audio.writer(sid,'mic')
                writer.write(AudioFrame(pcm[i:i+16000],'mic',i//16,0))
            writer.close()
            store.transition(sid,'completed')
            async def publish(event):
                if event['type']=='transcription_progress': print(event['mode'],flush=True)
            runner=DeepgramRecordingRunner(store,audio,None,publish,
                config=DeepgramConfig(api_key=key,batch_transport=counter,budget_usd=.25))
            began=time.perf_counter()
            runner.reprocess(sid,language='en',cloud_consent=True)
            await runner.task
            if store.get(sid)['status']!='completed':
                raise RuntimeError('Transcription failed; no retry')
            asr_seconds=time.perf_counter()-began
            rows=store.segments(sid)
            transcript=' '.join(r['text'] for r in rows)
            (output/'transcript.txt').write_text(transcript+'\n')
            (output/'segments.json').write_text(json.dumps(rows,indent=2)+'\n')
            engine=RollingNotes(store,provider,authorized=lambda:True)
            began=time.perf_counter()
            value=await engine.run(sid,rows,revision=1)
            notes=store.save_grounded_summary(sid,1,transcript_hash(rows),value,
                provider=provider.provider,model=provider.model)
            summary_seconds=time.perf_counter()-began
            markdown=export_markdown(store.get(sid),rows,store.revisions(sid),notes)
            (output/'meeting-notes.md').write_text(markdown)
            (output/'summary.json').write_text(json.dumps(value,indent=2)+'\n')
            reference=' '.join(turn[2] for turn in TURNS)
            report={'synthetic':True,'physical_capture':False,'audio_s':len(pcm)/16000,
                'asr_s':asr_seconds,'summary_s':summary_seconds,'wer':score_words(reference,transcript).wer,
                'segments':len(rows),'anonymous_speaker_labels':len({r['local_speaker_id'] for r in rows}),
                'asr_requests':counter.calls,'summary_usage':engine.public(sid),
                'summary_provider':provider.public(),'summary_instructions':GUIDE,'citations_validated':True}
            (output/'results.json').write_text(json.dumps(report,indent=2)+'\n')
            overview=html.escape(value['overview']['text'])
            sections=[]
            for title,name in [('Decisions','decisions'),('Actions','action_items'),('Open questions','open_questions'),('Risks and uncertainty','uncertainties')]:
                items=[]
                for item in value[name]:
                    text=item.get('text',item.get('task',''))
                    if name=='action_items': text+=f' — {item.get("owner") or "Unassigned"} · {item.get("due") or "No date stated"}'
                    links=' '.join(f'<a href="#s-{html.escape(identity)}">source</a>' for identity in item['segment_ids'])
                    items.append('<li>'+html.escape(text)+' '+links+'</li>')
                sections.append('<h2>'+title+'</h2><ul>'+''.join(items)+'</ul>')
            source=''.join('<p id="s-'+html.escape(r['id'])+'"><b>'+str(r['start_ms']//1000)+'s · '+html.escape(r.get('speaker_label') or 'Unknown')+'</b> '+html.escape(r['text'])+'</p>' for r in rows)
            (output/'demo.html').write_text('<!doctype html><meta charset="utf-8"><title>Business meeting demo</title><style>body{font:17px/1.6 system-ui;max-width:980px;margin:40px auto;padding:24px;background:#f4f7f1;color:#182a21}audio{width:100%}h1,h2{color:#246b53}li{margin:12px 0}a{color:#246b53}p:target{background:#d3ebd8}details{margin-top:32px}</style><h1>Northstar enterprise pilot review</h1><p>Synthetic business meeting · fictional people and figures · locally generated voices, real cloud transcription and notes.</p><audio controls src="business-meeting.wav"></audio><p>'+overview+'</p>'+''.join(sections)+'<details><summary>Transcript and citations</summary>'+source+'</details><details><summary>Summary instructions used</summary><p>'+html.escape(GUIDE)+'</p></details>')
            print(json.dumps(report),flush=True)
    finally:
        conn.close()


if __name__=='__main__':
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--allow-synthetic-upload',action='store_true',required=True)
    parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    args.output.mkdir(parents=True,exist_ok=False)
    asyncio.run(run(args.output))
