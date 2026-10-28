# Microphone sensitivity and background-noise filtering

Implemented October 28, 2026 in native Preferences → Microphone. Defaults are
0 dB sensitivity, Standard filtering, and a -40 dBFS rejection threshold.

- **Input sensitivity:** -12 to +24 dB of software gain. This does not change the physical device's hardware settings. More gain boosts noise too; a smooth ceiling limits overload. Start at 0 dB and raise only if needed.
- **Background noise filter:** Off, Standard or Strong. The filter combines an 80 Hz rumble filter with a soft noise expander. Strong attenuates quiet backgrounds more and can soften quiet speech. It does not isolate a person, remove competing speech, or reliably remove all loud clicks/traffic. Off with 0 dB preserves input sample-for-sample.
- **Noise rejection threshold:** -65 to -25 dBFS. Raise it for more rejection; lower it to preserve quieter voices. Strong uses a 4 dB higher internal threshold and a deeper attenuation floor than Standard.
- **Reset microphone settings:** restores the defaults above. Done saves; Cancel discards the draft. No capture or provider request is made by changing these controls.

## Recording and storage

Settings apply at the start of **new recordings**, including both manual and
detected-call starts, and stay fixed for the session. Microphone processing happens
before the normal encrypted microphone writer, so playback, streaming ASR and saved
reruns all use the same processed track. Meeting-app/loopback audio is unaffected.

When gain or filtering is enabled, the app also writes `mic.raw.slpa` in the same
session directory before processing each microphone frame. It uses the session's
key with an independent authenticated file key, and is not sent to transcription.
Both versions preserve sample counts/timestamps. The original is retained for
recovery, not exposed as a separate playback selector in this increment. Record
archive/expiry/deletion policy covers it along with the normal tracks; deletion
removes its shared session key first. Disk reservation includes the extra track,
and raw/processed microphone checkpoints are made durable together. This roughly
doubles microphone storage when processing is enabled. No plaintext audio is saved.

Existing recordings are not rewritten or automatically filtered. Their prior
playback reconstruction correction remains in effect. See
[playback quality investigation](MICROPHONE_PLAYBACK_QUALITY_2026-10-28.md).

## Latest-recording analysis and verification

The latest recording at inspection was a 43,657 ms microphone-only test. Aggregate
peak was -6.66 dBFS with no clipping. Packet RMS p10/p20/p50/p90 were
-42.99/-41.96/-39.24/-32.06 dBFS; only 0.9% of spectral energy was below about 80 Hz.
These describe levels rather than identifying environmental sound types. Whole-
recording RMS includes both speech and pauses and is not microphone SNR.

An in-memory preview using the final 0 dB/-40 dBFS defaults changed levels in the
quietest 20% of packets by **-3.22 dB Standard / -7.80 dB Strong**, and in the
loudest 10% by **-0.87 / -0.91 dB**. These are energy-weighted packet groups, not
human-labeled noise/speech or a speech-quality rating. The initial milder threshold
was ineffective on this recording and was tuned before the final build. No user
audio was played, exported, modified, uploaded or used to train a model. Local
aggregate artifact: `data/phase1/microphone-filter-20261028.json` (gitignored).

All **511 Python tests** passed. Regressions cover settings persistence/validation,
consent gates, mic-only processing, untouched loopback, gain/limiting, quiet-noise
and rumble reduction, speech-band signal preservation, packet-boundary invariance,
raw/processed encrypted capture and retention deletion. A final desktop integration
assertion also verifies native-settings propagation to capture metadata and raw-track
creation. Ten seconds of generated PCM processed in 0.073 s on this host; this is a
single isolated CPU measurement, not a physical-call resource guarantee.

Production native rebuild, bundle verification, Ruff and diff checks passed. Actual
Preferences displayed the sensitivity slider, Standard filter and -40 dBFS threshold
with saved provider configuration intact. A draft slider-edit check was interrupted
by user navigation; physical slider save/cancel acceptance is not claimed. No new
microphone stream, playback or speaker-output operation was performed for validation.
