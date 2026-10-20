"""Tool-calling agent loop (Phase 3 — ARCHITECTURE.md §5).

Rules baked in from day one:
- Transcripts AND summaries/action_items are UNTRUSTED input (first- and
  second-order injection): tool arguments derived from them are data,
  never user intent.
- Every mutating tool call requires explicit user approval with the FULL
  argument payload visible before execution (confirmation UI).
- Tool-specific defaults: calendar.create_event disallows attendees by
  default; sessions.search is internal-only.
- Per-session action caps + events-table audit log.
"""
from __future__ import annotations


class AgentLoop:
    def __init__(self) -> None:
        raise NotImplementedError("Phase 3 — see ARCHITECTURE.md §5")
