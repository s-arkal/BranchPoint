# BranchPoint Phase 1 Verifier Checklist

Created: 2026-07-08
Verifier role: inspect implementation against Phase 1 specification; avoid changing package implementation.

## Phase 1 Acceptance Checklist

### Scope and Architecture
- [ ] Core package is framework-agnostic.
- [ ] No dependencies or implementation logic specific to LangGraph, LangChain, CrewAI, PydanticAI, OpenAI, Anthropic, SGLang, or vLLM.
- [ ] Output pipeline supports: agent run -> recorded events -> persisted trace -> dependency graph.
- [ ] Package layout includes:
  - [ ] `branchpoint/core/schema.py`
  - [ ] `branchpoint/core/ids.py`
  - [ ] `branchpoint/core/context.py`
  - [ ] `branchpoint/core/recorder.py`
  - [ ] `branchpoint/core/event_store.py`
  - [ ] `branchpoint/core/graph_types.py`
  - [ ] `branchpoint/core/graph_builder.py`
  - [ ] `branchpoint/core/serialization.py`
  - [ ] `branchpoint/storage/sqlite_store.py`
  - [ ] `branchpoint/storage/blob_store.py`
  - [ ] `branchpoint/sdk/client.py`
  - [ ] `branchpoint/sdk/decorators.py`
  - [ ] `branchpoint/cli/main.py`
- [ ] Package avoids scorer, replay, dashboard, and framework adapters beyond base stubs.

### Schema and Constants
- [ ] `TraceEvent` dataclass exists with all spec fields.
- [ ] `TraceRun` dataclass exists with all spec fields.
- [ ] Event type constants exist.
- [ ] Status constants exist.

### IDs and Context
- [ ] UUID helpers return IDs prefixed with `run_`, `evt_`, and `span_`.
- [ ] Contextvars exist for active run, project, parent, and span.
- [ ] Getter APIs expose active run, project, parent, and span.

### Event Store and SQLite
- [ ] `EventStore` protocol supports `create_run`.
- [ ] `EventStore` protocol supports `finish_run`.
- [ ] `EventStore` protocol supports `append_event`.
- [ ] `EventStore` protocol supports `list_events`.
- [ ] `EventStore` protocol supports `append_edge`.
- [ ] `EventStore` protocol supports `list_edges`.
- [ ] SQLite schema includes `runs`.
- [ ] SQLite schema includes `events`.
- [ ] SQLite schema includes `graph_edges`.
- [ ] SQLite schema includes the specified indexes.

### Blob Store and Payload Handling
- [ ] JSON blob storage writes under `.branchpoint/runs/<run_id>/payloads`.
- [ ] `MAX_INLINE_BYTES` is `16000`.
- [ ] Large input payloads become payload refs and hashes.
- [ ] Large output payloads become payload refs and hashes.

### Public API
- [ ] `BranchPoint` client API supports `trace`.
- [ ] `BranchPoint` client API supports `emit`.
- [ ] `BranchPoint` client API supports `tool`.
- [ ] `BranchPoint` client API supports `llm`.
- [ ] `BranchPoint` client API supports `memory_read`.
- [ ] `BranchPoint` client API supports `memory_write`.
- [ ] `BranchPoint` client API supports `retrieval` if retrieval is implemented.
- [ ] Public imports are exposed from `branchpoint`.

### Recorder Behavior
- [ ] Trace context creates runs.
- [ ] Trace context finishes runs with success.
- [ ] Trace context finishes runs with error on exception.
- [ ] Trace context sets and resets contextvars.
- [ ] Manual emit fills `event_id`.
- [ ] Manual emit fills `run_id`.
- [ ] Manual emit fills `project_id`.
- [ ] Manual emit fills timestamps.
- [ ] Manual emit fills parent/span context.
- [ ] Manual emit fills payload hashes.
- [ ] Manual emit stores payload refs for large payloads.
- [ ] Manual emit handles exceptions safely.

