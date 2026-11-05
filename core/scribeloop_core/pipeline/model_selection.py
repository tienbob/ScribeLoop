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
    def __init__(self,*args,cloud_config=None,cleanup_provider=None):
        super().__init__(*args,config=cloud_config or DeepgramConfig(budget_usd=.25,max_requests=160),
            cleanup_provider=cleanup_provider)
        self.base_config=self.cloud

    def configure_cloud(self,config):
        """Replace idle cloud defaults without reaching into runner internals."""
        if self.closed or self.active_id or (self.task and not self.task.done()):
            raise ValueError('Wait for recording and processing to finish')
        self.base_config=config
        self.cloud=config

    def profiles(self):
        return {'deepgram':{**self.cloud.public(),'duration_profiles':{
            '300':{'label':'5 minutes · development','budget_usd':self.base_config.budget_usd},
            '3600':{'label':'1 hour','budget_usd':3},
            '10800':{'label':'3 hours','budget_usd':8}}}}

    def _duration(self,duration):
        if duration not in {300,3600,10800} or self.closed or self.active_id or (self.task and not self.task.done()):
            raise ValueError('Invalid duration or busy recorder')
        self.cloud=(self.base_config if duration==300 else DeepgramConfig(api_key=self.base_config.api_key,
            connector=self.base_config.connector,batch_transport=self.base_config.batch_transport,
            budget_usd=3 if duration==3600 else 8,
            max_requests=1800 if duration==3600 else 4800,
            write_timeout_s=self.base_config.write_timeout_s, quality=self.base_config.quality))
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

    def resume(self,sid,*,language='auto',cloud_consent=False,language_hints=None):
        self._saved_duration(sid)
        if self.store.segments(sid) and self.store.transcript_provider(sid) != 'deepgram':
            raise ValueError('Use Transcribe again to replace a historical local transcript')
        return super().resume(sid,language=language,cloud_consent=cloud_consent,language_hints=language_hints)

    def reprocess(self,sid,*,language,cloud_consent=False,language_hints=None):
        self._saved_duration(sid)
        return super().reprocess(sid,language=language,cloud_consent=cloud_consent,language_hints=language_hints)


class TranscriptionRouter:
    """Production engine route: Deepgram cloud streaming or Ollama local batch.

    Selection happens per recording via the wire ``model`` field. Switching
    engines mid-recording is refused like any other busy-recorder change, and a
    saved transcript made with another engine is never silently mixed: resume
    uses the same engine that produced it, and Transcribe again replaces it.
    """
    def __init__(self,*args,cloud_config=None,local_config=None,cleanup_provider=None):
        from .local_transcription import LocalRecordingRunner
        self._args = args
        self.runners = {'deepgram':CloudRecordingRouter(*args,cloud_config=cloud_config,
            cleanup_provider=cleanup_provider)}
        self.local_config = local_config
        if local_config is not None:
            self.runners['ollama'] = LocalRecordingRunner(*args,config=local_config)
        self.selected = 'ollama' if local_config is not None else 'deepgram'
        self.closed = False

    def configure(self,*,cloud_config,local_config,cleanup_provider=None):
        """Apply idle Preferences changes without requiring an app restart."""
        from .local_transcription import LocalRecordingRunner
        if self.closed or self.active_id or (self.task and not self.task.done()):
            raise ValueError('Wait for recording and processing to finish')
        cloud = self.runners['deepgram']
        cloud.configure_cloud(cloud_config)
        cloud.cleanup_provider = cleanup_provider
        self.local_config = local_config
        if local_config is not None:
            if 'ollama' in self.runners:
                self.runners['ollama'].config = local_config
            else:
                self.runners['ollama'] = LocalRecordingRunner(*self._args,config=local_config)
        self.selected = 'ollama' if local_config is not None else 'deepgram'

    def refresh_cleanup(self,provider):
        """Re-sync the idle cleanup engines with the latest saved settings.

        A Preferences save reconfigures the recorder too, but a save that was
        rejected as busy, or an engine built before that save, would otherwise
        keep using the previous cleanup rules (for example the Experience
        validation toggle) until the app restarts.
        """
        if self.closed or self.active_id or (self.task and not self.task.done()):
            raise ValueError('Wait for recording and processing to finish')
        self.runners['deepgram'].cleanup_provider = provider
        if 'ollama' in self.runners:
            self.runners['ollama'].config.cleanup_provider = provider

    def __getattr__(self,name):
        if name in {'runners','selected','local_config'}:
            raise AttributeError(name)
        return getattr(self.runners[self.selected],name)

    @property
    def cloud(self):
        runner = self.runners[self.selected]
        return getattr(runner,'cloud',None) or getattr(runner,'config',None)

    def profiles(self):
        profiles = self.runners['deepgram'].profiles()
        if 'ollama' in self.runners:
            profiles['ollama'] = self.runners['ollama'].config.public()
        return profiles

    def select_model(self,model):
        if self.closed or self.active_id or (self.task and not self.task.done()):
            raise ValueError('Wait for the current recording or transcription to finish')
        if model not in self.runners:
            raise ValueError('Ollama transcription is not configured; set it up in Preferences.'
                             if model == 'ollama' else 'Unknown transcription model')
        self.selected = model

    def start(self,selection,metadata,title,*,consent_confirmed):
        return self.runners[self.selected].start(selection,metadata,title,
                                                 consent_confirmed=consent_confirmed)

    def resume(self,sid,*,language='auto',cloud_consent=False,language_hints=None):
        runner = self.runners[self.selected]
        if self.selected == 'deepgram':
            return runner.resume(sid,language=language,cloud_consent=cloud_consent,
                                 language_hints=language_hints)
        return runner.resume(sid,language=language)

    def reprocess(self,sid,*,language,cloud_consent=False,language_hints=None):
        runner = self.runners[self.selected]
        if self.selected == 'deepgram':
            return runner.reprocess(sid,language=language,cloud_consent=cloud_consent,
                                    language_hints=language_hints)
        return runner.reprocess(sid,language=language)

    async def close(self):
        self.closed = True
        for runner in self.runners.values():
            await runner.close()
