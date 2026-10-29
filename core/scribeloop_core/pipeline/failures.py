"""Allowlisted diagnostics: never expose arbitrary native/provider exception text."""

import re

CAPTURE_ERRORS = {
    'Meeting output stopped': ('capture_output_stopped','Meeting audio stopped unexpectedly while the selected source was still present or its status was unknown.'),
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
    'Missing audio buffer or synchronization clock': ('capture_missing_clock','The selected source did not supply a usable native synchronization clock.'),
    'A selected app is not running': ('capture_app_missing','The selected meeting app is not running. Reopen it and choose the source again.'),
    'No display for output capture': ('capture_display_missing','macOS did not provide a display for meeting-audio capture.'),
    'Cannot configure microphone': ('capture_microphone_setup','macOS could not attach the selected microphone to the capture session. Permission alone does not establish device availability.'),
    'Microphone capture failed': ('capture_microphone_runtime','The native microphone session reported a runtime failure after setup.'),
    'Microphone capture interrupted': ('capture_microphone_interrupted','macOS interrupted the microphone session after setup.'),
}


def failure_diagnostic(exc,stage):
    native = re.search(r'Native capture startup failed: (microphone_setup|output_enumeration|output_start|microphone_start):(screen|microphone|other):(-?\d{1,12})(?!\d)',str(exc))
    if native:
        step,domain,code = native.groups()
        detail = ('macOS declined Screen Recording access. Check access for this ScribeLoop build and restart the app.'
                  if domain=='screen' and code=='-3801' else
                  'macOS rejected the capture entitlement. This build needs its signing/permission setup corrected.'
                  if domain=='screen' and code=='-3803' else
                  'macOS could not start meeting audio. Stop competing capture tools and retry the selected source.'
                  if domain=='screen' and code=='-3818' else
                  'macOS could not start the selected capture source. This is not proof of a missing permission.')
        return {'code':f'native_{step}_{domain}_{code}','stage':stage,
            'message':f'{detail} Diagnostic: {step}/{domain}/{code}. No automatic retry was made.'}
    for marker,(code,message) in CAPTURE_ERRORS.items():
        if marker in str(exc):
            return {'code':code,'stage':stage,'message':message+' Encrypted audio is retained; no cloud fallback was used.'}
    return {'code':stage+'_failed','stage':stage,'message':
        ('Capture failed before transcription. Check microphone access and the selected device. '
         if stage=='capture' else 'Transcription failed. Check the selected model configuration and runtime. ')+
        'Encrypted audio is retained; no automatic provider fallback was used.'}
