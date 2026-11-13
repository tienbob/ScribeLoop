"""Desktop settings backed by the native secret store; no .env discovery."""
from __future__ import annotations

import contextlib
import json
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from ..llm.summary_provider import SummaryProvider
from ..pipeline.assemblyai_meeting import STREAMING_LANGUAGES, AssemblyAIConfig
from ..pipeline.deepgram_meeting import DeepgramConfig
from ..pipeline.languages import AUTO_LANGUAGES, HINT_LANGUAGE_NAMES, LanguageHints
from ..pipeline.transcription_quality import TranscriptionQuality
from ..storage.keys import KeyUnavailable

SERVICE = 'ai.scribeloop.providers.v1'
ACCOUNT = 'desktop-settings'
SUMMARY_PRESETS = {
    'OpenAI': 'https://api.openai.com/v1',
    'Gemini': 'https://generativelanguage.googleapis.com/v1beta/openai',
    'Ollama': 'http://localhost:11434/v1',
    'LM Studio': 'http://localhost:1234/v1',
}
OLLAMA_ENDPOINT = 'http://localhost:11434/v1'


class Settings(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    detection_enabled: bool = True
    automatic_notes: bool = True
    summary_key: str = Field(default='', max_length=2048, repr=False)
    assemblyai_key: str = Field(default='', max_length=512, repr=False)
    deepgram_key: str = Field(default='', max_length=512, repr=False)
    summary_endpoint: str = Field(default='https://api.openai.com/v1', max_length=500)
    summary_model: str = Field(default='gpt-5.4-mini', min_length=1, max_length=100)
    summary_provider: str = Field(default='OpenAI', min_length=1, max_length=80)
    summary_instructions: str = Field(default='', max_length=2000)
    summary_keys: dict[str, str] = Field(default_factory=dict, repr=False)
    summary_models: dict[str, str] = Field(default_factory=dict, repr=False)
    language: str = 'auto'
    language_hints: LanguageHints | None = None
    duration_s: int = 3600
    stop_on_microphone_release: bool = True
    vocabulary: list[str] = Field(default_factory=list, max_length=50)
    asr_microphone_source: Literal['processed', 'raw'] = 'processed'
    final_transcription: bool = False
    transcription_engine: Literal['deepgram', 'assemblyai', 'ollama'] = 'deepgram'
    local_transcription_endpoint: str = Field(default=OLLAMA_ENDPOINT, max_length=200)
    local_transcription_model: str = Field(default='', max_length=128)
    local_speaker_labels: bool = False
    local_transcript_cleanup: bool = True
    local_cleanup_model: str = Field(default='', max_length=128)
    local_cleanup_provider: str = Field(default='Ollama', min_length=1, max_length=80)
    local_cleanup_endpoint: str = Field(default='', max_length=200)
    local_cleanup_validate: bool = True
    local_cleanup_key: str = Field(default='', max_length=2048, repr=False)
    local_cleanup_keys: dict[str, str] = Field(default_factory=dict, repr=False)
    local_cleanup_models: dict[str, str] = Field(default_factory=dict, repr=False)

    @field_validator('vocabulary')
    @classmethod
    def vocabulary_valid(cls, value):
        return TranscriptionQuality(vocabulary=value).vocabulary
    microphone_gain_db: float = Field(default=0., ge=-12, le=24, allow_inf_nan=False)
    microphone_noise_filter: Literal['off','standard','strong'] = 'standard'
    microphone_noise_threshold_db: float = Field(default=-40., ge=-65, le=-25, allow_inf_nan=False)

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

    @field_validator('summary_key', 'deepgram_key', 'assemblyai_key', 'local_cleanup_key')
    @classmethod
    def secret_valid(cls, value):
        if value and (not value.isascii() or any(c.isspace() for c in value)):
            raise ValueError('Invalid API key')
        return value

    @field_validator('summary_keys', 'local_cleanup_keys')
    @classmethod
    def summary_keys_valid(cls, value):
        """Per-provider saved keys; switching providers restores the matching one."""
        if not isinstance(value, dict) or len(value) > 12:
            raise ValueError('Invalid saved provider keys')
        return {name: key for name, key in value.items()
                if isinstance(name, str) and len(name) <= 80 and isinstance(key, str) and key
                and key.isascii() and not any(c.isspace() for c in key) and len(key) <= 2048}

    @field_validator('summary_models')
    @classmethod
    def summary_models_valid(cls, value):
        """Per-provider saved notes models; model IDs are not secret."""
        if not isinstance(value, dict) or len(value) > 12:
            raise ValueError('Invalid saved provider models')
        return {name: model for name, model in value.items()
                if isinstance(name, str) and len(name) <= 80
                and isinstance(model, str) and len(model) <= 100
                and not any(ord(c) < 32 for c in model)}

    @field_validator('local_cleanup_models')
    @classmethod
    def cleanup_models_valid(cls, value):
        """Per-provider saved cleanup models; model IDs are not secret."""
        if not isinstance(value, dict) or len(value) > 12:
            raise ValueError('Invalid saved cleanup models')
        return {name: model for name, model in value.items()
                if isinstance(name, str) and len(name) <= 80
                and isinstance(model, str) and len(model) <= 128
                and not any(ord(c) < 32 for c in model)}

    @field_validator('local_cleanup_provider')
    @classmethod
    def cleanup_provider_valid(cls, value):
        if value not in (*SUMMARY_PRESETS, 'Custom'):
            raise ValueError('Unsupported cleanup provider')
        return value

    @field_validator('deepgram_key','assemblyai_key')
    @classmethod
    def transcription_secret_valid(cls, value):
        if value and len(value) < 16:
            raise ValueError('Invalid transcription API key')
        return value

    @model_validator(mode='after')
    def local_engine_consistent(self):
        if self.transcription_engine == 'assemblyai' and self.language_hints and any(code not in STREAMING_LANGUAGES for code in self.language_hints):
            raise ValueError('Choose languages supported by AssemblyAI streaming')
        if self.transcription_engine == 'ollama' and not self.local_transcription_model.strip():
            raise ValueError('Choose an Ollama transcription model')
        return self

    def cleanup(self):
        """Cleanup engine: independent of the notes provider.

        Any of the notes providers may be chosen, on-device (Ollama, LM
        Studio) or cloud (OpenAI, Gemini, any OpenAI-compatible endpoint).
        Choosing a cloud provider sends transcript text to that provider;
        audio is never uploaded by this step. An explicit cleanup model
        wins. When unset, that provider's saved notes model is reused even
        when notes currently use another provider. The key comes from the
        per-provider cleanup key store, falling back to the saved notes key
        only when its provider endpoint matches the cleanup destination.
        """
        if not self.local_transcript_cleanup:
            return None
        name = self.local_cleanup_provider
        model = self.local_cleanup_model.strip()
        if not model and name == self.summary_provider:
            model = self.summary_model.strip()
        elif not model:
            model = self.summary_models.get(name,'').strip()
        if not model:
            return None
        endpoint = (self.local_cleanup_endpoint.strip()
            or SUMMARY_PRESETS.get(name) or OLLAMA_ENDPOINT)
        # Credentials belong to a provider/destination, not the currently active
        # notes selection. Never share a notes key with a different custom URL.
        shared_key = ''
        notes_endpoint = self.summary_endpoint if name == self.summary_provider else SUMMARY_PRESETS.get(name,'')
        if notes_endpoint and endpoint.rstrip('/') == notes_endpoint.rstrip('/'):
            shared_key = (self.summary_key if name == self.summary_provider else self.summary_keys.get(name,''))
        key = self.local_cleanup_keys.get(name) or shared_key
        provider = SummaryProvider(settings={'SUMMARY_API_KEY': key,
            'SUMMARY_BASE_URL': endpoint, 'SUMMARY_MODEL': model,
            'SUMMARY_PROVIDER': name, 'SUMMARY_INSTRUCTIONS': ''})
        # The validation skip is an experimental Experience toggle; it travels
        # with the cleanup engine instance, not with summaries.
        provider.cleanup_validate = self.local_cleanup_validate
        provider.cleanup_vocabulary = tuple(self.vocabulary)
        return provider if provider.configured else None

    def local_transcription(self):
        """Valid local engine configuration; raises when the saved settings are unusable."""
        from ..pipeline.local_transcription import LocalTranscriptionConfig
        if self.transcription_engine != 'ollama':
            return None
        config = LocalTranscriptionConfig(model=self.local_transcription_model,
            endpoint=self.local_transcription_endpoint or None,
            speaker_labels=self.local_speaker_labels,
            cleanup_provider=self.cleanup())
        if not config.configured:
            raise ValueError('Choose a valid Ollama endpoint and transcription model name')
        return config

    def summary(self):
        return SummaryProvider(settings={'SUMMARY_API_KEY': self.summary_key,
            'SUMMARY_BASE_URL': self.summary_endpoint, 'SUMMARY_MODEL': self.summary_model,
            'SUMMARY_PROVIDER': self.summary_provider,'SUMMARY_INSTRUCTIONS':self.summary_instructions})

    @staticmethod
    def _key_preview(key):
        return ((key[:4] if len(key) > 4 else '')+'********') if key else ''

    def public(self):
        local = None
        if self.transcription_engine == 'ollama':
            with contextlib.suppress(ValueError):
                local = self.local_transcription()
        cleanup = self.cleanup()
        return {**self.model_dump(exclude={'summary_key','deepgram_key','assemblyai_key','summary_keys',
                    'local_cleanup_key','local_cleanup_keys'}),
                'language_hints':self.language_hints or (list(AUTO_LANGUAGES) if self.language=='auto' else [self.language]),
                'supported_language_hints':HINT_LANGUAGE_NAMES,
                'transcription_configured': bool(self.assemblyai_key if self.transcription_engine == 'assemblyai' else self.deepgram_key),
                'assemblyai_configured': bool(self.assemblyai_key),
                'deepgram_configured': bool(self.deepgram_key),
                'local_transcription_configured': local.configured if local else False,
                'local_cleanup_configured': bool(cleanup and cleanup.configured),
                'local_cleanup_provider': self.local_cleanup_provider,
                'local_cleanup_endpoint': (self.local_cleanup_endpoint.strip()
                    or SUMMARY_PRESETS.get(self.local_cleanup_provider) or OLLAMA_ENDPOINT),
                'summary_key_preview': self._key_preview(self.summary_key),
                'summary_key_previews': {name:self._key_preview(key) for name,key in self.summary_keys.items()},
                'summary_models': dict(self.summary_models),
                'local_cleanup_key_preview': self._key_preview(self.local_cleanup_keys.get(self.local_cleanup_provider,'')),
                'local_cleanup_key_previews': {name:self._key_preview(key) for name,key in self.local_cleanup_keys.items()},
                'summary_configured': self.summary().configured,
                'summary_presets': SUMMARY_PRESETS}


class DesktopSettings:
    def __init__(self, backend=None, *, transcription_key="", default_detection_enabled=True):
        if backend is None:
            from keyring.backends.macOS import Keyring
            backend = Keyring()
        self._cloud = DeepgramConfig(api_key=transcription_key, budget_usd=.25, max_requests=160)
        self.backend = backend
        self.default_detection_enabled = default_detection_enabled
        self.value = self.load()

    def load(self):
        try:
            raw = self.backend.get_password(SERVICE, ACCOUNT)
            if raw is not None and len(raw.encode()) > 16384:
                raise ValueError('Settings too large')
            value = json.loads(raw) if raw else {}
            if not isinstance(value,dict):
                raise TypeError('Invalid settings')
            from ..pipeline.languages import LEGACY_DESKTOP_LANGUAGES
            if value.get('language') in LEGACY_DESKTOP_LANGUAGES:
                value['language'] = 'auto'
            if value.get('summary_key'):
                # Legacy single-key settings migrate into the per-provider map.
                keys = value.get('summary_keys')
                value['summary_keys'] = {**keys, value.get('summary_provider') or 'OpenAI': value['summary_key']} \
                    if isinstance(keys,dict) else {value.get('summary_provider') or 'OpenAI': value['summary_key']}
            if value.get('summary_model'):
                models = value.get('summary_models')
                value['summary_models'] = {**models, value.get('summary_provider') or 'OpenAI': value['summary_model']} \
                    if isinstance(models,dict) else {value.get('summary_provider') or 'OpenAI': value['summary_model']}
            return Settings.model_validate(
                {'detection_enabled': self.default_detection_enabled, **value})
        except Exception:  # noqa: BLE001 - native errors can include secret data
            raise KeyUnavailable('Provider settings could not be unlocked. No fallback was used.') from None

    def cloud(self):
        quality = TranscriptionQuality(vocabulary=self.value.vocabulary,
            asr_microphone_source=self.value.asr_microphone_source,
            final_transcription=self.value.final_transcription if self.value.transcription_engine == 'deepgram' else False)
        deepgram = DeepgramConfig(api_key=self.value.deepgram_key or self._cloud.api_key,
            connector=self._cloud.connector,batch_transport=self._cloud.batch_transport,
            budget_usd=self._cloud.budget_usd,max_requests=self._cloud.max_requests,quality=quality)
        assembly = AssemblyAIConfig(api_key=self.value.assemblyai_key,budget_usd=.25,max_requests=160,
            quality=quality.model_copy(update={'final_transcription':False}))
        selected = assembly if self.value.transcription_engine == 'assemblyai' else deepgram
        selected.alternatives = {'deepgram':deepgram,'assemblyai':assembly}
        return selected

    def prepare(self, patch):
        if not isinstance(patch, dict) or len(json.dumps(patch,ensure_ascii=False).encode()) > 12288:
            raise ValueError('Invalid settings')
        patch = dict(patch)
        if 'language' in patch and 'language_hints' not in patch:
            patch['language_hints'] = None
        provider = patch.get('summary_provider', self.value.summary_provider)
        if isinstance(provider,str) and provider in SUMMARY_PRESETS and 'summary_provider' in patch:
            patch['summary_endpoint'] = SUMMARY_PRESETS[provider]
        if (isinstance(provider,str) and 'summary_model' not in patch
                and provider != self.value.summary_provider):
            # Notes provider switch without an explicit model: restore that
            # provider's saved model so switching needs no re-entry.
            saved_model = self.value.summary_models.get(provider,'')
            if saved_model or provider == 'OpenAI':
                patch['summary_model'] = saved_model or 'gpt-5.4-mini'
        destination = patch.get('summary_endpoint', self.value.summary_endpoint)
        if 'summary_key' not in patch:
            if (isinstance(provider,str) and provider != self.value.summary_provider
                    and provider in SUMMARY_PRESETS):
                # Provider switch between known presets: restore that provider's
                # saved key. Never reuse another provider's key on a new endpoint.
                patch['summary_key'] = self.value.summary_keys.get(provider,'')
            elif (isinstance(destination,str) and
                    destination.rstrip('/') != self.value.summary_endpoint.rstrip('/')):
                # Custom endpoint change without a new key: start blank so a saved
                # key is never sent to an endpoint it was not created for.
                patch['summary_key'] = ''
        cleanup_provider = patch.get('local_cleanup_provider', self.value.local_cleanup_provider)
        cleanup_endpoint = patch.get('local_cleanup_endpoint', self.value.local_cleanup_endpoint)
        if 'local_cleanup_key' not in patch:
            if (isinstance(cleanup_provider,str) and cleanup_provider != self.value.local_cleanup_provider
                    and cleanup_provider in SUMMARY_PRESETS):
                # Cleanup provider switch between known presets: restore that
                # provider's saved key, mirroring the notes key behavior.
                patch['local_cleanup_key'] = self.value.local_cleanup_keys.get(cleanup_provider,'')
            elif (isinstance(cleanup_endpoint,str) and cleanup_provider == 'Custom'
                    and cleanup_endpoint.rstrip('/') != self.value.local_cleanup_endpoint.rstrip('/')):
                # Custom endpoint change without a new key: start blank so a saved
                # key is never sent to an endpoint it was not created for.
                patch['local_cleanup_key'] = ''
        if 'local_cleanup_model' not in patch and isinstance(cleanup_provider,str) \
                and cleanup_provider != self.value.local_cleanup_provider:
            # Provider switch without an explicit model: restore that provider's
            # saved model so switching providers needs no re-entry.
            patch['local_cleanup_model'] = self.value.local_cleanup_models.get(cleanup_provider,'')
        candidate = Settings.model_validate(self.value.model_dump() | patch)
        if candidate.summary_key and candidate.summary_keys.get(candidate.summary_provider) != candidate.summary_key:
            # Remember the key under its provider so later switches restore it.
            candidate = candidate.model_copy(update={'summary_keys':
                {**candidate.summary_keys, candidate.summary_provider: candidate.summary_key}})
        if candidate.summary_models.get(candidate.summary_provider) != candidate.summary_model:
            # Remember the notes model under its provider too.
            candidate = candidate.model_copy(update={'summary_models':
                {**candidate.summary_models, candidate.summary_provider: candidate.summary_model}})
        if candidate.local_cleanup_key and candidate.local_cleanup_keys.get(candidate.local_cleanup_provider) != candidate.local_cleanup_key:
            # Remember the cleanup key under its provider as well.
            candidate = candidate.model_copy(update={'local_cleanup_keys':
                {**candidate.local_cleanup_keys, candidate.local_cleanup_provider: candidate.local_cleanup_key}})
        cleanup_model = candidate.local_cleanup_model.strip()
        if cleanup_model and candidate.local_cleanup_models.get(candidate.local_cleanup_provider) != cleanup_model:
            # Remember the cleanup model under its provider too.
            candidate = candidate.model_copy(update={'local_cleanup_models':
                {**candidate.local_cleanup_models, candidate.local_cleanup_provider: cleanup_model}})
        # Validate URL without a paid request; blank keys are allowed for staged setup.
        probe = candidate.model_copy(update={'summary_key': candidate.summary_key or 'validation-only'})
        if not probe.summary().configured:
            raise ValueError('Invalid summary endpoint/model')
        if candidate.transcription_engine == 'ollama':
            candidate.local_transcription()  # Raises ValueError on an unusable endpoint/model.
        return candidate

    def persist(self, candidate):
        """Blocking Keychain I/O only; publish the new value on the owning loop."""
        try:
            encoded = candidate.model_dump_json()
            self.backend.set_password(SERVICE, ACCOUNT, encoded)
            if self.backend.get_password(SERVICE, ACCOUNT) != encoded:
                raise KeyUnavailable('Settings verification failed')
        except Exception:  # noqa: BLE001 - never expose backend error text
            raise KeyUnavailable('Provider settings could not be saved.') from None

    def save(self, patch):
        candidate = self.prepare(patch)
        self.persist(candidate)
        self.value = candidate
        return candidate.public()
