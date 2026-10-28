"""Causal gain, rumble filtering and soft expansion; no devices or network.

Reduces quiet background noise, not competing speech or all sudden sounds.
"""
from __future__ import annotations

import math
from typing import Literal

import numpy as np
from pydantic import BaseModel, ConfigDict, Field

from .source import SAMPLE_RATE, AudioFrame


class MicrophoneProcessing(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)
    gain_db: float = Field(default=0., ge=-12, le=24)
    noise_filter: Literal['off', 'standard', 'strong'] = 'off'
    noise_threshold_db: float = Field(default=-40., ge=-65, le=-25)

    @property
    def enabled(self):
        return self.gain_db != 0 or self.noise_filter != 'off'


class MicrophoneProcessor:
    def __init__(self, settings: MicrophoneProcessing):
        self.settings = settings
        self.gain = 10**(settings.gain_db/20)
        self.pole = math.exp(-2*math.pi*80/SAMPLE_RATE)
        self.previous_x = self.previous_y = 0.
        self.energy = np.zeros(320, dtype=np.float64)
        self.energy_sum = 0.
        self.index = self.hold = 0
        self.envelope = 1.
        self.floor = 10**((-30 if settings.noise_filter == 'strong' else -18)/20)
        self.threshold = 10**((settings.noise_threshold_db+(4 if settings.noise_filter == 'strong' else 0))/20)
        self.attack = math.exp(-1/(.003*SAMPLE_RATE))
        self.release = math.exp(-1/(.2*SAMPLE_RATE))

    def process(self, frame: AudioFrame):
        if frame.stream != 'mic' or not self.settings.enabled:
            return frame
        result = np.empty_like(frame.pcm)
        for i, sample in enumerate(frame.pcm):
            value = float(sample)
            if self.settings.noise_filter != 'off':
                filtered = self.pole*(self.previous_y+value-self.previous_x)
                self.previous_x, self.previous_y = value, filtered
                value = filtered
                square = value*value
                self.energy_sum += square-self.energy[self.index]
                self.energy[self.index] = square
                self.index = (self.index+1) % len(self.energy)
                rms = math.sqrt(max(0., self.energy_sum)/len(self.energy))
                if rms >= self.threshold*1.25:
                    self.hold = int(.15*SAMPLE_RATE)
                if self.hold:
                    self.hold -= 1
                    target = 1.
                else:
                    knee = min(1., max(0., (rms/self.threshold-.5)/.75))
                    target = self.floor+(1-self.floor)*knee*knee
                coefficient = self.attack if target > self.envelope else self.release
                self.envelope = target+coefficient*(self.envelope-target)
                value *= self.envelope
            value *= self.gain
            # Smooth ceiling avoids hard clipping when sensitivity is boosted.
            if abs(value) > .9:
                value = math.copysign(.9+.1*math.tanh((abs(value)-.9)/.1), value)
            result[i] = value
        return AudioFrame(result, frame.stream, frame.t_ms, frame.wall_ts, frame.sample_offset)
