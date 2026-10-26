"""Groq adapter regression tests; mocked HTTPS only, no credentials or paid calls."""
from __future__ import annotations

import io
import json

import httpx
import numpy as np
import pytest
import soundfile as sf
from scribeloop_core.pipeline.groq_asr import ENDPOINT, GroqError, load_groq_key, transcribe_groq

KEY = 'test-key-never-log-this'


def audio():
    return np.zeros(16000,dtype=np.float32)


def test_key_loader_literal_only_environment_precedence_and_no_secret_errors(tmp_path,monkeypatch):
    monkeypatch.delenv('GROQ_API_KEY',raising=False)
    path = tmp_path/'.env'
    path.write_text('export GROQ_API_KEY="'+KEY+'"\nUNRELATED=ignored\n')
    assert load_groq_key(path) == KEY
    monkeypatch.setenv('GROQ_API_KEY','environment-key-value')
    assert load_groq_key(path) == 'environment-key-value'
    monkeypatch.delenv('GROQ_API_KEY')
    path.write_text('GROQ_API_KEY=$(print dangerous)\n')
    with pytest.raises(GroqError) as exc:
        load_groq_key(path)
    assert 'dangerous' not in str(exc.value)


def test_flac_upload_fixed_endpoint_and_timestamp_bounds():
    seen = []
    def handler(request):
        seen.append(request)
        assert str(request.url) == ENDPOINT
        assert request.headers['authorization'] == 'Bearer '+KEY
        body = request.read()
        assert b'whisper-large-v3-turbo' in body
        assert b'name="language"\r\n\r\nen' in body
        assert b'public-test.flac' in body
        start = body.index(b'fLaC')
        boundary = request.headers['content-type'].split('boundary=')[1].encode()
        end = body.index(b'\r\n--'+boundary,start)
        pcm,sr = sf.read(io.BytesIO(body[start:end]),dtype='float32')
        assert sr == 16000 and len(pcm) == 16000
        return httpx.Response(200,json={'text':'Hello','language':'english',
            'segments':[{'start':.1,'end':1.2,'text':' Hello '}]})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        result = transcribe_groq(audio(),api_key=KEY,client=client)
    assert len(seen) == 1
    assert result['segments'][0]['start_ms'] == 100
    assert result['segments'][0]['end_ms'] == 1000
    assert result['upload_bytes'] > 0


@pytest.mark.parametrize('status',[401,403,413,429,500,302])
def test_http_errors_are_sanitized_and_never_retried(status):
    calls = []
    def handler(request):
        calls.append(True)
        return httpx.Response(status,text='private body '+KEY,
                              headers={'location':'https://untrusted.invalid'})
    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(GroqError) as exc:
        transcribe_groq(audio(),api_key=KEY,client=client)
    assert len(calls) == 1
    assert KEY not in str(exc.value) and 'private body' not in str(exc.value)


@pytest.mark.parametrize('body',[
    b'not json',json.dumps({'text':42}).encode(),
    json.dumps({'text':'test','segments':[{'start':-1,'end':1,'text':'bad'}]}).encode(),
    json.dumps({'text':'test','segments':[{'start':float('nan'),'end':1,'text':'bad'}]}).encode(),
    b'x'*4_000_001])
def test_bad_or_oversized_responses_fail_without_body_exposure(body):
    with (httpx.Client(transport=httpx.MockTransport(lambda req:httpx.Response(200,content=body))) as client,
          pytest.raises(GroqError)):
        transcribe_groq(audio(),api_key=KEY,client=client)


def test_invalid_input_never_opens_network():
    def forbidden(request):
        raise AssertionError('Unexpected network call')
    with httpx.Client(transport=httpx.MockTransport(forbidden)) as client:
        for pcm in (np.zeros((10,2),dtype=np.float32),np.array([np.nan],dtype=np.float32),
                    np.zeros(300*16000+1,dtype=np.float32)):
            with pytest.raises(ValueError):
                transcribe_groq(pcm,api_key=KEY,client=client)
        with pytest.raises(ValueError):
            transcribe_groq(audio(),api_key=KEY,model='unknown',client=client)


def test_network_error_has_no_key_or_request_details():
    def handler(request):
        raise httpx.ConnectError(KEY+' sensitive details',request=request)
    with httpx.Client(transport=httpx.MockTransport(handler)) as client, pytest.raises(GroqError) as exc:
        transcribe_groq(audio(),api_key=KEY,client=client)
    assert KEY not in str(exc.value)
