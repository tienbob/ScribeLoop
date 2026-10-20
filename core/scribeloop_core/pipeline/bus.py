"""Typed in-process event bus (ARCHITECTURE.md §2, §2.3).

asyncio queues for now; the same publish/subscribe surface moves to
Redis/NATS if stages split into processes.

Backpressure rule (§2.3): publish never blocks the publisher (capture has the
highest priority). If a subscriber's queue is full, the event is **dropped for
that subscriber only** — ASR input is bounded by the session recorder, not by
queue size, and critical events (session lifecycle) use small bounded queues
that must keep up.
"""
from __future__ import annotations

import asyncio
import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import AsyncIterator, TypeVar

log = logging.getLogger(__name__)

E = TypeVar("E", bound="Event")


# --------------------------------------------------------------------------
# Event types (skeleton set — stages define their own as they grow)
# --------------------------------------------------------------------------

@dataclass(frozen=True, kw_only=True)
class Event:
    """Base event; ``ts`` is ``time.monotonic()`` at publish."""

    ts: float = field(default_factory=time.monotonic)


@dataclass(frozen=True, kw_only=True)
class SessionStarted(Event):
    session_id: str


@dataclass(frozen=True, kw_only=True)
class SessionStopped(Event):
    session_id: str


@dataclass(frozen=True, kw_only=True)
class SpeechSpan(Event):
    """VAD: a speech region began/ended (sample indices on the recorder timeline)."""

    kind: str  # 'start' | 'end'
    sample: int


@dataclass(frozen=True, kw_only=True)
class TranscriptChunk(Event):
    """ASR output. ``is_final`` False = unstable partial (ephemeral, §3.1)."""

    stream: str
    text: str
    start_ms: int
    end_ms: int
    is_final: bool
    language: str | None = None
    confidence: float | None = None


@dataclass(frozen=True, kw_only=True)
class AttributedSegment(Event):
    """Transcript text with a local speaker label (Flow 1 step 6)."""

    local_speaker: str
    stream: str
    text: str
    start_ms: int
    end_ms: int
    revision: int = 1  # 1=confirmed, 2=offline-corrected (0 partials never persist)


@dataclass(frozen=True, kw_only=True)
class SummaryUpdated(Event):
    """Rolling or final summary state changed (Flow 1 step 9 / Flow 2)."""

    session_id: str
    kind: str  # 'rolling' | 'provisional' | 'final'
    content_md: str


# --------------------------------------------------------------------------
# Bus
# --------------------------------------------------------------------------

class _Subscription:
    """Async iterator over one subscriber's queue; unsubscribe on aclose()."""

    def __init__(self, queue: asyncio.Queue, bus: "EventBus", event_type: type) -> None:
        self._queue = queue
        self._bus = bus
        self._event_type = event_type
        self._closed = False

    def __aiter__(self) -> "_Subscription":
        return self

    async def __anext__(self) -> Event:
        if self._closed:
            raise StopAsyncIteration
        item = await self._queue.get()
        if item is _SENTINEL:
            self._closed = True
            raise StopAsyncIteration
        return item  # type: ignore[return-value]

    async def aclose(self) -> None:
        if not self._closed:
            self._closed = True
            self._bus._unsubscribe(self._event_type, self._queue)


_SENTINEL = object()


class EventBus:
    """In-process pub/sub. ``subscribe`` returns an async iterator."""

    def __init__(self, maxsize: int = 1024) -> None:
        self._subs: dict[type, set[asyncio.Queue]] = defaultdict(set)
        self._maxsize = maxsize

    def subscribe(self, event_type: type[E]) -> AsyncIterator[E]:
        queue: asyncio.Queue = asyncio.Queue(maxsize=self._maxsize)
        self._subs[event_type].add(queue)
        return _Subscription(queue, self, event_type)  # type: ignore[return-value]

    async def publish(self, event: Event) -> None:
        """Fan out; never blocks. Full subscriber queue ⇒ drop for that subscriber."""
        for queue in list(self._subs[type(event)]):
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                log.warning(
                    "bus: dropping %s for a slow subscriber (backpressure §2.3)",
                    type(event).__name__,
                )

    def _unsubscribe(self, event_type: type, queue: asyncio.Queue) -> None:
        subs = self._subs.get(event_type)
        if subs and queue in subs:
            subs.remove(queue)
            try:
                queue.put_nowait(_SENTINEL)
            except asyncio.QueueFull:
                pass
