"""Local, bounded email snapshots. Never sends mail or contacts a model provider."""
from __future__ import annotations

import asyncio
import contextlib
import json
import os
import re
import shutil
import stat
import subprocess
import tempfile
import time
import wave
from pathlib import Path
from typing import Literal
from uuid import uuid4

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from ..capture.source import SAMPLE_RATE
from ..llm.grounded import timestamp

MAX_TEXT = 2_000_000
MAX_AUDIO = 400_000_000
MAX_CACHE = 2_000_000_000
EMAIL_LIMIT = 15*1024*1024  # Leaves room for base64 overhead on common mail limits.
TTL = 7*86400
ID = re.compile(r'^[0-9a-f]{32}$')


class EmailExportRequest(BaseModel):
    model_config = ConfigDict(extra='forbid',strict=True)
    action: Literal['prepare_email_export']
    session_id: str = Field(pattern=r'^[0-9a-f]{32}$')
    revision: int = Field(ge=0)
    kind: Literal['summary','transcript','audio']
    include_sources: bool = False


def plain(value):
    return ''.join(c for c in str(value) if c in '\n\t' or ord(c)>=32)


def summary_blocks(saved,rows,title,*,include_sources=False):
    """Semantic, literal text blocks: shells may style them without parsing HTML."""
    numbers={row['id']:i+1 for i,row in enumerate(rows)}
    by_id={row['id']:row for row in rows}
    used=set()
    def cited(item,action=False):
        ids=list(dict.fromkeys(sid for sid in item.get('segment_ids',[]) if sid in numbers))
        used.update(ids)
        value=plain(item['task'] if action else item['text'])
        if action:
            if item.get('owner'):value+=' · Owner: '+plain(item['owner'])
            if item.get('due'):value+=' · Due: '+plain(item['due'])
        return value+(' ['+', '.join(str(numbers[sid]) for sid in ids)+']' if ids and include_sources else '')
    summary=saved['summary']
    blocks=[{'style':'title','text':title},{'style':'paragraph','text':cited(summary['overview'])}]
    for heading,key in [('Key points','key_points'),('Decisions','decisions'),('Action items','action_items'),
                        ('Open questions','open_questions'),('Uncertainties','uncertainties')]:
        items=summary.get(key,[])
        if items:
            blocks.append({'style':'heading','text':heading})
            blocks.extend({'style':'bullet','text':cited(item,key=='action_items')} for item in items)
    if used and include_sources:
        blocks.append({'style':'references','text':'Sources'})
        blocks.extend({'style':'source','text':f'[{numbers[sid]}] {timestamp(by_id[sid]["start_ms"])}–{timestamp(by_id[sid]["end_ms"])} · '
            +plain(by_id[sid].get('speaker_label') or 'Speaker unknown')} for sid in numbers if sid in used)
    return blocks


def blocks_text(blocks):
    output=[]
    for block in blocks:
        value=('• ' if block['style']=='bullet' else '')+block['text']
        separator='\n' if block['style'] in {'source','bullet'} else '\n\n'
        output.append((separator if output else '')+value)
    return ''.join(output)


