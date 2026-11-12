# macOS Drive build compared with the latest local app

Prepared **12 November 2026**, Asia/Saigon. The user requested a comparison and
changelog; no installer was uploaded or replaced.

## Identified artifacts

| Property | Shared Google Drive build | Latest local build |
| --- | --- | --- |
| Version | 0.1.45 (46) | 0.1.50 (51) |
| Location | Scribeloop_beta → macos → ScribeLoop-0.1.45-46 | `dist/macos/ScribeLoop.app` |
| Artifact | `ScribeLoop-0.1.45-46-arm64-internal-unsigned.dmg` | Built `.app`; a new distribution DMG was not produced for this comparison |
| Date | Uploaded 6 November 2026 | Current code through 12 November 2026 |
| Source reference | No Git commit recorded in the published installer manifest | `4337f2c6096ffc01d59cf190a8735ad8caf8a0cc` |
| Architecture / minimum OS | arm64 / macOS 13.0 | arm64 / macOS 13.0 |
| Signing | Internal ad-hoc; not notarized | Internal ad-hoc; not notarized |
| Workspace schema | 15, read from the packaged Python module | 23 |
| Uncompressed app file bytes | 125,865,512 (~120.0 MiB) | 300,789,233 (~286.9 MiB) |

Sizes sum regular app files, excluding symlinks, for both apps. The new app is
about 166.8 MiB larger. The bundled local embedding model/runtime accounts for
substantial new packaging; no full per-dependency size attribution was performed.
The old DMG is compressed (57,161,121 bytes), so its size is not compared with the
new uncompressed app as a download-size measurement. Current RAM, ASR speed and
new DMG size were not benchmarked for this report.

### Drive sources

