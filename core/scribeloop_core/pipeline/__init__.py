"""Pipeline: typed event bus, VAD gate, streaming ASR, diarization, attribution.

Stage contract (ARCHITECTURE.md §2 'Event-driven core'): each stage is a
component that publishes/subscribes typed events; every stage is testable
with recorded audio (WavFileSource) and movable to a separate process later.
"""
