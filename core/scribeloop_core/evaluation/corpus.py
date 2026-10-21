"""A corpus manifest references local files; recordings never belong in Git."""
from __future__ import annotations

import json
from pathlib import Path
from typing import Literal

import soundfile as sf
from pydantic import BaseModel, ConfigDict, Field, model_validator


class Recording(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    source: Literal["real", "ami", "voxconverse", "synthetic"]
    audio: Path
    reference: Path
    speaker_labels: Path
    consent_confirmed: bool = False
    consent_record: Path | None = None
    provenance: str = Field(min_length=1)
    variant_of: str | None = None
    codec: str | None = None

    @model_validator(mode="after")
    def consent_and_variant(self) -> Recording:
        if self.source == "real" and (not self.consent_confirmed or not self.consent_record):
            raise ValueError("real meetings require confirmed consent and a local consent record")
        if (self.variant_of is None) != (self.codec is None):
            raise ValueError("codec and variant_of must be supplied together")
        return self


class Corpus(BaseModel):
    model_config = ConfigDict(extra="forbid")

    version: Literal[1]
    recordings: list[Recording] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_ids(self) -> Corpus:
        by_id = {item.id: item for item in self.recordings}
        if len(by_id) != len(self.recordings):
            raise ValueError("recording IDs must be unique")
        for item in self.recordings:
            if item.variant_of is not None:
                parent = by_id.get(item.variant_of)
                if parent is None or parent.id == item.id or parent.variant_of is not None:
                    raise ValueError(f"{item.id}: variant must reference an original recording")
                if parent.source != item.source:
                    raise ValueError(f"{item.id}: variant source must match its original")
        return self


def load_corpus(path: Path) -> Corpus:
    corpus = Corpus.model_validate_json(path.read_text(encoding="utf-8"))
    for item in corpus.recordings:
        for field in ("audio", "reference", "speaker_labels", "consent_record"):
            value = getattr(item, field)
            if value is not None:
                resolved = (path.parent / value).resolve()
                if not resolved.is_file():
                    raise ValueError(f"{item.id}: missing {field}: {resolved}")
                setattr(item, field, resolved)
        info = sf.info(item.audio)
        if info.frames == 0 or info.samplerate <= 0:
            raise ValueError(f"{item.id}: audio must be nonempty")
        if not item.reference.read_text(encoding="utf-8").strip():
            raise ValueError(f"{item.id}: reference must contain a corrected transcript")
        turns = json.loads(item.speaker_labels.read_text(encoding="utf-8"))
        if not isinstance(turns, list) or not turns:
            raise ValueError(f"{item.id}: speaker labels must be a nonempty list")
        for turn in turns:
            if (not isinstance(turn, dict) or not isinstance(turn.get("speaker"), str)
                    or not turn["speaker"].strip()
                    or type(turn.get("start_ms")) is not int
                    or type(turn.get("end_ms")) is not int
                    or not 0 <= turn["start_ms"] < turn["end_ms"] <= info.duration * 1000 + 1):
                raise ValueError(f"{item.id}: invalid speaker turn: {turn}")
    return corpus


def readiness(corpus: Corpus) -> dict:
    originals = [item for item in corpus.recordings if item.variant_of is None]
    real = sum(item.source == "real" for item in originals)
    public = {item.source for item in originals}
    degraded = {item.variant_of for item in corpus.recordings if item.codec == "opus"}
    missing = [item.id for item in originals if item.id not in degraded]
    return {"real_meetings": real, "required_real_meetings": 10,
            "has_ami": "ami" in public, "has_voxconverse": "voxconverse" in public,
            "missing_opus_variants": missing,
            "corpus_ready": real >= 10 and {"ami", "voxconverse"} <= public and not missing}
