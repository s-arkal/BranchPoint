# Phase 02 Completion: Explicit Edges And Dependencies

## Status

Completed.

Phase 02 added a public explicit edge API, edge type/source validation, deterministic explicit edge IDs, edge provenance metadata, and graph rebuild behavior that merges persisted explicit edges with inferred edges without duplication. The implementation stayed inside the Phase 02 scope guard and did not add scoring or post-Phase-02 state/snapshot/provider features.

## What Changed

- Added `BranchPoint.edge(...)` for validated, persisted explicit graph edges.
- Added canonical edge type constants and validation helpers, including `custom_dependency` as the custom edge escape hatch.
- Canonicalized state dependency edges to `state_dependency`.
- Preserved compatibility for the prior `statedependency` spelling by accepting it as an alias and recording the alias in explicit edge metadata.
- Preserved compatibility for the existing `parent_child` alias while keeping `parentchild` as the canonical edge type.
- Added deterministic explicit edge IDs based on run ID, source event ID, target event ID, normalized edge type, source kind, and reason.
- Added explicit edge endpoint validation against events in the same run.
- Rejected self-edges by default, with an explicit `allow_self_edge=True` escape hatch.
- Rejected invalid edge types, source kinds, weights, and confidence values.
- Added source/provenance metadata on explicit edges, including `source_kind` and `explicit`.
- Added source/provenance metadata on inferred edges, including `source_kind` and `graph_builder_inferred`.
- Updated `GraphBuilder.build(...)` to persist inferred edges, then merge valid persisted edges back into the returned NetworkX graph so explicit edges survive graph rebuilds.
- Kept SQLite persistence idempotent through the existing `INSERT OR IGNORE` behavior.

## Files Changed

- `branchpoint/core/graph_types.py`
- `branchpoint/core/graph_builder.py`
- `branchpoint/sdk/client.py`
- `tests/test_graph_dependency_edges.py`
- `docs/pre_scoring_hardening/completion/PHASE_02_COMPLETION.md`

## Tests Run

Focused graph/storage/API tests:

```bash
uv run pytest tests/test_graph_builder.py tests/test_graph_dependency_edges.py tests/test_sqlite_store.py tests/test_emit_refs.py tests/test_depends_on_context.py
```

Result: 20 passed.

Full test suite:

```bash
uv run pytest tests
```

Result: 65 passed.

Additional checks:

```bash
python3 -m compileall branchpoint
git diff --check
rg "bp.edge|state_dependency|explicit_input_ref|explicit_output_ref|source_kind|graph_builder_inferred" branchpoint tests
rg "scorer|LocalRisk|DownstreamInfluence|EvidenceMismatch|PropagationSignal|FailureProximity|ValidationCredit|top[-_ ]?k|rank|replay|dashboard|SGLang|radix|KV-cache|kv_cache|graph database|neo4j" branchpoint tests examples pyproject.toml
```

Result: compile passed, diff check clean, graph verifier search found the new edge API/provenance coverage, and scope search found only existing docs/checklist/README guard language rather than implementation.

## Known Gaps

- No `bp.emit_edge(...)` or edge declaration event was added; Phase 02 stores explicit edges directly in `graph_edges`.
- No graph validation command, graph JSON export, or machine-readable CLI graph detail was added; those remain later-phase work.
- No first-class state path matching API was added. The `state_path_match` source kind is reserved as metadata vocabulary for later state work.
- Stored legacy inferred edges created before this phase may lack the new `source_kind` and `graph_builder_inferred` metadata because SQLite uses idempotent `INSERT OR IGNORE` and this phase does not rewrite historical rows.

## Baseline/Prior-Phase Drift

- Phase 01 already added `schema_version` to `GraphEdge` and the SQLite `graph_edges` table, so Phase 02 did not need an additional schema-version migration.
- The Phase 00 baseline and README said public `bp.edge(...)` was missing. That was true at the historical baseline but is no longer true after this phase.
- The prior code used `statedependency`; Phase 02 makes `state_dependency` canonical while accepting `statedependency` as a compatibility alias.
- The existing graph builder emitted both `parentchild` and `parent_child`. Phase 02 keeps that compatibility behavior while treating `parentchild` as the canonical edge type.
- The repository still has pre-existing unrelated local changes to `.gitignore` and an untracked `.DS_Store`; they were not part of this phase.

## No-Scoring Confirmation

No scorer, ranking API, scoring tables, role classification, score explanations, replay engine, dashboard, graph database dependency, provider integration, framework integration, ML dependency, SGLang/radix/prompt-cache/KV-cache dependency, state API, or snapshot implementation was added.