- [Shared beta folder](https://drive.google.com/drive/folders/1mG24AVtTiG9Ip82hpqNwqj8_UUSYRnbj)
- [macOS folder](https://drive.google.com/drive/folders/1Ry0PoufkB12k6Cm4xSaZ9I8nQRLygrbV)
- [0.1.45 release folder](https://drive.google.com/drive/folders/1BfXYSUtrOYjOf7BbZcdEUKXeRNhDxkMX)
- [Installer](https://drive.google.com/file/d/109DBoVaRB6QY5ixzPhoazwdGneTMlsOe/view?usp=drivesdk)
- [Published manifest](https://drive.google.com/file/d/1Gne2u8wyCrGLm3NMXhhpSuDOPMATiWYi/view?usp=drivesdk)
- [START_HERE instructions](https://drive.google.com/file/d/1JJDYhLxnbiVGtHb27KVedmXhBTR5OyNw/view?usp=drivesdk)

Both connected Drive accounts were searched. The located macOS folder contained
one release folder, 0.1.45-46. This establishes the baseline in that distribution
location; it is not a claim that no other installer exists anywhere.

## Comparison method and confidence

1. Read the live Drive folder listing, installer metadata, manifest and setup text.
2. Hash the local distribution copy. Its SHA-256 matches the Drive manifest and
   its file size matches the Drive installer metadata.
3. Mount that local DMG read-only, without launching its app. Read its Info.plist
   and inspect the PyInstaller module archive as data, without executing old code.
4. Compare packaged module availability/settings with current source and inspect
   the relevant Git diffs and implementation/validation documents.
5. Read the current built app version, calculate comparable file sizes and record
   the exact code reference, latest test result and bundle-verification status.

The remote DMG body was not downloaded and independently hashed. The inspected
local copy is identified by the checksum published in Drive. The old installer
does not pin a Git SHA; several normalized module bytecodes match `22b17ea` or
`dcb9530`, while others do not. Those commits are supporting source context,
not an asserted exact source commit for the Drive binary.

### Recorded checksums

Drive-manifest SHA-256 and matching local old DMG:

```text
580de67f53be3202b6a9bc529c6037cf6c83cc050d760348a5aec3e4aca90d25
```

Old app native executable SHA-256:

```text
ed215205848a7c24e2a56f782aa4869c00c77d4cad07917faddd12a323795bdb
```

Current app native executable SHA-256:

```text
ef7af1a7ba32adb7c1b60e8e0a452c589f6384be4f92362e3a8dffa285e4113e
```

The executable hash identifies that file, not a cryptographic hash of the whole
current app directory. A subsequent rebuild can change it.

Current bundled Python core SHA-256:

```text
b0f84f6fc745e6965362e16da366c5a3664c84823195633a5c6630f7a434a116
```

Its packaged database module declares schema 23 and its grounded-summary module
contains the label-independent content hash, confirming that the newest summary
fix is present in the built core, not only in the working tree.

## Feature comparison

| Area | Shared 0.1.45 | Current local app |
| --- | --- | --- |
| Cloud transcription | Deepgram | Deepgram plus AssemblyAI live/batch, separate credentials and provenance |
| Optional local transcription | Ollama settings/runtime already packaged | Retained; not counted as a new feature |
| Conversation Search | No user-facing Search module/settings | Keyword and optional Hybrid Search, meeting/date/person filters, exact source navigation |
| Semantic retrieval | Vector primitives, without the new meeting-search configuration | Six provider choices, fixed model/dimension generations and lazy indexing |
| Ask | No meeting-Ask module | Cited RAG answers, chat timeline, fixed context, complete saved threads and history deletion |
| Setup | Existing agreement and provider/permission settings | Guided checklist, transcript-only option, separate mic/output setup and clearer allowed states |
| Calendar | Integration already present | Explicit connected/authorization state, relaunch/activation reconciliation, grant reuse |
| Detection preference | Pause not persisted in desktop settings | Saved detection-enabled state restored on startup |
| Summary freshness | Label-sensitive input hash | Separate content/attribution hash; name edits preserve current notes |
| Data management | Encryption, playback, sharing and retention already present | Existing functions retained; encrypted search/Ask state and deletion added |

The Drive setup text describes Deepgram-only transcription, but the actual
packaged settings and modules also contain the optional Ollama route. Therefore
this changelog does not present local transcription as new.

Packaged baseline inspection found `pipeline.local_transcription`,
`llm.transcript_cleanup` and `storage.workspace_keys`, but no
`pipeline.assemblyai_meeting`, `storage.search`, `storage.embeddings` or
`llm.meeting_ask`. This supports the principal additions independently of commit
titles. Other UX fixes are grounded in source comparisons and dated validation
records rather than a new interactive run of the old app.

## Validation and limits

- Latest source: 914 Python tests passed; the changed summary-freshness paths
  include migration, rename/link, reopen, stale-legacy and rollback checks.
- Latest bundle audit: 40 native binaries, macOS 13.0 target, zero external native
  dependencies, no bundled recordings or operator transcription credential.
- Native audit checks passed for serialized Search, stale responses and cached
  history deletion. The newest context-picker UI did not receive a completed
  visual walkthrough because desktop-control startup failed.
- Existing local synthetic retrieval evaluation: 95% support recall within five
  passages and 97.5% within twenty for both Search and Ask. These are not Gemini
  accuracy scores, an ASR benchmark, or a comparison with 0.1.45, which lacked
  those user-facing features.
- Real Gemini and AssemblyAI integration checks are documented separately; no
  live AI requests were made for this comparison and the old app was not run.
- Production-scale encrypted retrieval, independent answer-grounding evaluation,
  broader physical audio/OS acceptance and notarized distribution remain open.
- The local version number has been reused across development changes. A future
  distribution should identify a unique build/code revision before replacing the
  Drive installer. This report does not bump the version or publish a release.

## Supporting implementation records

- [User-facing changelog](../CHANGELOG.md)
- [Current architecture](../ARCHITECTURE.md)
- [AssemblyAI integration](ASSEMBLYAI_INTEGRATION.md)
- [First-use UX review](FIRST_USE_UX_AUDIT_2026-11-09.md)
- [Calendar/relaunch audit](CALENDAR_STATE_AUDIT_2026-11-09.md)
- [Search/Ask remediation and measurements](SEARCH_ASK_AUDIT_REMEDIATION.md)
- [Chat flow and history deletion](ASK_CHAT_UX.md)
- [Summary freshness behavior](MEETING_SUMMARIES.md)
