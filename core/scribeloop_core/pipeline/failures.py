"""Allowlisted diagnostics: never expose arbitrary native/provider exception text."""

CAPTURE_ERRORS = {
    'Invalid PCM frame or recorder timestamp': ('capture_invalid_frame','The recorder rejected an invalid audio frame or timestamp.'),
    'Microphone access is not authorized': ('microphone_permission','Microphone permission is not authorized for the app launching ScribeLoop. Enable it in macOS Microphone settings, then restart that app.'),
    'Selected microphone is unavailable': ('microphone_unavailable','The selected microphone is unavailable. Select a connected microphone and confirm the source again.'),
    'Default microphone changed': ('microphone_changed','The default microphone changed during capture. Select the intended microphone explicitly and start again.'),
    'Selected microphone disconnected': ('microphone_disconnected','The selected microphone disconnected during capture.'),
    'Audio clock discontinuity or drift exceeded 2 ms': ('capture_clock_discontinuity','Native audio timestamps jumped or drifted beyond the current 2 ms capture tolerance. Capture stopped; this is a capture-clock failure, before transcription.'),
    'Capture format changed or packet oversized': ('capture_format_changed','The microphone format changed or delivered an oversized packet. Select the microphone explicitly and restart capture.'),
    'Audio transport full or closed': ('capture_transport_full','The native audio pipe filled or closed. Capture stopped instead of dropping audio.'),
    'Microphone resampling failed': ('capture_resampling','The native microphone resampler failed.'),
    'Capture timestamps overlap or move backwards': ('capture_timestamp_overlap','Native audio timestamps overlapped or moved backwards.'),
    'Frame timestamps overlap': ('capture_timestamp_overlap','Recorded audio timestamps overlapped or moved backwards.'),
    'A selected capture stream stalled': ('capture_stalled','The selected audio source stopped delivering frames.'),
    'Native capture stream stalled': ('capture_stalled','The native audio source stopped delivering complete frames.'),
    'No authenticated audio in a selected stream': ('recovery_no_audio','No authenticated microphone/audio frames were available to recover.'),
}


def failure_diagnostic(exc,stage):
    for marker,(code,message) in CAPTURE_ERRORS.items():
        if marker in str(exc):
            return {'code':code,'stage':stage,'message':message+' Encrypted audio is retained; no cloud fallback was used.'}
    return {'code':stage+'_failed','stage':stage,'message':
        ('Capture failed before transcription. Check microphone access and the selected device. '
         if stage=='capture' else 'Transcription failed. Check the selected model configuration and runtime. ')+
        'Encrypted audio is retained; no automatic provider fallback was used.'}
