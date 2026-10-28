"""Offline aggregate diagnostics. No playback, capture, upload or plaintext files."""
from __future__ import annotations

import json
import math
from pathlib import Path

import numpy as np

from ..capture.source import SAMPLE_RATE, sample_position
from .audio import SessionAudioStore
from .encrypted import encrypted_database
from .playback import PlaybackIndex
from .workspace_keys import WorkspaceAudioKeys


def inspect_track(audio, sid, stream):
    from ..capture.microphone_processing import MicrophoneProcessing, MicrophoneProcessor
    preview_processors = {mode:MicrophoneProcessor(MicrophoneProcessing(noise_filter=mode))
                          for mode in ('standard','strong')} if stream == 'mic' else {}
    preview_rms = {mode:[] for mode in preview_processors}
    preview_input = []
    count = packets = clipped = gaps = overlaps = 0
    energy = peak = 0.
    packet_rms = []
    low_band = spectrum_energy = 0.
    spectral_buffer = np.zeros(0, dtype=np.float32)
    previous_end = None
    for frame in audio.frames(sid, stream):
        pcm = frame.pcm.astype(np.float64)
        count += len(pcm)
        packets += 1
        clipped += int(np.count_nonzero(np.abs(pcm) >= .999))
        energy += float(np.dot(pcm, pcm))
        if len(packet_rms) < 1_100_000:
            packet_rms.append(float(np.sqrt(np.mean(pcm*pcm))))
        if count <= 60*SAMPLE_RATE and preview_processors:
            preview_input.append(float(np.sqrt(np.mean(pcm*pcm))))
            for mode, processor in preview_processors.items():
                filtered = processor.process(frame).pcm.astype(np.float64)
                preview_rms[mode].append(float(np.sqrt(np.mean(filtered*filtered))))
        spectral_buffer = np.concatenate([spectral_buffer, pcm])
        while len(spectral_buffer) >= 1024:
            spectrum = np.abs(np.fft.rfft(spectral_buffer[:1024]*np.hanning(1024)))**2
            low_band += float(spectrum[:6].sum())  # Below approximately 80 Hz.
            spectrum_energy += float(spectrum.sum())
            spectral_buffer = spectral_buffer[1024:]
        peak = max(peak, float(np.max(np.abs(pcm))))
        raw_start = round(frame.t_ms*SAMPLE_RATE/1000)
        if previous_end is not None:
            delta = raw_start-previous_end
            if abs(raw_start-sample_position(frame)) <= SAMPLE_RATE//1000:
                gaps += max(0, delta)
                overlaps += max(0, -delta)
        previous_end = raw_start+len(pcm)
    index = PlaybackIndex(audio)
    info = index.index(sid, stream)
    # Compare actual old packet placement with corrected, sample-aligned windows.
    difference = reference_energy = 0.
    for start_ms in [0] if info['duration_ms'] else []:
        fixed = index.window(sid, stream, start_ms, start_ms+60000)
        old = np.zeros_like(fixed)
        for frame in audio.frames(sid, stream):
            offset = round((frame.t_ms-start_ms)*SAMPLE_RATE/1000)
            lo, hi = max(0, offset), min(len(old), offset+len(frame.pcm))
            if hi > lo:
                old[lo:hi] = frame.pcm[lo-offset:hi-offset]
        difference += float(np.sum((fixed.astype(np.float64)-old)**2))
        reference_energy += float(np.sum(fixed.astype(np.float64)**2))
    previews = {}
    if preview_input:
        baseline = np.asarray(preview_input)
        for mode, values in preview_rms.items():
            output = np.asarray(values)
            previews[mode] = {}
            for label, mask in [('quietest_20_percent',baseline <= np.percentile(baseline,20)),
                                ('loudest_10_percent',baseline >= np.percentile(baseline,90))]:
                before, after = np.sum(baseline[mask]**2), np.sum(output[mask]**2)
                previews[mode][label+'_level_change_db'] = round(10*math.log10(max(1e-24,after)/before),2) if before else None
    return {'sample_rate_hz':SAMPLE_RATE, 'duration_ms':info['duration_ms'],
        'samples':count, 'packets':packets, 'comparison_window_ms':min(60000, info['duration_ms']),
        'peak_dbfs':round(20*math.log10(peak), 2) if peak else None,
        'rms_dbfs':round(10*math.log10(energy/count), 2) if energy else None,
        'packet_rms_percentiles_dbfs':{
            str(q):round(20*math.log10(max(1e-12,float(np.percentile(packet_rms,q)))), 2)
            for q in (10,20,50,90)} if packet_rms else {},
        'below_80hz_energy_percent':round(low_band/spectrum_energy*100, 2) if spectrum_energy else 0.,
        'filter_preview_first_60s':previews,
        'clipped_percent':round(clipped/max(1, count)*100, 4),
        'rounding_gap_samples':gaps, 'rounding_overwritten_samples':overlaps,
        'old_vs_corrected_snr_db':round(10*math.log10(reference_energy/difference), 2)
            if difference and reference_energy else None}


def inspect_workspace(database):
    path = Path(database).resolve()
    if not path.is_file() or path.suffix != '.sqlcipher':
        raise ValueError('Select an existing encrypted workspace')
    vault = None
    def unlock(key, native):
        nonlocal vault
        vault = WorkspaceAudioKeys(path.with_name(path.name+'.audio-keys'), key, native)
    try:
        with encrypted_database(path, on_unlock=unlock) as conn:
            # Metadata only; never read or print transcript, notes, title or IDs.
            rows = conn.execute('SELECT id FROM sessions ORDER BY started_at DESC').fetchall()
            audio = SessionAudioStore(path.parent/'recordings', vault)
            for row in rows:
                sid = row['id']
                tracks = [s for s in ('mic', 'loopback') if audio.path(sid, s).is_file()]
                if tracks:
                    return {'mode':'offline newest retained recording; no audio output or network',
                        'tracks':{s:inspect_track(audio, sid, s) for s in tracks}}
            raise ValueError('No retained recording')
    finally:
        if vault is not None:
            vault.close()


def main(request):
    if not isinstance(request, dict) or set(request) != {'database'} or not isinstance(request['database'], str):
        raise ValueError('Invalid diagnostics request')
    print(json.dumps(inspect_workspace(request['database']), allow_nan=False), flush=True)
