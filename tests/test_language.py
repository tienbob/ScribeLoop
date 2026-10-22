"""Language is detected once from early speech, never independently per chunk."""
import numpy as np
import pytest
from scribeloop_core.pipeline.language import (
    LanguageDetectionError,
    early_speech,
    identify_language,
)


def test_opening_speech_is_bounded_and_skips_silence():
    pcm=np.arange(40*16000,dtype=np.float32)
    seen=[]
    def vad(opening):
        seen.append(opening.size)
        return [{'start':3*16000,'end':20*16000}]
    sample=early_speech({'mic':pcm,'loopback':pcm},vad)
    assert seen==[30*16000,30*16000]
    assert len(sample)==12*16000
    assert sample[0]==3*16000
    assert sample[6*16000]==3*16000


def test_insufficient_speech_does_not_guess():
    with pytest.raises(LanguageDetectionError):
        early_speech({'mic':np.zeros(16000,dtype=np.float32)},lambda pcm:[])


class Detector:
    def __init__(self,language='vi',probability=.95):
        self.language=language
        self.probability=probability
        self.calls=0
    def detect_language(self,sample,**kwargs):
        self.calls+=1
        return self.language,self.probability,[(self.language,self.probability),('other',1-self.probability)]


def test_one_detection_for_both_tracks():
    detector=Detector()
    lock=identify_language({'mic':np.ones(16000*5,dtype=np.float32),
                            'loopback':np.ones(16000*5,dtype=np.float32)},detector=detector,
                            speech_timestamps=lambda pcm:[{'start':0,'end':len(pcm)}])
    assert detector.calls==1
    assert lock=={'language':'vi','mode':'auto','confidence':.95,'sample_s':10}


@pytest.mark.parametrize('language,probability',[('vi',.55),('zh',.99),('en',float('nan'))])
def test_uncertain_or_unsupported_language_requires_manual_choice(language,probability):
    with pytest.raises(LanguageDetectionError):
        identify_language({'mic':np.ones(16000*5,dtype=np.float32)},detector=Detector(language,probability),
                          speech_timestamps=lambda pcm:[{'start':0,'end':len(pcm)}])
