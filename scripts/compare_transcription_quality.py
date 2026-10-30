"""Six explicitly authorized private-call trials, or offline verified-reference scoring.

Never plays audio, writes plaintext recordings, changes the workspace or retries.
Private responses are sealed with a key derived from the existing workspace key.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.hashes import SHA256
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from scribeloop_core.evaluation.streaming_quality import score_verified, stream_trial
from scribeloop_core.storage.encrypted import cipher_driver, database_account
from scribeloop_core.storage.keys import NativeKeyStore

AAD=b'SLQA1'


def seal(path,key,report):
    nonce=os.urandom(12)
    path.write_bytes(AAD+nonce+AESGCM(key).encrypt(nonce,json.dumps(report,ensure_ascii=False).encode(),AAD))
    path.chmod(0o600)


def unseal(path,key):
    value=path.read_bytes()
    if not value.startswith(AAD): raise ValueError('Invalid report')
    return json.loads(AESGCM(key).decrypt(value[5:17],value[17:],AAD))


def apply_references(report,path):
    if path is None: return
    refs=json.loads(path.read_text())
    if any(refs.get(k)!=report[k] for k in ('call_started_at','start_s','end_s')):
        raise ValueError('Reference belongs to a different audio interval')
    for result in report['results']:
        result['score']=score_verified(refs.get('tracks',{}).get(result['track'],{}),result,clip_start_s=report['start_s'])


async def run(args):
    marker=args.output.with_suffix('.attempt.json'); sealed=args.output.with_suffix('.enc')
    if not args.score_only and (marker.exists() or sealed.exists()):
        raise ValueError('Already attempted; choose a new explicitly authorized run, never retry automatically')
    if not args.score_only and args.reference_template and args.reference_template.exists():
        raise ValueError('Reference template already exists; refusing to overwrite human corrections')
    if not args.score_only and args.verified_text:
        raise ValueError('Verified phrase scoring requires --score-only')
    if not args.score_only and not args.consent_latest_call_upload:
        raise ValueError('Explicit --consent-latest-call-upload required for Deepgram and Soniox')
    if not 0<=args.start_s<args.end_s or args.end_s-args.start_s>60:
        raise ValueError('Select a bounded interval of at most sixty seconds')
    db=Path.home()/'Library/Application Support/ScribeLoop/meetings.sqlcipher'
    key=NativeKeyStore().get(database_account(db))
    if key is None: raise ValueError('Workspace key unavailable')
    report_key=HKDF(algorithm=SHA256(),length=32,salt=None,info=b'ai.scribeloop.quality-ab.v1').derive(key)
    if args.score_only:
        report=unseal(sealed,report_key)
        if report['call_started_at']!=args.expected_start_ms:
            raise ValueError('Report belongs to a different call')
        apply_references(report,args.references)
        if args.verified_text:
            ref={'human_verified':True,'text':args.verified_text,'start_s':args.phrase_start_s,'end_s':args.phrase_end_s}
            report['verified_phrase']={'track':args.phrase_track,**ref}
            for result in report['results']:
                if result['track']==args.phrase_track:
                    result['score']=score_verified(ref,result,clip_start_s=report['start_s'])
        if args.references or args.verified_text:
            seal(sealed,report_key,report)
            args.output.with_suffix('.scores.json').write_text(json.dumps([
                {'trial':r['trial'],'status':r['status'],'score':r.get('score')} for r in report['results']],indent=2)+'\n')
        print(json.dumps([{'trial':r['trial'],'status':r['status'],'score':r.get('score'),
            **({'text':r.get('text',''),'words':r.get('words',[])} if args.review_local else {})} for r in report['results']],ensure_ascii=False))
        return
    from inspect_soniox_streaming import load_key
    from scribeloop_core.api.desktop_settings import DesktopSettings
    from scribeloop_core.pipeline.transcription_quality import (
        RawMicrophoneAudio,
        TranscriptionQuality,
    )
    from scribeloop_core.storage.audio import SessionAudioStore
    from scribeloop_core.storage.playback import PlaybackIndex
    from scribeloop_core.storage.workspace_keys import WorkspaceAudioKeys
    soniox_key=load_key(); deepgram_key=DesktopSettings().value.deepgram_key
    if not deepgram_key: raise ValueError('Deepgram key unavailable')
    vocabulary=TranscriptionQuality(vocabulary=args.term).vocabulary
    conn=cipher_driver().connect(db.as_uri()+'?mode=ro',uri=True)
    try:
        conn.execute(f'''PRAGMA key = "x'{key.hex()}'"'''); conn.execute('PRAGMA query_only=ON')
        row=conn.execute("SELECT id,started_at,status FROM sessions WHERE source='meeting' ORDER BY started_at DESC LIMIT 1").fetchone()
        if not row or row[1]!=args.expected_start_ms or row[2]!='completed':
            raise ValueError('Latest call changed or is not completed; authorization does not apply')
        sid=row[0]
    finally: conn.close()
    root=db.with_name(db.name+'.audio-keys'); vault=WorkspaceAudioKeys(root,key,NativeKeyStore())
    class ReadOnlyKeys:
        def get(self,account): return vault._read(root/(account[6:]+'.key'),account)
    audio=SessionAudioStore(db.parent/'recordings',ReadOnlyKeys())
    try:
        clips={}
        for name,view,track in [('processed_mic',audio,'mic'),('raw_mic',RawMicrophoneAudio(audio),'mic'),('loopback',audio,'loopback')]:
            index=PlaybackIndex(view)
            if index.index(sid,track)['duration_ms']<args.end_s*1000:
                raise ValueError('Audio does not cover the entire authorized interval')
            clips[name]=index.window(sid,track,args.start_s*1000,args.end_s*1000)
    finally:
        audio.clear_unlocked_keys(); vault.close()
    plan=[('deepgram_processed','deepgram','processed_mic',[]),
          ('deepgram_raw','deepgram','raw_mic',[]),
          ('deepgram_raw_vocabulary','deepgram','raw_mic',vocabulary),
          ('deepgram_loopback_vocabulary','deepgram','loopback',vocabulary),
          ('soniox_raw_vocabulary','soniox','raw_mic',vocabulary),
          ('soniox_loopback_vocabulary','soniox','loopback',vocabulary)]
    args.output.parent.mkdir(parents=True,exist_ok=True)
    journal={'call_started_at':row[1],'start_s':args.start_s,'end_s':args.end_s,
             'maximum_audio_seconds':6*(args.end_s-args.start_s),'retries':0,'attempts':[]}
    with marker.open('x') as output: json.dump(journal,output,indent=2)
    report={**journal,'language_hints':['en','vi'],'vocabulary':vocabulary,'results':[]}
    apply_references(report,args.references)  # Validate reference identity before any upload.
    seal(sealed,report_key,report)
    if args.reference_template:
        with args.reference_template.open('x') as reference_file:
            reference_file.write(json.dumps({k:report[k] for k in ('call_started_at','start_s','end_s')} |
                {'tracks':{track:{'human_verified':False,'text':''} for track in ('mic','loopback')}},indent=2)+'\n')
    async def trial(name,provider,clip,terms):
        journal['attempts'].append({'trial':name,'provider':provider})
        marker.write_text(json.dumps(journal,indent=2)+'\n')
        result=await stream_trial(provider,clips[clip],api_key=deepgram_key if provider=='deepgram' else soniox_key,
                                  languages=['en','vi'],vocabulary=terms)
        result.update(trial=name,track='loopback' if clip=='loopback' else 'mic')
        report['results'].append(result); seal(sealed,report_key,report)
        print(json.dumps({k:v for k,v in result.items() if k not in ('text','words')}),flush=True)
    await asyncio.gather(*(trial(*item) for item in plan))
    apply_references(report,args.references); seal(sealed,report_key,report)
    journal['status']='finished'; marker.write_text(json.dumps(journal,indent=2)+'\n')
    summary={**{k:v for k,v in report.items() if k not in ('results','vocabulary','verified_phrase')},'vocabulary_count':len(vocabulary),'results':[
        {k:v for k,v in r.items() if k not in ('text','words')} for r in report['results']]}
    args.output.with_suffix('.summary.json').write_text(json.dumps(summary,indent=2)+'\n')


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--expected-start-ms',type=int,required=True)
    parser.add_argument('--start-s',type=int,default=30); parser.add_argument('--end-s',type=int,default=90)
    parser.add_argument('--output',type=Path,required=True)
    parser.add_argument('--consent-latest-call-upload',action='store_true')
    parser.add_argument('--term',action='append',default=[])
    parser.add_argument('--references',type=Path); parser.add_argument('--reference-template',type=Path)
    parser.add_argument('--score-only',action='store_true')
    parser.add_argument('--verified-text',help='Human-corrected phrase; sealed in the diagnostic report only')
    parser.add_argument('--phrase-start-s',type=float); parser.add_argument('--phrase-end-s',type=float)
    parser.add_argument('--phrase-track',choices=['mic','loopback'],default='mic')
    parser.add_argument('--review-local',action='store_true',help='Print decrypted diagnostic text locally, never upload')
    args=parser.parse_args()
    try: asyncio.run(run(args))
    except Exception as exc:  # noqa: BLE001 - never echo private speech, keys or provider headers
        raise SystemExit('Comparison stopped: '+type(exc).__name__) from None


if __name__=='__main__': main()
