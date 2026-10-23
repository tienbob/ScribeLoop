"""Discovery and future recording boundaries. No PCM or browsing data here."""
from __future__ import annotations

from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator


class MetadataModel(BaseModel):
    model_config = ConfigDict(extra='forbid', frozen=True, strict=True)


class ProcessActivity(MetadataModel):
    pid: int = Field(gt=0)
    bundle_id: str = Field(min_length=1, max_length=256)
    input_running: bool | None
    output_running: bool | None


class ActivitySnapshot(MetadataModel):
    observed_at: float = Field(ge=0, allow_inf_nan=False)
    available: bool
    complete: bool
    processes: tuple[ProcessActivity, ...] = ()

    @model_validator(mode='after')
    def valid(self):
        if not self.available and (self.complete or self.processes):
            raise ValueError('Unavailable observer cannot provide activity')
        return self


class AppIdentity(MetadataModel):
    app_id: str = Field(min_length=1, max_length=256)
    name: str = Field(min_length=1, max_length=256)
    kind: Literal['meeting', 'browser']
    bundle_ids: tuple[str, ...] = Field(min_length=1)


class MeetingCandidate(MetadataModel):
    id: str
    app_id: str
    name: str
    kind: Literal['meeting', 'browser']
    evidence: tuple[Literal['input_active', 'output_active'], ...]
    confidence: Literal['possible', 'ambiguous']
    first_seen: float
    last_seen: float


class CaptureSelection(MetadataModel):
    """Explicit scope; an empty app selection never means whole-system."""

    scope: Literal['application', 'system', 'microphone']
    bundle_ids: tuple[str, ...] = ()
    microphone_device_id: str = Field(min_length=1, max_length=256)

    @model_validator(mode='after')
    def valid(self):
        if not self.microphone_device_id.strip() or self.microphone_device_id.startswith('-'):
            raise ValueError('Microphone selection must not be blank')
        if (self.scope == 'application') != bool(self.bundle_ids):
            raise ValueError('Application scope requires bundles; system/microphone scopes forbid them')
        if any(not item.strip() or item.startswith('-') for item in self.bundle_ids):
            raise ValueError('Invalid bundle identifier')
        if len(set(self.bundle_ids)) != len(self.bundle_ids):
            raise ValueError('Duplicate bundle identifier')
        return self


class RecordingState(str, Enum):
    """Planned recorder states; not yet wired into persistence."""

    PREPARING = 'preparing'
    RECORDING = 'recording'
    STOPPING = 'stopping'
    COMPLETED = 'completed'
    FAILED = 'failed'
    INTERRUPTED = 'interrupted'
