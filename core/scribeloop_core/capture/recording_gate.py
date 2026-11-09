"""Exclude paused PCM before storage/ASR, using one sample clock for both tracks."""
from bisect import bisect_right
from dataclasses import replace
from itertools import chain
from threading import Lock

from .source import SAMPLE_RATE, sample_position


class RecordingGate:
    MAX_INTERVALS = 16_384

    def __init__(self):
        self._lock = Lock()
        self._watermark = 0
        self._intervals = []
        self._ends = []
        self._removed = []
        self._paused_at = None

    @property
    def paused(self):
        with self._lock:
            return self._paused_at is not None

    def set_paused(self, paused):
        with self._lock:
            if paused and self._paused_at is None:
                if (len(self._intervals)>=self.MAX_INTERVALS
                        and self._intervals[-1][1]!=self._watermark):
                    raise ValueError('Pause limit reached for this recording; recording continues')
                self._paused_at = self._watermark
            elif not paused and self._paused_at is not None:
                left,right=self._paused_at,self._watermark
                if right>left:
                    if self._intervals and self._intervals[-1][1]==left:
                        old_left,_=self._intervals[-1]
                        self._intervals[-1]=(old_left,right)
                        self._ends[-1]=right
                        self._removed[-1]+=right-left
                    else:
                        self._intervals.append((left,right))
                        self._ends.append(right)
                        self._removed.append((self._removed[-1] if self._removed else 0)+right-left)
                self._paused_at = None

    def filter(self, frame):
        """Trim boundary packets and collapse pauses identically on both tracks.

        Capture remains open; paused samples are discarded in the reader thread,
        never placed in the recorder queue, encrypted files, or provider input.
        The watermark includes discarded frames so Continue uses the source clock.
        """
        start = sample_position(frame)
        end = start + frame.pcm.size
        with self._lock:
            self._watermark = max(self._watermark, end)
            # Retain history for delayed packets on either track, but skip all
            # intervals before this packet in O(log n), without copying them.
            index=bisect_right(self._ends,start)
            active=() if self._paused_at is None else ((self._paused_at,max(end,self._watermark)),)
            intervals=chain((self._intervals[i] for i in range(index,len(self._intervals))),active)
            result = []
            cursor, removed = start, self._removed[index-1] if index else 0
            for left, right in intervals:
                if right <= cursor:
                    removed += right-left
                    continue
                if left >= end:
                    break
                if cursor < left:
                    result.append(self._slice(frame, start, cursor, left, removed))
                cursor = max(cursor, right)
                removed += right-left
                if cursor >= end:
                    break
            if cursor < end:
                result.append(self._slice(frame, start, cursor, end, removed))
            return result

    @staticmethod
    def _slice(frame, original, start, end, removed):
        position = start-removed
        return replace(frame, pcm=frame.pcm[start-original:end-original],
                       t_ms=round(position*1000/SAMPLE_RATE), sample_offset=position)