### Decorators
- [ ] `tool` decorator emits `toolcall`.
- [ ] `tool` decorator emits `tooloutput`.
- [ ] `tooloutput` links parent/input refs.
- [ ] `tool` decorator preserves return value.
- [ ] `tool` decorator records error and re-raises.
- [ ] `llm` decorator emits `llmcall`.
- [ ] `llm` decorator emits `llmoutput`.
- [ ] `llm` decorator uses safe serializer and metadata.
- [ ] `memory_read` decorator emits `memoryread`.
- [ ] `memory_write` decorator emits `memorywrite`.
- [ ] `retrieval` decorator emits `retrievalquery` and `retrievalresult` if present.

### Serialization
- [ ] `safe_serialize` never throws.
- [ ] `safe_serialize` handles primitives.
- [ ] `safe_serialize` handles dict.
- [ ] `safe_serialize` handles list, tuple, and set.
- [ ] `safe_serialize` handles dataclasses.
- [ ] `safe_serialize` handles Pydantic `model_dump`.
- [ ] `safe_serialize` handles datetime values.
- [ ] `safe_serialize` handles Exception values.
- [ ] `safe_serialize` handles bytes.
- [ ] `safe_serialize` handles unknown objects.

### Graph
- [ ] `GraphEdge` dataclass exists.
- [ ] `TraceGraph` dataclass exists.
- [ ] Graph builder creates `parent_child` edges.
- [ ] Graph builder creates `explicit_input_ref` edges.
- [ ] Graph builder creates `explicit_output_ref` edges.
- [ ] Graph builder creates `sequence` edges.
- [ ] Graph builder creates `toolresultdependency` edges.
- [ ] Graph builder creates `statedependency` edges.
- [ ] Graph can be persisted.
- [ ] Graph can be listed.

### CLI
- [ ] CLI can list runs.
- [ ] CLI can list events.
- [ ] CLI can build graph.
- [ ] CLI can print graph edges.

### Tests
- [ ] Schema tests exist.
- [ ] Recorder tests exist.
- [ ] SQLite store tests exist.
- [ ] Graph builder tests exist.
- [ ] End-to-end trace tests exist.

## Phase 1 Baseline Inspection

Current repository state:
- `branchpoint/` exists but is empty.
- `tests/` exists and contains this verifier checklist only.
- No tracked source files exist.
- No package metadata was found during the baseline scan.

Baseline result:
- All implementation checklist items are currently unverified and effectively missing.
- No framework-specific dependencies were found, but only because no package implementation or dependency metadata exists yet.
- No tests from the required test categories exist yet.

## Phase 2 Verification Plan

When implementation lands:
1. Inspect repository layout with `rg --files`, `find`, and targeted source reads.
2. Check dependency metadata for prohibited framework/vendor dependencies.
3. Read each required module and map implemented APIs/classes/functions to this checklist.
4. Inspect SQLite DDL and index definitions.
5. Inspect payload reference behavior, hash behavior, and `MAX_INLINE_BYTES`.
6. Inspect public imports and CLI entrypoints.
7. Run the relevant test suite via `uv run pytest`.
8. Add a pass/fail result section to this file or report results in the verifier response with concrete file references.

## Phase 2 Verification Result

Verified: 2026-07-08

Commands run:
- `uv run pytest -q`
- Targeted runtime probes for required module existence, dataclass fields, decorator event emission, SQLite tables/indexes, and graph edge types.

Result by acceptance area:
- PASS: Required package modules exist.
- PASS: No prohibited framework/vendor dependency or implementation references found outside this checklist.
- PASS: Schema dataclasses, ID helpers, contextvars, event store protocol, SQLite storage, blob storage, public client API, recorder, decorators, safe serialization, CLI, and required test buckets are present.
- PASS: Graph builder creates dependency edges, persists/lists them, and now emits the strict spec edge types `parent_child`, `explicit_input_ref`, `explicit_output_ref`, and `sequence` alongside the compatibility/typed dependency edges.

Test result:
- PASS: 10 tests passed.

## Focused Graph Re-Verification

Verified: 2026-07-08

Commands run:
- `uv run pytest -q`
- Targeted file-backed runtime probe confirming graph edge types include `parent_child`, `explicit_input_ref`, `explicit_output_ref`, and `sequence`.

Result:
- PASS: Graph vocabulary gap is closed.
- PASS: 10 tests passed.
