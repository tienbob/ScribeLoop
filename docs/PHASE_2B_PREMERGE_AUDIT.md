# Phase 2b pre-merge audit

Requested 2026-10-26 for PR #5. Review code paths and invariants, not just previous
CI results. Implement the following confirmed fixes before merge:

1. **Saved resume beyond a cursor batch:** finished-file logic returned before
   scanning through the confirmed checkpoint. Continue bounded reads to the saved
   offset, reject checkpoints outside both authenticated saved tracks before any
   connection, and test checkpoints beyond eight records. Remove duplicate saved
   authentication scans from the explicit batch wrapper.
2. **Summary quota reset during privacy purge:** request/input usage was computed
   from deletable section jobs. Persist anonymous per-meeting aggregate usage in
   schema v10, seeded from existing jobs, updated atomically before dispatch. Forget
   may remove derived content, but must preserve already-consumed quota.
3. **Retention clock after crash:** recovery overwrote known capture end times,
   potentially extending retention. Preserve existing end times while interrupting
   active sessions; use recovery time only when no end time was recorded.

Validate failure behavior, migration/restart, privacy purge and full suite/CI on
these fixes. Do not merge automatically. Recognition/physical/signing gates remain
as documented in the Phase 2b closeout.
4. **Stale browser presentation after edits/deletion:** clear cached rolling notes
   when source speakers/transcript change or a meeting is deleted. Clamp transcript
   pagination to a valid full window when a replacement has fewer entries; otherwise
   a short replacement could show one entry with navigation hidden.
5. **Fractional native-frame endpoints:** integer sent checkpoints were compared to
   float frame ends, so a batch ending at e.g. 800.0625 ms could stall permanently.
   Compare using the integer send timeline and bound pending frame batches.
6. **Playback frame-count amplification:** window playback retained all overlapping
   frame objects. Fill a fixed-size output window instead, authenticate to EOF, and
   enforce an overall sample bound as well as the long-recording timeline bound.

## Resolution and validation

All six findings above are fixed. Regression coverage now includes a checkpoint
past multiple cursor batches, invalid checkpoints rejected before either cloud
connection, fractional native-frame endpoints through footer finalization, bounded
playback with many tiny frames, quota migration and privacy-purge preservation,
crash retention clocks, shorter replacement pagination and stale draft cleanup.

Local validation: **409 Python tests passed**, **15 frontend regression groups**,
Ruff/whitespace and 67-module import smoke passed. No real provider calls or private
recordings were used. Refresh Linux/macOS CI on the audit commit before merge;
[PR #5 checks](https://github.com/tienbob/ScribeLoop/pull/5/checks) track that result.
Known real-device/voice-recognition/signing gates remain documented, not hidden.
