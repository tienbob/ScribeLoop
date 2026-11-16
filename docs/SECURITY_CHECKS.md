# Security checks

CI runs pip-audit 2.10.1 against the installed core, dev and security dependencies,
and Bandit 1.9.4 against `core/` at medium severity or higher. An advisory failure
requires investigation and a dependency update or an explicitly reviewed exception.
These checks complement tests and code review; they do not certify the app secure
or cover optional research environments and native dependencies.

## Reviewed static-analysis baseline

`security/bandit-baseline.json` records 23 reviewed existing findings. It does not
disable any rule globally. New findings fail the scan. Bandit's baseline matching
is heuristic: changes to existing baselined code still require review; a green
scan does not establish that modified SQL or model loading is safe.

The B608 findings use generated `?` placeholders with separately bound values,
fixed dictionary field names, literal SQL clauses, literal migration identifiers,
or validated vector table names. Reviewed modules: `llm/meeting_ask.py`,
`speakers/registry.py`, and `storage/{db,search,sessions,vectors}.py`.
User text and provider/model identifiers must never become SQL identifiers.
Vector generation names are hashed; registry names are validated before deletion.

The two B615 findings are the historical MOSS worker's explicit local-directory
loads with `local_files_only=True`. They do not download from Hugging Face. This
experimental worker executes reviewed local model code and is not the cloud app's
normal transcription path. A complete local manifest is checked before importing
the model libraries; it is not an independently trusted upstream revision.

AMI annotations now use defusedxml to reject entity expansion instead of
baselining the XML parser warnings. A regression test checks rejection.

Do not regenerate the baseline automatically to make CI pass. Review each new
finding and its input provenance before recording a justified exception here.

## Historical MOSS manifest

The Python MOSS worker requires `models/MOSS-Transcribe-Diarize/manifest.sha256`
covering every regular file recursively, except the manifest itself. Entries use
SHA-256 followed by two spaces and a relative POSIX filename (the sha256sum binary
marker is also supported). Empty, malformed, duplicate, missing, extra, unsafe or
symlinked entries fail before model imports. Files are hashed in 1 MiB chunks.

Obtain the model and manifest from a reviewed source, verify them independently,
and keep the directory read-only during loading. A manifest beside the weights
detects corruption relative to that manifest; anyone able to replace both can
bypass it. This is not code authentication, a sandbox or protection against
concurrent filesystem modification. It does not change the historical GGUF
experiment documented in [LOCAL_MOSS.md](LOCAL_MOSS.md).
