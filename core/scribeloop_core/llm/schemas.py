"""Pydantic schemas for summaries (Flow 2 step 4 — provisional & final share this)."""
from __future__ import annotations

from pydantic import BaseModel, Field


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