class EmailExports:
    def __init__(self,root=None,*,converter='/usr/bin/afconvert'):
        self.root=Path(root) if root else Path(tempfile.gettempdir())/f'scribeloop-email-{os.getuid()}'
        self.converter=converter

    def _root(self):
        self.root.mkdir(mode=0o700,parents=True,exist_ok=True)
        info=self.root.lstat()
        if not stat.S_ISDIR(info.st_mode) or info.st_uid!=os.getuid() or info.st_mode&0o077:
            raise ValueError('Temporary sharing storage is unavailable')

    def cleanup(self):
        if not self.root.exists():return
        self._root()
        for child in self.root.iterdir():
            if (ID.fullmatch(child.name) and not child.is_symlink() and child.is_dir()
                    and time.time()-child.stat().st_mtime>TTL):
                shutil.rmtree(child)

    def discard(self,ids):
        if not isinstance(ids,list) or len(ids)>30 or any(not isinstance(i,str) or not ID.fullmatch(i) for i in ids):
            raise ValueError('Invalid export selection')
        if not self.root.exists():return
        self._root()
        for identity in ids:
            path=self.root/identity
            if path.is_dir() and not path.is_symlink():shutil.rmtree(path)

    @staticmethod
    def _text(path,value):
        data=value.encode('utf-8')
        if len(data)>MAX_TEXT:raise ValueError('This text is too large for email sharing')
        with os.fdopen(os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as out:out.write(data)

    def _write(self,identity,sid,kind,title,body,preview,audio,tracks,blocks=None):
        self._root();self.cleanup()
        # Bound abandoned plaintext exports without deleting recent mail attachments.
        total=sum(p.stat().st_size for child in self.root.iterdir() if child.is_dir() and not child.is_symlink()
                  for p in child.iterdir() if p.is_file() and not p.is_symlink())
        reservation=MAX_AUDIO if kind=='audio' else len(preview.encode('utf-8'))+4096
        if total+reservation>MAX_CACHE:raise ValueError('Temporary sharing storage is full. Save existing attachments and try again later.')
        folder=self.root/identity;folder.mkdir(mode=0o700)
        try:
            if kind=='audio':
                file=self._audio(folder,sid,audio,tracks)
            else:
                file=folder/('Summary.txt' if kind=='summary' else 'Transcript.txt')
                self._text(file,preview)
            size=file.stat().st_size
            self._text(folder/'export.json',json.dumps({'session_id':sid,'kind':kind,'created_at':time.time()}))
            return {'export_id':identity,'session_id':sid,'kind':kind,'subject':title,
                'body':body,'body_blocks':blocks or [],'preview':preview[:12000],'preview_truncated':len(preview)>12000,
                'attachment':{'path':str(file.resolve()),'name':file.name,'bytes':size},
                'email_eligible':kind=='summary' or size<=EMAIL_LIMIT}
        except BaseException:
            shutil.rmtree(folder)
            raise

    def _audio(self,folder,sid,retained,tracks):
        durations=retained.authenticated_durations(sid,tracks)  # Authenticates each entire SLPA container first.
        duration=max(durations.values(),default=0)
        if not 0<duration<=10_805_000 or duration*SAMPLE_RATE//1000*2+44>MAX_AUDIO:
            raise ValueError('Retained audio is empty or too large to export')
        file=folder/'Conversation.wav'
        with os.fdopen(os.open(file,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600),'wb') as handle, wave.open(handle,'wb') as output:
            output.setnchannels(1);output.setsampwidth(2);output.setframerate(SAMPLE_RATE)
            for start in range(0,duration,60000):
                end=min(start+60000,duration)
                windows=[retained.playback_index.window(sid,track,start,end) for track in tracks]
                count=max((len(pcm) for pcm in windows),default=0)
                mixed=np.zeros(count,dtype=np.float32)
                for pcm in windows:mixed[:len(pcm)]+=pcm/len(windows)
                output.writeframes((np.clip(mixed,-1,1)*32767).astype('<i2').tobytes())
        compressed=folder/'Conversation.m4a'
        if Path(self.converter).is_file():
            try:
                subprocess.run([self.converter,'-f','m4af','-d','aac','-b','32000',str(file),str(compressed)],
                    check=True,timeout=180,stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
                if compressed.is_file() and compressed.stat().st_size>0:
                    compressed.chmod(0o600);file.unlink();return compressed
            except (OSError,subprocess.SubprocessError):pass
        compressed.unlink(missing_ok=True)
        return file  # Playable WAV remains available through Save if compression is unavailable.

    async def prepare(self,store,retained,command):
        request=EmailExportRequest.model_validate(command)
        session=store.get(request.session_id)
        if (not session or session['source']!='meeting' or session['status'] not in {'completed','failed','interrupted'}
                or session['transcript_revision']!=request.revision):
            raise ValueError('Reopen a saved conversation before sharing')
        title=' '.join(plain(session['title'] or 'Conversation').split())[:160]
        heading=f'{title}\n'
        tracks=[]
        blocks=[]
        if request.kind=='audio':
            if not retained or retained.busy:raise ValueError('Audio is busy or unavailable')
            retained.require(request.session_id);tracks=retained.status(request.session_id)['tracks']
            preview='Retained conversation audio. Microphone and meeting audio are combined when both are available.'
            body=f'Audio recording: {title}\n\nThe conversation audio is attached.'
        else:
            rows=store.segments(request.session_id,revision=request.revision)
            if not rows:raise ValueError('This conversation has no saved transcript')
            roster={person['id']:person for person in store.speaker_roster(request.session_id,request.revision)}
            for row in rows:
                person=roster.get(row.get('display_speaker_id'))
                if person:
                    number=person['person_number']
                    row['speaker_label']=(f'Person {number}' if person['name']==f'Speaker {number}' else person['name'])
            if request.kind=='summary':
                saved=store.grounded_summary(request.session_id)
                if not saved or saved['stale'] or saved['revision']!=request.revision:
                    raise ValueError('Generate an up-to-date summary before sharing it')
                blocks=summary_blocks(saved,rows,title,include_sources=request.include_sources)
                preview=blocks_text(blocks)
                body=preview
            else:
                preview=heading+'\n'+'\n\n'.join(f'{timestamp(row["start_ms"])}–{timestamp(row["end_ms"])} · '
                    +plain(row.get('speaker_label') or 'Speaker unknown')+' · '+('Microphone' if row['stream']=='mic' else 'Meeting audio')
                    +'\n'+plain(row['text']) for row in rows)
                body=f'Transcript: {title}\n\nThe full transcript is attached, with speaker labels and timestamps.'
        if len(preview.encode('utf-8'))>MAX_TEXT:raise ValueError('This text is too large for email sharing')
        identity=uuid4().hex
        if request.kind=='audio':
            try:
                result=await retained.export_worker(request.session_id,self._write,identity,request.session_id,request.kind,title,body,preview,retained,tracks,blocks)
            except asyncio.CancelledError:
                with contextlib.suppress(OSError,ValueError):self.discard([identity])
                raise
        else:
            # Finish before releasing the snapshot on cancellation; orphaned files
            # remain bounded by cleanup even if the requesting window disappears.
            task=asyncio.create_task(asyncio.to_thread(self._write,identity,request.session_id,request.kind,title,body,preview,None,[],blocks))
            try:result=await asyncio.shield(task)
            except asyncio.CancelledError:
                while not task.done():
                    try:await asyncio.shield(task)
                    except asyncio.CancelledError:continue
                    except Exception:break  # noqa: BLE001 - preserve the original cancellation after worker failure
                if not task.cancelled():task.exception()
                with contextlib.suppress(OSError,ValueError):self.discard([identity])
                raise
        return {'type':'email_export',**result,'revision':request.revision,
                'include_sources':request.kind=='summary' and request.include_sources}
