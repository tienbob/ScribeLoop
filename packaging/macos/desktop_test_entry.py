"""Separate preview entry point. NEVER used by build_desktop.py or release app."""
import asyncio
import json
import os
import sys
import tempfile
from pathlib import Path

import numpy as np
from scribeloop_core.api.desktop import bootstrap, serve
from scribeloop_core.api.desktop_contract import DesktopContract
from scribeloop_core.api.desktop_fixture import FixtureKeys, deny_external_network, read_control
from scribeloop_core.api.desktop_settings import DesktopSettings
from scribeloop_core.api.server import create_app
from scribeloop_core.capture.source import AudioFrame
from scribeloop_core.meetings.service import DiscoveryService
from scribeloop_core.pipeline.model_selection import CloudRecordingRouter


class Vault:
    value = None
    def get_password(self, *_args):
        return self.value
    def set_password(self, _service, _account, value):
        self.value = value


def fixture_app(request, helpers):
    settings = DesktopSettings(Vault())
    contract = DesktopContract(settings, helpers/'scribeloop-meeting')
    def recording(store, audio, _factory, publish):
        sid = store.create_recording('Designing a quieter workspace',
            {'provider':'fixture','scope':'microphone','language':'en'})['id']
        audio.create_session(sid)
        store.set_audio_paths(sid,audio)
        with audio.writer(sid,'mic') as writer:
            writer.write(AudioFrame(np.zeros(16000,dtype=np.float32),'mic',0,0))
        words = ['We will focus the next release on a simpler recording experience.',
                 'The team agreed to keep the transcript close to the notes.',
                 'Our next step is to test the desktop workflow together.']
        for i in range(230):
            store.append(sid,{'text':words[i%3], 'start_ms':i*2000,'end_ms':i*2000+1500,'is_final':True},stream='mic')
        store.transition(sid,'completed')
        store.create_transcript_notes(sid)
        rows = store.segments(sid)
        from scribeloop_core.llm.grounded import transcript_hash
        cited = {'text':'The team discussed a simpler recording experience and keeping notes beside the transcript.',
                 'segment_ids':[rows[0]['id'],rows[1]['id']]}
        value = {'overview':cited,'title':{'text':'Designing a quieter workspace','segment_ids':[rows[0]['id']]},
                 'key_points':[cited],'decisions':[], 'action_items':[], 'open_questions':[], 'uncertainties':[]}
        store.save_grounded_summary(sid,1,transcript_hash(rows),value,provider='Synthetic preview',model='fixture')
        def source(*_args,**_kwargs):
            raise ValueError('Preview never opens capture')
        return CloudRecordingRouter(store,audio,source,publish,cloud_config=settings.value.cloud())
    return create_app(database=Path(request['database']),storage='encrypted',key_store=FixtureKeys(),
        discovery_factory=lambda publish:DiscoveryService(publish),recording_factory=recording,
        desktop=contract,launch_token=request['token'],summary_provider=settings.value.summary())[0]


def main():
    sys.addaudithook(deny_external_network)
    request=bootstrap(read_control(timeout=10))
    with tempfile.TemporaryDirectory(prefix='scribeloop-desktop-preview-') as directory:
        os.chmod(directory,0o700)
        request['database']=str(Path(directory)/'preview.sqlcipher')
        asyncio.run(serve(request,app_factory=fixture_app))


if __name__ == '__main__':
    try:
        main()
    except Exception:  # noqa: BLE001 - redact fixture startup errors
        print(json.dumps({'type':'fatal','message':'Preview failed'}),flush=True)
        raise SystemExit(1) from None
