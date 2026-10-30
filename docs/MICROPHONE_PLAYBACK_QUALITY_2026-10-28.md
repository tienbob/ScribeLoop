# Microphone playback quality — October 28

Confirmed a reconstruction defect using both the native generated signal and the
latest user-authorized retained recording. No audio was played, captured or
uploaded; no output device, volume or mute settings were changed. The saved audio
and transcript were not rewritten. The offline analyzer prints aggregate numbers
only, not recording identifiers, titles, transcript text, keys or audio.

## Cause

Native resampling produces small packets whose lengths do not divide evenly into
milliseconds. SLPA v1 keeps integer-ms timestamps alongside intact PCM samples.
Playback previously placed each packet independently at `round(t_ms * 16)`,
inserting short zero-filled gaps and overwriting adjacent samples. The existing
native smoke verified frequency/sample counts but did not test reconstruction
waveform fidelity. The same placement affected assembled transcription windows.

## Evidence

The newest retained recording contains 1,004,202 microphone samples in 5,884 packets
and has a 62,826 ms timeline. Old reconstruction inserted 21,571 gap samples and
overwrote 21,571 samples (about 2.15% each). Old versus corrected reconstruction
SNR in the first 60 seconds is 6.29 dB; this measures reconstruction difference,
not microphone signal-to-noise ratio. Stored microphone peak is -10.48 dBFS,
whole-recording RMS -38.57 dBFS, and clipping fraction 0%. Average RMS includes
pauses; these aggregate measurements do not establish hardware frequency response,
background-noise quality or subjective listening acceptance. The loopback container
is empty.

A 30-second native generated test reproduces the old defect. With the fix, all
480,000 samples per source match the native helper's output exactly, with zero
maximum sample error. No microphone/output device is opened by that test.

## Correction and verification

Authenticated readers retain integer timestamps for validation/footer compatibility
and reconstruct sample positions by counting contiguous samples, absorbing at most
1 ms of timestamp rounding. Larger gaps remain gaps. Sparse seek checkpoints retain
that exact position. Playback, legacy bounded windows, live transcription assembly,
saved batch upload and historical local adapters use the corrected positions.
The encrypted format, footer validation and source clock checks are unchanged.
Existing recordings benefit without a file rewrite, transcription rerun or upload.

Regression tests cover irregular 170/341/371-sample packets, multiple index
checkpoints, seek boundaries, real gaps, the fractional-ms final tail, cloud windows
and exact PCM16 batch bytes. All 500 Python tests, Ruff and diff checks pass.
Production rebuild and bundle verification passed. Playback listening acceptance
was intentionally not attempted under the speaker-output prohibition.

A bundled `--inspect-audio` core mode accepts an existing encrypted workspace path
on stdin and performs exclusive offline aggregate analysis. Close the normal app
before running it. It may require workspace Keychain approval and does not access
provider settings. Local aggregate report:
`data/phase1/microphone-quality-20261028.json` (gitignored).
