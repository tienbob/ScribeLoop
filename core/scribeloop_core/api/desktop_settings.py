"""Desktop-only Keychain settings; no environment or repository .env discovery."""
from __future__ import annotations

import json

from pydantic import BaseModel, ConfigDict, Field, field_validator

from ..llm.summary_provider import SummaryProvider
from ..pipeline.deepgram_meeting import DeepgramConfig
from ..storage.keys import KeyUnavailable

SERVICE = 'ai.scribeloop.providers.v1'
ACCOUNT = 'desktop-settings'
SUMMARY_PRESETS = {
    'OpenAI': 'https://api.openai.com/v1',
    'Gemini': 'https://generativelanguage.googleapis.com/v1beta/openai',
    'Ollama': 'http://localhost:11434/v1',
    'LM Studio': 'http://localhost:1234/v1',
}


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    summary_key: str = Field(default='', max_length=2048, repr=False)
    summary_endpoint: str = Field(default='https://api.openai.com/v1', max_length=500)
    summary_model: str = Field(default='gpt-5.4-mini', min_length=1, max_length=100)
    summary_provider: str = Field(default='OpenAI', min_length=1, max_length=80)
    language: str = 'auto'
    duration_s: int = 3600

    @field_validator('language')
    @classmethod
    def language_valid(cls, value):
        from ..pipeline.languages import LANGUAGE_CHOICES
        if value not in LANGUAGE_CHOICES:
            raise ValueError('Unsupported language')
        return value

    @field_validator('duration_s')
    @classmethod
    def duration_valid(cls, value):
        if value not in {300, 3600, 10800}:
            raise ValueError('Unsupported duration')
        return value

    @field_validator('summary_key')
    @classmethod
    def secret_valid(cls, value):
        if value and (not value.isascii() or any(c.isspace() for c in value)):
            raise ValueError('Invalid API key')
        return value

    def summary(self):
        return SummaryProvider(settings={'SUMMARY_API_KEY': self.summary_key,
            'SUMMARY_BASE_URL': self.summary_endpoint, 'SUMMARY_MODEL': self.summary_model,
            'SUMMARY_PROVIDER': self.summary_provider})

    def public(self):
        return {**self.model_dump(exclude={'summary_key'}),
                'summary_configured': self.summary().configured,
                'summary_presets': SUMMARY_PRESETS}


class DesktopSettings:
    def __init__(self, backend=None, *, transcription_key=""):
        if backend is None:
            from keyring.backends.macOS import Keyring
            backend = Keyring()
        self._cloud = DeepgramConfig(api_key=transcription_key, budget_usd=.25, max_requests=160)
        self.backend = backend
        self.value = self.load()

    def load(self):
        try:
            raw = self.backend.get_password(SERVICE, ACCOUNT)
            if raw is not None and len(raw) > 8192:
                raise ValueError('Settings too large')
            value = json.loads(raw) if raw else {}
            if not isinstance(value,dict):
                raise TypeError('Invalid settings')
            value.pop('deepgram_key',None)  # Legacy user key is never used or retained on save.
            return Settings.model_validate(value)
        except Exception:  # noqa: BLE001 - native errors can include secret data
            raise KeyUnavailable('Provider settings could not be unlocked. No fallback was used.') from None

    def cloud(self):
        return self._cloud

    def save(self, patch):
        if not isinstance(patch, dict) or len(json.dumps(patch)) > 8192:
            raise ValueError('Invalid settings')
        patch = dict(patch)
        provider = patch.get('summary_provider', self.value.summary_provider)
        if isinstance(provider,str) and provider in SUMMARY_PRESETS and 'summary_provider' in patch:
            patch['summary_endpoint'] = SUMMARY_PRESETS[provider]
        destination = patch.get('summary_endpoint', self.value.summary_endpoint)
        if (isinstance(destination,str) and
                destination.rstrip('/') != self.value.summary_endpoint.rstrip('/') and
                'summary_key' not in patch):
            patch['summary_key'] = ''
        candidate = Settings.model_validate(self.value.model_dump() | patch)
        # Validate URL without a paid request; blank keys are allowed for staged setup.
        probe = candidate.model_copy(update={'summary_key': candidate.summary_key or 'validation-only'})
        if not probe.summary().configured:
            raise ValueError('Invalid summary endpoint/model')
        try:
            encoded = candidate.model_dump_json()
            self.backend.set_password(SERVICE, ACCOUNT, encoded)
            if self.backend.get_password(SERVICE, ACCOUNT) != encoded:
                raise KeyUnavailable('Settings verification failed')
        except Exception:  # noqa: BLE001 - never expose backend error text
            raise KeyUnavailable('Provider settings could not be saved.') from None
        self.value = candidate
        return candidate.public()
