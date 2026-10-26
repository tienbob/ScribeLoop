"""Cloud-only production router and a historical model-comparison harness."""
from .deepgram_meeting import DeepgramConfig, DeepgramRecordingRunner


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
        super().__init__(*args,config=cloud_config or DeepgramConfig(budget_usd=.25,max_requests=160))
        self.base_config=self.cloud

    def profiles(self):
        return {'deepgram':{**self.cloud.public(),'duration_profiles':{
            '300':{'label':'5 minutes · development','budget_usd':self.base_config.budget_usd},
            '3600':{'label':'1 hour','budget_usd':3},
            '10800':{'label':'3 hours','budget_usd':8}}}}

    def _duration(self,duration):
        if duration not in {300,3600,10800} or self.closed or self.active_id or (self.task and not self.task.done()):
            raise ValueError('Invalid duration or busy recorder')
        self.cloud=(self.base_config if duration==300 else DeepgramConfig(api_key=self.base_config.api_key,
            connector=self.base_config.connector,budget_usd=3 if duration==3600 else 8,
            max_requests=1800 if duration==3600 else 4800))
        self.max_s=duration

    def _saved_duration(self,sid):
        session=self._saved(sid)
        self._duration(session.get('capture',{}).get('duration_s') or 300)

    def select_model(self,model):
        if model != 'deepgram' or self.closed or self.active_id or (self.task and not self.task.done()):
            raise ValueError('Only cloud streaming transcription is available; wait for active work')

    def start(self,selection,metadata,title,*,consent_confirmed):
        self._duration(metadata.get('duration_s') or 300)
        return super().start(selection,{**metadata,'engine':'deepgram'},title,
                             consent_confirmed=consent_confirmed)

    def resume(self,sid,*,language='auto',cloud_consent=False):
        self._saved_duration(sid)
        if self.store.segments(sid) and self.store.transcript_provider(sid) != 'deepgram':
            raise ValueError('Use Transcribe again to replace a historical local transcript')
        return super().resume(sid,language=language,cloud_consent=cloud_consent)

    def reprocess(self,sid,*,language,cloud_consent=False):
        self._saved_duration(sid)
        return super().reprocess(sid,language=language,cloud_consent=cloud_consent)
