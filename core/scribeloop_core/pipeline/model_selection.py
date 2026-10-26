"""Cloud-only production router and a historical model-comparison harness."""
from .deepgram_meeting import DeepgramRecordingRunner


class ModelRecordingRunner:
    def __init__(self,*args,cloud_config=None,local_config=None):
        # Historical experiment harness only; the app uses CloudRecordingRouter.
        from .moss_meeting import MossRecordingRunner
        self.runners = {'deepgram':DeepgramRecordingRunner(*args,config=cloud_config),
                        'moss-q6-k':MossRecordingRunner(*args,config=local_config)}
        self.selected = 'deepgram'
        self.cloud = self.runners['deepgram'].cloud
        self.closed = False

    def __getattr__(self,name):
        return getattr(self.runners[self.selected],name)

    def profiles(self):
        return {'deepgram':self.cloud.public(),'moss-q6-k':self.runners['moss-q6-k'].config.public()}

    def select_model(self,model):
        if self.closed or self.active_id or (self.task and not self.task.done()):
            raise ValueError('Wait for the current recording or transcription to finish')
        if model not in self.runners:
            raise ValueError('Unknown transcription model')
        self.selected = model

    def start(self,selection,metadata,title, *, consent_confirmed):
        return self.runners[self.selected].start(selection,{**metadata,'engine':self.selected},title,
            consent_confirmed=consent_confirmed)

    def resume(self,sid, *, language='auto',cloud_consent=False):
        runner = self.runners[self.selected]
        previous = runner.store.transcript_provider(sid)
        provider = 'deepgram' if self.selected == 'deepgram' else 'local-moss'
        if runner.store.segments(sid) and previous != provider:
            raise ValueError('Choose Transcribe again to replace a transcript from another model')
        return runner.resume(sid,language=language,
            **({'cloud_consent':cloud_consent} if self.selected == 'deepgram' else {}))

    def reprocess(self,sid, *, language,cloud_consent=False):
        return self.runners[self.selected].reprocess(sid,language=language,
            **({'cloud_consent':cloud_consent} if self.selected == 'deepgram' else {}))

    async def close(self):
        self.closed = True
        for runner in self.runners.values():
            await runner.close()


class CloudRecordingRouter(DeepgramRecordingRunner):
    """Production app route: one cloud stream, no local model construction."""
    def __init__(self,*args,cloud_config=None):
        super().__init__(*args,config=cloud_config)

    def profiles(self):
        return {'deepgram':self.cloud.public()}

    def select_model(self,model):
        if model != 'deepgram' or self.closed or self.active_id or (self.task and not self.task.done()):
            raise ValueError('Only cloud streaming transcription is available; wait for active work')

    def start(self,selection,metadata,title,*,consent_confirmed):
        return super().start(selection,{**metadata,'engine':'deepgram'},title,
                             consent_confirmed=consent_confirmed)

    def resume(self,sid,*,language='auto',cloud_consent=False):
        if self.store.segments(sid) and self.store.transcript_provider(sid) != 'deepgram':
            raise ValueError('Use Transcribe again to replace a historical local transcript')
        return super().resume(sid,language=language,cloud_consent=cloud_consent)
