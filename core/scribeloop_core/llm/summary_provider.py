"""Explicit OpenAI-compatible text request, configured only by the local user."""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx


def summary_settings(path):
    """Literal .env assignments with environment overrides; never source a file."""
    names = ('SUMMARY_BASE_URL','SUMMARY_MODEL','SUMMARY_API_KEY','SUMMARY_PROVIDER','OPENAI_API_KEY')
    values = {}
    if Path(path).is_file():
        for line in Path(path).read_text(encoding='utf-8').splitlines():
            name,separator,value = line.strip().removeprefix('export ').partition('=')
            if separator and name.strip() in names:
                value = value.strip()
                if len(value)>1 and value[0] == value[-1] and value[0] in {'"',"'"}:
                    value = value[1:-1]
                values[name.strip()] = value
    return {name:os.environ.get(name,values.get(name,'' )).strip() for name in names}


class SummaryProvider:
    def __init__(self, *, settings=None, request=None):
        settings = settings if settings is not None else summary_settings(
            Path(__file__).resolve().parents[3]/'.env')
        self.base_url = (settings.get('SUMMARY_BASE_URL') or 'https://api.openai.com/v1').rstrip('/')
        self.openai = self.base_url == 'https://api.openai.com/v1'
        self.model = settings.get('SUMMARY_MODEL') or ('gpt-5.4-mini' if self.openai else '')
        self.provider = settings.get('SUMMARY_PROVIDER','') or ('OpenAI' if self.openai else 'OpenAI-compatible')
        self._api_key = settings.get('SUMMARY_API_KEY') or (settings.get('OPENAI_API_KEY','') if self.openai else '')
        self._request = request
        self.cloud = True
        self.configured = False
        try:
            url = urlsplit(self.base_url)
            try:
                loopback = ipaddress.ip_address(url.hostname or '').is_loopback
            except ValueError:
                loopback = url.hostname == 'localhost'
            self.cloud = not loopback
            valid = (url.scheme in {'https','http'} and url.hostname and
                (url.scheme == 'https' or loopback) and not url.username and not url.password and
                not url.query and not url.fragment and 1 <= len(self.model) <= 100 and
                len(self.provider) <= 80 and (bool(self._api_key) or loopback) and
                not any(ord(c)<32 for c in self.base_url+self.model+self.provider+self._api_key))
            _ = url.port  # Reject malformed ports.
            self.configured = bool(valid)
        except ValueError:
            pass

    def public(self):
        return {'provider':self.provider,'model':self.model,'cloud':self.cloud,
                'configured':self.configured,'endpoint':self.base_url if self.configured else '',
                'max_input_bytes':100_000}

    async def generate(self, messages):
        if len(json.dumps(messages,ensure_ascii=True).encode())>100000:
            raise ValueError('Summary request too large')
        if not self.configured:
            raise ValueError('Configure summary endpoint, model and key locally')
        if self._request:
            return await self._request(messages)
        body = ({'model':self.model,'input':messages,'max_output_tokens':4000,'store':False}
                if self.openai else {'model':self.model,'messages':messages,'max_tokens':4000,'temperature':0.2})
        headers = {'Authorization':'Bearer '+self._api_key} if self._api_key else {}
        async with httpx.AsyncClient(timeout=httpx.Timeout(60,connect=10),
                                     trust_env=False,follow_redirects=False) as client, client.stream('POST',self.base_url+('/responses' if self.openai else '/chat/completions'),
                headers=headers,json=body) as response:
            response.raise_for_status()
            data = bytearray()
            async for block in response.aiter_bytes():
                data.extend(block)
                if len(data) > 200_000:
                    raise ValueError('Summary response too large')
        value = json.loads(data)
        if self.openai:
            if value.get('status') != 'completed':
                raise ValueError('Summary response was incomplete')
            parts = [part['text'] for item in value.get('output',[]) if item.get('type') == 'message'
                     for part in item.get('content',[]) if part.get('type') == 'output_text']
            if not parts:
                raise ValueError('Summary response has no text')
            return ''.join(parts)
        return value['choices'][0]['message']['content']
