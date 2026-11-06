"""Explicit OpenAI-compatible text request, configured only by the local user."""
from __future__ import annotations

import ipaddress
import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import httpx

from .ollama_lifecycle import KEEP_ALIVE


class IncompleteSummaryOutput(ValueError):
    """Provider explicitly stopped before completing its structured response."""


def ollama_summary_schema(messages):
    """Constrain citation strings to this request's original source identities."""
    from .schemas import GroundedSummary
    schema = GroundedSummary.model_json_schema()
    identities = set()
    for message in messages:
        if message.get('role') != 'user':
            continue
        try:
            payload = json.loads(message.get('content',''))
        except (ValueError,TypeError):
            continue
        if not isinstance(payload,dict):
            continue
        rows = payload.get('untrusted_transcript')
        if isinstance(rows,list):
            identities.update(row['id'] for row in rows if isinstance(row,dict)
                and isinstance(row.get('id'),str) and row['id'])
        allowed = payload.get('allowed_original_segment_ids')
        if isinstance(allowed,list):
            identities.update(identity for identity in allowed if isinstance(identity,str) and identity)
    if identities:
        for name in ('CitedText','CitedAction'):
            schema['$defs'][name]['properties']['segment_ids']['items']['enum'] = sorted(identities)
    return schema


def summary_settings(path):
    """Literal .env assignments with environment overrides; never source a file."""
    names = ('SUMMARY_BASE_URL','SUMMARY_MODEL','SUMMARY_API_KEY','SUMMARY_PROVIDER','SUMMARY_INSTRUCTIONS','OPENAI_API_KEY')
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


def _simplify_schema(source):
    """Small provider grammar; full length/list/citation checks remain local.

    Nested array bounds in a Pydantic schema multiply Gemini's grammar states.
    Inline the small acyclic definitions and omit decoding bounds/defaults rather
    than weakening validation or retrying a rejected request.
    """
    definitions = source.get('$defs', {})
    def simplify(node):
        if isinstance(node, list):
            return [simplify(value) for value in node]
        if not isinstance(node, dict):
            return node
        if '$ref' in node:
            return simplify(definitions[node['$ref'].removeprefix('#/$defs/')])
        result = {}
        for key, value in node.items():
            if key in {'$defs','default','title','minItems','maxItems','minLength','maxLength'}:
                continue
            if key == 'properties':
                # Property names may themselves be JSON Schema keywords (title).
                result[key] = {name:simplify(schema) for name,schema in value.items()}
            else:
                result[key] = simplify(value)
        if 'properties' in result:
            result['required'] = list(result['properties'])
        return result
    return simplify(source)


def gemini_summary_schema():
    from .schemas import GroundedSummary
    return _simplify_schema(GroundedSummary.model_json_schema())


def ollama_request_body(messages,schema,*,model):
    """Native Ollama chat body: no-thinking, bounded context and idle keep-alive."""
    return {'model':model,'messages':messages,'stream':False,'think':False,
        'keep_alive':KEEP_ALIVE,
        'format':schema if schema is not None else ollama_summary_schema(messages),
        'options':{'num_ctx':16384,'num_predict':4000,'temperature':0.2}}


