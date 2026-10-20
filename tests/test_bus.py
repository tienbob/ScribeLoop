"""Event bus tests (§2, §2.3 backpressure)."""
import asyncio

from scribeloop_core.pipeline.bus import EventBus, SessionStarted, TranscriptChunk


async def test_pubsub_roundtrip():
    bus = EventBus()
    sub = bus.subscribe(SessionStarted)
    await bus.publish(SessionStarted(session_id="s1"))  # type: ignore[arg-type]
    event = await asyncio.wait_for(sub.__anext__(), timeout=1.0)
    assert event.session_id == "s1"
    await sub.aclose()


async def test_isolation_by_type():
    bus = EventBus()
    session_sub = bus.subscribe(SessionStarted)
    chunk_sub = bus.subscribe(TranscriptChunk)
    await bus.publish(SessionStarted(session_id="s1"))  # type: ignore[arg-type]
    await bus.publish(TranscriptChunk(stream="loopback", text="hi", start_ms=0,
                                      end_ms=900, is_final=True))
    a = await asyncio.wait_for(session_sub.__anext__(), timeout=1.0)
    b = await asyncio.wait_for(chunk_sub.__anext__(), timeout=1.0)
    assert isinstance(a, SessionStarted) and isinstance(b, TranscriptChunk)
    await session_sub.aclose()
    await chunk_sub.aclose()


async def test_full_queue_drops_not_blocks():
    """§2.3: publish never blocks; full subscriber queue => drop for it."""
    bus = EventBus(maxsize=2)
    slow = bus.subscribe(TranscriptChunk)
    for i in range(10):
        await bus.publish(TranscriptChunk(stream="loopback", text=str(i),
                                          start_ms=0, end_ms=1, is_final=True))
    received = 0
    while not slow._queue.empty():
        await slow.__anext__()
        received += 1
    assert received == 2  # queue size, not 10; publisher never blocked
    await slow.aclose()
