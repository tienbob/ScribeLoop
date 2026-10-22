"""macOS capture clients: implemented SCK fallback and planned Core Audio taps.

Only the ScreenCaptureKit helper exists and has local permission/tone checks.
Process taps, microphone capture, shared-clock mapping, and signed distribution
remain pending. Neither class is connected to the recording UI yet.
"""
from __future__ import annotations

from collections.abc import Iterator

from .source import AudioFrame


class MacOSTapSource:
    """Core Audio process-tap source (macOS 14.2+), via the Swift helper."""

    stream = "loopback"

    def __init__(self, pid_or_bundle_ids: list[str] | None = None) -> None:
        """Target specific processes (e.g. the meeting app) or None for system-wide."""
        raise NotImplementedError(
            "Core Audio process taps are not implemented; use the local "
            "ScreenCaptureKit fallback for authorized diagnostics."
        )

    def frames(self) -> Iterator[AudioFrame]:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class MacOSScreenAudioSource:
    """Implemented ScreenCaptureKit fallback (macOS 13+); no mic or video saved.

    Requires Screen Recording permission, unlike the preferred process-tap path.
    A signed app's permission attribution remains a separate host validation.
    """

    stream = 'loopback'

    def __init__(self, bundle_ids: list[str] | None = None, *, helper_path=None,
                 timeout_s: float = 10):
        import sys
        from pathlib import Path

        from .pcm_process import PCMProcessSource

        if sys.platform != 'darwin':
            raise OSError('ScreenCaptureKit requires macOS')
        helper = Path(helper_path or 'data/native/scribeloop-sck').resolve()
        if not helper.is_file():
            raise FileNotFoundError(f'Build the native helper first: {helper}')
        command = [str(helper)]
        for bundle in bundle_ids or []:
            if not bundle or bundle.startswith('-'):
                raise ValueError('Invalid application bundle identifier')
            command.extend(['--bundle-id', bundle])
        self._source = PCMProcessSource(command, timeout_s=timeout_s)

    def frames(self) -> Iterator[AudioFrame]:
        return self._source.frames()

    def monitored_frames(self, on_health) -> Iterator[AudioFrame]:
        return self._source.monitored_frames(on_health)

    def close(self) -> None:
        self._source.close()
