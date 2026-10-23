"""One selected backend per operation, sharing one capture lifecycle."""
from .deepgram_meeting import DeepgramRecordingRunner
from .moss_meeting import MossRecordingRunner


class ModelRecordingRunner:
    def __init__(self,*args,cloud_config=None,local_config=None):
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
