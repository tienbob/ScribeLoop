# Code audit fixes and re-audit — 30 October 2026

The four findings from the code audit are fixed. The installed app was replaced in place at `dist/macos/ScribeLoop.app`, version 0.1.10 (11), Apple Silicon, macOS 13 or later.

## Changes

- **Stop and recovery:** read-only commands no longer accumulate receipt-ledger entries. Stop, Stop recording, and revocation bypass the ledger capacity check and the uncertain-mutation barrier. Repeated pending emergency actions share one request; a terminal or unknown result permits a new request. Ordinary mutations still require resolution of uncertain earlier actions. Read receipts still reject unavailable playback requests without recreating ledger entries.
- **Stop during startup:** an explicitly delivered controller STOP permits completion before all selected tracks have produced PCM. Completion framing, absence of trailing bytes, and successful helper exit remain required. Without accepted STOP, missing tracks still fail closed. STOP delivery and the completion check share the lifecycle lock.
- **History pagination:** loading older pages and inserting new sessions preserve all loaded metadata, including the newest, selected, and active sessions. Pages still load on demand; duplicate rows are not appended.
- **History performance:** schema 14 adds `events(session_id,type,id)`. A v13 upgrade creates the index and advances the version in one transaction. Existing v13 deduplication retains its own atomic migration boundary. Upgrade, idempotence, query-plan, and injected rollback tests cover the new migration.

## Verification and re-audit

- Full Python suite: **600 passed**; recording tests rerun after binding the loop variable in an existing test callback: **18 passed**.
- Native regression executable: passed saturated-ledger recovery and emergency Stop, read-receipt handling, repeated emergency actions, 150-row history pagination, selected/active preservation, new-session insertion, and deduplication.
- Frontend replay regressions passed; Ruff and `git diff --check` passed.
- Rebuilt app opened the encrypted workspace and restored its three saved conversations. The silent access check initially detected the existing ad-hoc signing permission issue (`-3801`). After local macOS authentication, the stale ScribeLoop entry was removed and the current app added. Final in-app check: meeting-audio access allowed, microphone authorized, display available; no audio recorded. The app remains open and watching for calls.
- Production rebuild and bundle audit passed: 38 native binaries, zero external native dependencies, minimum macOS 13, no recordings or transcription credentials bundled. Build remains ad-hoc signed.
- Re-read changed control flow and its callers: recording startup/drain, desktop receipt consumers and reconnect reconciliation, history reduction/insertion, and SQLite/SQLCipher upgrade paths. No further actionable findings in these changed paths.

Synthetic, in-memory history benchmark: 30 sessions and 60,030 events; median of five reads. Without the new index, `SessionStore.list()` took **165.010 ms**; with it, **0.261 ms**. `EXPLAIN QUERY PLAN` uses the new index. This measures synthetic metadata reads, not live-call latency.

Native checks can be reproduced without launching the core or opening audio devices:

```sh
DEVELOPER_DIR=/Applications/Xcode.app/Contents/Developer xcrun swiftc \
  -parse-as-library -swift-version 5 -D DESKTOP_AUDIT_TESTS \
  -target arm64-apple-macos13.0 -module-cache-path build/macos/swift-cache \
  shell/macos/Desktop/DesktopClient.swift shell/macos/Desktop/MeetingPlayback.swift \
  tests/native/DesktopAuditTests.swift -o build/macos/desktop-audit-tests
build/macos/desktop-audit-tests
```

The native test uses an unresumed socket and runs synchronously, so queued transport tasks do not execute. No speaker output, live recording, or billed provider requests were used. These checks do not establish behavior during a real Teams/browser call. Loaded history metadata grows with explicitly loaded pages; transcript and audio payloads remain separately paged/cached.

## Follow-up app test

After permission refresh, hands-on checks in the installed 0.1.10 app passed:

- A notes source link opens the saved transcript.
- New recording opens setup; Not now returns to the same three conversations without creating a recording.
- Language search for `vi` filters the list; selecting Vietnamese alongside English shows two selected. Cancel discards the draft; reopening restores the original one-language selection. User settings were preserved.
- The silent capture-access check reports meeting-audio access allowed, microphone authorized, and display available.

A focused lifecycle/desktop/autocall/transport/end-reason run passed **97 tests** in 9.57 seconds. Two new full-recorder integration cases use a real child pipe with synthetic data: Stop before any PCM and Stop before the second track starts. Both drain successfully, finish as completed with `manual_stop`, clear active/processing state, and emit no recording error. Both were rerun successfully after import formatting. The native ledger/history regression executable also passed again. No product code changed during this follow-up, so another app rebuild was unnecessary.

No microphone audio was captured, audio was played, or cloud provider requests were made. Actual Teams/browser hangup remains a live-call acceptance check.
