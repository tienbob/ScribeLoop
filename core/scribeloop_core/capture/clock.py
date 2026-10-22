"""Shared host-clock mapping contract for future native multi-stream capture.

Adapters must convert device timestamps into the same monotonic host nanoseconds
before calling this mapper. A device clock or Python receipt time cannot be
substituted. Existing SLP1 adapters are intentionally unchanged until M3.
"""
from dataclasses import dataclass


@dataclass(frozen=True)
class SessionClock:
    origin_host_ns: int

    def __post_init__(self):
        if type(self.origin_host_ns) is not int or self.origin_host_ns < 0:
            raise ValueError('Expected nonnegative integer host nanoseconds')

    def milliseconds(self, capture_host_ns: int) -> int:
        if type(capture_host_ns) is not int or capture_host_ns < self.origin_host_ns:
            raise ValueError('Capture must be on or after the session host-clock origin')
        return (capture_host_ns - self.origin_host_ns + 500_000) // 1_000_000