class SummaryProvider:
    def __init__(self, *, settings=None, request=None):
        settings = settings if settings is not None else summary_settings(
            Path(__file__).resolve().parents[3]/'.env')
        self.base_url = (settings.get('SUMMARY_BASE_URL') or 'https://api.openai.com/v1').rstrip('/')
        self.openai = self.base_url == 'https://api.openai.com/v1'
        self.model = settings.get('SUMMARY_MODEL') or ('gpt-5.4-mini' if self.openai else '')
        self.provider = settings.get('SUMMARY_PROVIDER','') or ('OpenAI' if self.openai else 'OpenAI-compatible')
        self.instructions = settings.get('SUMMARY_INSTRUCTIONS','')
        if not isinstance(self.instructions,str) or len(self.instructions)>2000:
            raise ValueError('Summary instructions must be at most 2000 characters')
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
            if self.provider == 'Ollama' and (not loopback or 'cloud' in self.model.rsplit(':',1)[-1].lower()):
                self.configured = False
        except ValueError:
            pass

    def public(self):
        return {'provider':self.provider,'model':self.model,'cloud':self.cloud,
                'configured':self.configured,'endpoint':self.base_url if self.configured else '',
                'max_input_bytes':100_000}

    async def generate(self, messages, *, schema=None):
        if len(json.dumps(messages,ensure_ascii=True).encode())>100000:
            raise ValueError('Summary request too large')
        if not self.configured:
            raise ValueError('Configure summary endpoint, model and key locally')
        if self._request:
            return await self._request(messages)
        body = ({'model':self.model,'input':messages,'max_output_tokens':4000,'store':False}
                if self.openai else {'model':self.model,'messages':messages,'max_tokens':4000,'temperature':0.2})
        if self.base_url == 'https://generativelanguage.googleapis.com/v1beta/openai':
            body['response_format'] = {'type':'json_schema','json_schema':{
                'name':'meeting_summary' if schema is None else 'transcript_cleanup',
                'strict':schema is None,
                'schema':gemini_summary_schema() if schema is None else _simplify_schema(schema)}}
        elif self.openai and schema is not None:
            # The Responses API carries its grammar under text.format.
            body['text'] = {'format':{'type':'json_schema','name':'transcript_cleanup',
                'strict':False,'schema':schema}}
        elif schema is not None:
            # OpenAI-compatible servers (LM Studio and cloud custom endpoints)
            # accept JSON-schema response formats.
            body['response_format'] = {'type':'json_schema','json_schema':{
                'name':'transcript_cleanup','strict':False,'schema':schema}}
        if self.provider == 'Ollama':
            # The compatibility endpoint can ignore think=False and inherit a
            # 4K context, consuming the answer budget on hidden reasoning. Native
            # options make the context/output budgets and no-thinking explicit.
            # keep_alive bounds residency for large models; small models are
            # unloaded explicitly when their task finishes.
            body = ollama_request_body(messages,schema,model=self.model)
        headers = {'Authorization':'Bearer '+self._api_key} if self._api_key else {}
        async with httpx.AsyncClient(timeout=httpx.Timeout(120 if self.provider=='Ollama' else 60,connect=10),
                                     trust_env=False,follow_redirects=False) as client:
            if self.provider == 'Ollama':
                metadata = await client.post(self.base_url.removesuffix('/v1')+'/api/show',json={'model':self.model})
                metadata.raise_for_status()
                info = metadata.json()
                if (not isinstance(info,dict) or info.get('remote_host') or info.get('remote_model')
                        or 'completion' not in (info.get('capabilities') or [])):
                    raise ValueError('Choose a downloaded local text model for Ollama notes')
            target = (self.base_url.removesuffix('/v1')+'/api/chat' if self.provider=='Ollama'
                else self.base_url+('/responses' if self.openai else '/chat/completions'))
            async with client.stream('POST',target,
                    headers=headers,json=body) as response:
                response.raise_for_status()
                data = bytearray()
                async for block in response.aiter_bytes():
                    data.extend(block)
                    if len(data) > 200_000:
                        raise ValueError('Summary response too large')
        value = json.loads(data)
        if self.provider == 'Ollama':
            if value.get('done') is not True or value.get('done_reason') != 'stop':
                raise IncompleteSummaryOutput('Local notes response was incomplete; no JSON repair was attempted')
            return value['message']['content']
        if self.openai:
            if value.get('status') != 'completed':
                raise ValueError('Summary response was incomplete')
            parts = [part['text'] for item in value.get('output',[]) if item.get('type') == 'message'
                     for part in item.get('content',[]) if part.get('type') == 'output_text']
            if not parts:
                raise ValueError('Summary response has no text')
            return ''.join(parts)
        if value['choices'][0].get('finish_reason')=='length':
            raise IncompleteSummaryOutput('Notes response reached its output limit')
        return value['choices'][0]['message']['content']
