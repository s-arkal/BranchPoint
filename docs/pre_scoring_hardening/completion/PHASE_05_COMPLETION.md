# Phase 05 Completion: Payload Redaction, Serialization, And Retention

## Status

Completed.

Phase 05 added redaction-before-storage, deterministic payload serialization, bounded previews/truncation metadata, blob integrity validation, and local retention cleanup. The implementation stayed inside Phase 05 scope and did not add scoring, replay, dashboards, provider/framework integrations, graph database dependencies, or cache/KV work.

## What Changed

- Added `BranchPoint(...)` configuration for redaction rules, callbacks, replacement text, default redaction inclusion, inline payload limits, preview limits, and maximum stored blob bytes.
- Added default redaction for common secret keys including `authorization`, `api_key`, `password`, `token`, `secret`, `client_secret`, and `ssn`.
- Added JSON Pointer redaction, key-name redaction, regex redaction for string values, and callback redaction.
- Added storage serialization that serializes safely, redacts before persistence, and records redaction paths without storing original values.
- Added canonical hash serialization using compact sorted JSON, deterministic set ordering, and stable object repr handling.
- Changed event, snapshot, state metadata hashes, SQLite writes, and blob writes to use deterministic redacted serialization rather than raw payload hashing.
- Added preview serialization and truncation metadata for payloads constrained by `max_blob_bytes`.
- Added redaction/truncation metadata for event payloads, run metadata, event metadata, snapshot payloads, and snapshot metadata.
- Added blob integrity checks using stored payload hashes, including missing/corrupted blob detection.
- Added `BranchPoint.validate_run_blobs(run_id)` and CLI `branchpoint validate-run <run_id>`.
- Added retention cleanup for completed runs older than a duration/cutoff, deleting runs, events, graph edges, snapshots, and matching blob directories.
- Added `BranchPoint.cleanup(older_than=...)`, SQLite cleanup primitives, BlobStore run cleanup, and CLI `branchpoint cleanup --older-than 30d`.
- Added focused payload-safety tests covering default/custom redaction, deterministic hashes, blob redaction/integrity, truncation, snapshot redaction, cleanup, and CLI validation/cleanup.

## Files Changed

- `branchpoint/cli/main.py`
- `branchpoint/core/recorder.py`
- `branchpoint/core/serialization.py`
- `branchpoint/core/snapshots.py`
- `branchpoint/sdk/client.py`
- `branchpoint/sdk/decorators.py`
- `branchpoint/storage/blob_store.py`
- `branchpoint/storage/sqlite_store.py`
- `tests/test_payload_safety.py`
- `docs/pre_scoring_hardening/completion/PHASE_05_COMPLETION.md`

## Tests Run

Verification refresh for Linear issue BRA-5, 2026-07-09:

- Re-read the required Phase 00, README, Phase 05, non-goal/scope-guard, verifier, and completion documents.
- Inspected current redaction, serialization, recorder payload hashing/externalization, `BlobStore`, SQLite store, CLI cleanup/validation, and storage/payload tests.
- Found no Phase 05 implementation drift requiring code changes.
- Confirmed current code remains inside Phase 05 scope with no scorer, ranking, replay, dashboard, provider/framework integration, graph database, or cache/KV implementation.

Focused Phase 05 suite:

```bash
uv run pytest tests/test_payload_safety.py tests/test_schema.py tests/test_recorder.py tests/test_sqlite_store.py tests/test_snapshots.py
```

Result: 28 passed.

Full suite:

```bash
uv run pytest tests
```

Result: 88 passed.

Additional checks:

```bash
python3 -m compileall branchpoint
git diff --check
rg "scorer|LocalRisk|DownstreamInfluence|EvidenceMismatch|PropagationSignal|FailureProximity|ValidationCredit|top[-_ ]?k|rank|replay|dashboard|SGLang|radix|KV-cache|kv_cache|graph database|neo4j" branchpoint tests examples pyproject.toml
```

Result: compile passed, diff check clean, and the scope search found only existing README/checklist guardrail text rather than implementation.

Verifier subagents:

- Storage and Payload Safety Verifier: PASS.
- Scope Guard Verifier: PASS.

## Known Gaps

- Redaction callbacks intentionally receive serialized values and may over-redact if they raise; callback failure redacts the current path rather than preserving possibly sensitive content.
- Regex redaction currently redacts whole string values, not substrings.
- Retention cleanup is local and explicit; there is no hosted retention service or scheduled cleanup worker.
- Blob validation covers blob-backed event and snapshot payloads; inline payload hash validation is not exposed as a separate CLI command.
- Truncation stores a preview-shaped payload when `max_blob_bytes` is exceeded, so intentionally truncated payloads are not recoverable.

## Baseline/Prior-Phase Drift

- Phase 00 and the hardening README listed redaction rules, retention cleanup, deterministic hash/preview policy, and blob integrity validation as missing. Those are now implemented for local SQLite/blob persistence.
- Phase 04 added snapshots and basic hash verification first; Phase 05 reused those snapshot surfaces and added redaction, metadata safety, preview configuration, and blob integrity checks.
- SQLite schema did not require new columns for Phase 05 because redaction/truncation metadata is stored in existing metadata JSON fields.
- The repository still has pre-existing unrelated local changes to `.gitignore` and an untracked `.DS_Store`; they were not part of Phase 05 and should not be committed with this phase.

## No-Scoring Confirmation

No scorer, ranking API, scoring tables, role classification, scoring explanations, replay engine, dashboard, graph database dependency, provider integration, framework integration, ML dependency, SGLang/radix/prompt-cache/KV-cache dependency, runtime cache acceleration, graph DB work, or cache/KV work was added.
