"""macOS capture clients: implemented SCK fallback and planned Core Audio taps.

The original SCK helper has local permission/tone checks. The meeting helper adds
AVFoundation mic capture and host-clock mapping, verified with generated signals
only; physical alignment and signed distribution remain pending. An opt-in encrypted five-minute recording test is connected to the local UI.
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


class MacOSMeetingAudioSource:
    """Explicit consented native mic + app/system output; never writes audio files.

    The encrypted development recorder uses this source. Construction alone
    does not open audio; iterating frames starts the native helper.
    """

    stream = 'multi'

    def __init__(self, selection, *, consent_confirmed: bool, helper_path=None, timeout_s=10):
        import sys
        from pathlib import Path

        from ..meetings.contracts import CaptureSelection
        from .multiplexed import MultiplexedPCMSource

        if consent_confirmed is not True:
            raise ValueError('Explicit recording consent is required')
        if sys.platform != 'darwin':
            raise OSError('Native meeting capture requires macOS')
        if not isinstance(selection, CaptureSelection):
            raise TypeError('An explicit validated capture selection is required')
        helper = Path(helper_path or 'data/native/scribeloop-meeting').resolve()
        if not helper.is_file():
            raise FileNotFoundError('Build the meeting capture helper first')
        command = [str(helper), '--consent-confirmed', '--scope', selection.scope,
                   '--microphone-id', selection.microphone_device_id]
        for bundle in selection.bundle_ids:
            command.extend(['--bundle-id', bundle])
        self._source = MultiplexedPCMSource(command, timeout_s=timeout_s)

    def frames(self):
        return self._source.frames()

    def close(self):
        self._source.close()

    def request_stop(self):
        return self._source.request_stop()


class MacOSMicrophoneSource:
    """Mic-only AVFoundation source using the same SLC2/SLP2 clock protocol."""

    stream = 'mic'

    def __init__(self, microphone_id='default', *, consent_confirmed: bool,
                 helper_path=None, timeout_s=10):
        import sys
        from pathlib import Path

        from .multiplexed import MultiplexedPCMSource

        if consent_confirmed is not True:
            raise ValueError('Explicit recording consent is required')
        if sys.platform != 'darwin':
            raise OSError('Native microphone capture requires macOS')
        if not isinstance(microphone_id, str) or not microphone_id.strip() or microphone_id.startswith('-'):
            raise ValueError('Select a microphone explicitly')
        helper = Path(helper_path or 'data/native/scribeloop-meeting').resolve()
        if not helper.is_file():
            raise FileNotFoundError('Build the meeting capture helper first')
        self._source = MultiplexedPCMSource(
            [str(helper), '--consent-confirmed', '--microphone-only', '--microphone-id', microphone_id],
            expected_streams=('mic',), timeout_s=timeout_s)

    def frames(self):
        return self._source.frames()

    def close(self):
        self._source.close()

    def request_stop(self):
        return self._source.request_stop()
