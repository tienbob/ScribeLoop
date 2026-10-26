"""Pydantic schemas for summaries (Flow 2 step 4 — provisional & final share this)."""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ActionItem(BaseModel):
    task: str
    owner: str | None = None
    due: str | None = Field(default=None, description="ISO date if explicitly stated")


class Decision(BaseModel):
    text: str


class Summary(BaseModel):
    overview: str
    key_points: list[str]
    decisions: list[Decision] = Field(default_factory=list)
    action_items: list[ActionItem] = Field(default_factory=list)
    open_questions: list[str] = Field(default_factory=list)


class Participant(BaseModel):
    """Local label → canonical name after registry matching / user rename."""

    local_label: str
    name: str | None = None


class CitedText(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    text: str = Field(min_length=1,max_length=2000)
    segment_ids: list[str] = Field(min_length=1,max_length=30)


class CitedAction(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True)
    task: str = Field(min_length=1,max_length=2000)
    owner: str | None = Field(default=None,max_length=100)
    due: str | None = Field(default=None,max_length=100)
    segment_ids: list[str] = Field(min_length=1,max_length=30)


class GroundedSummary(BaseModel):
    """Cited extension of the summary sections; output remains untrusted data."""
    model_config = ConfigDict(extra='forbid', strict=True)
    overview: CitedText
    key_points: list[CitedText] = Field(max_length=20)
    decisions: list[CitedText] = Field(max_length=20)
    action_items: list[CitedAction] = Field(max_length=20)
    open_questions: list[CitedText] = Field(max_length=20)
    uncertainties: list[CitedText] = Field(max_length=20)
