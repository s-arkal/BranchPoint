"""Public BranchPoint client."""

from __future__ import annotations

import inspect
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from branchpoint.core.context import get_current_project_id, get_current_run_id, reset_dependency_refs, set_dependency_refs
from branchpoint.core.errors import EventContractError, NoActiveTraceError, TraceNotFoundError
from branchpoint.core.graph_builder import GraphBuilder
from branchpoint.core.graph_types import (
    EDGE_SOURCE_EXPLICIT_USER,
    EDGE_WEIGHTS,
    GraphEdge,
    deterministic_explicit_edge_id,
    normalize_edge_type,
    validate_edge_source_kind,
    validate_edge_weight,
)
from branchpoint.core.ids import new_snapshot_id
from branchpoint.core.provenance import ProvenanceTracker
from branchpoint.core.recorder import Recorder
from branchpoint.core.refs import ProvenanceRef, collect_input_refs, refs_to_dicts
from branchpoint.core.schema import (
    METADATA_AFTER_HASH,
    METADATA_BEFORE_HASH,
    METADATA_OPERATION,
    METADATA_STATE_NAME,
    METADATA_STATE_PATH,
    METADATA_VALUE_HASH,
    SNAPSHOT_CUSTOM,
    STATE_READ,
    STATE_WRITE,
    SUCCESS,
    Snapshot,
    canonical_state_name,
    canonical_state_path,
    validate_snapshot_kind,
)
from branchpoint.core.serialization import RedactionCallback, RedactionConfig, hash_serialized_payload, safe_serialize
from branchpoint.core.snapshots import (
    diff_json_like,
    link_snapshot_metadata,
    prepare_snapshot_payload,
    verify_snapshot_payload,
)
from branchpoint.storage.blob_store import BlobStore
from branchpoint.storage.sqlite_store import SQLiteEventStore
from .decorators import (
    handoff_decorator,
    llm_decorator,
    memory_read_decorator,
    memory_write_decorator,
    retry_decorator,
    retrieval_decorator,
    route_decorator,
    state_read_decorator,
    state_write_decorator,
    tool_decorator,
    validation_decorator,
)
from .prompt import BranchPointPrompt


class BranchPoint:
    def __init__(
        self,
        project: str,
        db_path: str = ".branchpoint/branchpoint.sqlite",
        *,
        provenance_mode: str = "hybrid",
        strict_event_types: bool = True,
        redaction_rules: list[str | re.Pattern[str]] | tuple[str | re.Pattern[str], ...] | None = None,
        redaction_callbacks: list[RedactionCallback] | tuple[RedactionCallback, ...] | None = None,
        redaction_replacement: str = "[REDACTED]",
        include_default_redaction: bool = True,
        max_inline_bytes: int = 16_000,
        max_preview_chars: int = 2_000,
        max_blob_bytes: int | None = None,
    ) -> None:
        self.project_id = project
        self.db_path = db_path
        self.strict_event_types = strict_event_types
        self.redaction_config = RedactionConfig.from_rules(
            redaction_rules,
            callbacks=redaction_callbacks,
            replacement=redaction_replacement,
            include_defaults=include_default_redaction,
        )
        self.max_inline_bytes = max_inline_bytes
        self.max_preview_chars = max_preview_chars
        self.max_blob_bytes = max_blob_bytes
        self.store = SQLiteEventStore(
            db_path=db_path,
            strict_event_types=strict_event_types,
            redaction_config=self.redaction_config,
        )
        self.blob_store = BlobStore(
            Path(db_path).parent,
            max_inline_bytes=max_inline_bytes,
            max_blob_bytes=max_blob_bytes,
        )
        self.provenance_tracker = ProvenanceTracker(provenance_mode=provenance_mode)
        self.recorder = Recorder(
            project_id=project,
            store=self.store,
            blob_store=self.blob_store,
            provenance_tracker=self.provenance_tracker,
            strict_event_types=strict_event_types,
            redaction_config=self.redaction_config,
            max_preview_chars=max_preview_chars,
            max_blob_bytes=max_blob_bytes,
        )

    def trace(self, name: str | None = None, metadata: dict[str, Any] | None = None):
        return self.recorder.trace(name=name, metadata=metadata)

    def emit(self, *args: Any, auto_refs: bool = True, **kwargs: Any):
        if not auto_refs:
            return self.recorder.emit(*args, **kwargs)

        bound = inspect.signature(self.recorder.emit).bind_partial(*args, **kwargs)
        arguments = bound.arguments
        input_args = (arguments["input"],) if "input" in arguments else ()
        detail_refs, input_refs = collect_input_refs(
            self.provenance_tracker,
            args=input_args,
            manual_input_refs=arguments.get("input_refs"),
            include_context_refs=True,
        )
        arguments["input_refs"] = input_refs
        arguments["metadata"] = _metadata_with_provenance(arguments.get("metadata"), detail_refs)
        return self.recorder.emit(**arguments)

    def depends_on(
        self,
        *values: Any,
        event_ids: list[str] | tuple[str, ...] | None = None,
        reason: str = "depends_on_context",
    ):
        return _DependencyContext(self.provenance_tracker, values, event_ids, reason)

    def state_read(
        self,
        path: str | list[Any] | tuple[Any, ...],
        value: Any = None,
        *,
        state_name: str | None = None,
        name: str | None = None,
        metadata: dict[str, Any] | None = None,
        input_refs: Any = None,
        status: str = SUCCESS,
        auto_refs: bool = True,
    ):
        resolved_state_name = canonical_state_name(_metadata_state_name(metadata, state_name))
        resolved_state_path = canonical_state_path(path)
        event_metadata = _state_event_metadata(
            metadata,
            operation="read",
            state_name=resolved_state_name,
            state_path=resolved_state_path,
            value=value,
            hash_payload=self.recorder.hash_payload,
        )
        if auto_refs:
            detail_refs, event_input_refs = collect_input_refs(
                self.provenance_tracker,
                manual_input_refs=input_refs,
                include_context_refs=True,
            )
            event_metadata = _metadata_with_provenance(event_metadata, detail_refs)
        else:
            event_input_refs = list(input_refs or [])
        return self.recorder.emit(
            type=STATE_READ,
            name=name,
            input={METADATA_STATE_NAME: resolved_state_name, METADATA_STATE_PATH: resolved_state_path},
            output=value,
            input_refs=event_input_refs,
            status=status,
            metadata=event_metadata,
        )

    def state_write(
        self,
        path: str | list[Any] | tuple[Any, ...],
        *,
        before: Any = None,
        after: Any = None,
        state_name: str | None = None,
        name: str | None = None,
        metadata: dict[str, Any] | None = None,
        input_refs: Any = None,
        status: str = SUCCESS,
        auto_refs: bool = True,
    ):
        resolved_state_name = canonical_state_name(_metadata_state_name(metadata, state_name))
        resolved_state_path = canonical_state_path(path)
        event_metadata = _state_event_metadata(
            metadata,
            operation="write",
            state_name=resolved_state_name,
            state_path=resolved_state_path,
            before=before,
            after=after,
            hash_payload=self.recorder.hash_payload,
        )
        if auto_refs:
            detail_refs, event_input_refs = collect_input_refs(
                self.provenance_tracker,
                args=(before, after),
                manual_input_refs=input_refs,
                include_context_refs=True,
            )
            event_metadata = _metadata_with_provenance(event_metadata, detail_refs)
        else:
            event_input_refs = list(input_refs or [])
        return self.recorder.emit(
            type=STATE_WRITE,
            name=name,
            input={
                METADATA_STATE_NAME: resolved_state_name,
                METADATA_STATE_PATH: resolved_state_path,
                "before": before,
            },
            output=after,
            input_refs=event_input_refs,
            status=status,
            metadata=event_metadata,
        )

    def snapshot(
        self,
        *,
        kind: str = SNAPSHOT_CUSTOM,
        payload: Any,
        name: str | None = None,
        event_id: str | None = None,
        metadata: dict[str, Any] | None = None,
        run_id: str | None = None,
        project_id: str | None = None,
    ) -> Snapshot:
        validate_snapshot_kind(kind)
        active_run_id = get_current_run_id()
        active_project_id = get_current_project_id()
        resolved_run_id = run_id or active_run_id
        if resolved_run_id is None:
            raise NoActiveTraceError("snapshot requires an active trace or explicit run_id")
        if active_run_id is not None and run_id is not None and run_id != active_run_id:
            raise EventContractError("snapshot run_id cannot differ from the active trace run_id")
        if active_project_id is not None and project_id is not None and project_id != active_project_id:
            raise EventContractError("snapshot project_id cannot differ from the active trace project_id")

        run = self.store.get_run(resolved_run_id)
        if run is None:
            raise TraceNotFoundError(f"BranchPoint run {resolved_run_id!r} does not exist")

        event = None
        if event_id is not None:
            events_by_id = {event.event_id: event for event in self.store.list_events(resolved_run_id)}
            event = events_by_id.get(event_id)
            if event is None:
                raise EventContractError(
                    f"BranchPoint snapshot event_id must belong to run {resolved_run_id!r}: {event_id!r}"
                )

        resolved_project_id = project_id or (event.project_id if event is not None else active_project_id) or run.project_id
        snapshot = Snapshot(
            snapshot_id=new_snapshot_id(),
            run_id=resolved_run_id,
            event_id=event_id,
            project_id=resolved_project_id,
            kind=kind,
            name=name,
            payload=payload,
            metadata=safe_serialize(metadata or {}),
        )
        prepare_snapshot_payload(
            snapshot,
            self.blob_store,
            redaction_config=self.redaction_config,
            max_preview_chars=self.max_preview_chars,
            max_blob_bytes=self.max_blob_bytes,
        )
        self.store.append_snapshot(snapshot)

        if event is not None:
            self.store.update_event_metadata(event.event_id, link_snapshot_metadata(event.metadata, snapshot))

        return snapshot

    def get_snapshot(self, snapshot_id: str) -> Snapshot | None:
        return self.store.get_snapshot(snapshot_id)

    def list_snapshots(
        self,
        run_id: str | None = None,
        *,
        event_id: str | None = None,
        kind: str | None = None,
    ) -> list[Snapshot]:
        resolved_run_id = run_id or get_current_run_id()
        if resolved_run_id is None:
            raise NoActiveTraceError("list_snapshots requires an active trace or explicit run_id")
        return self.store.list_snapshots(resolved_run_id, event_id=event_id, kind=kind)

    def snapshot_payload(self, snapshot: Snapshot | str) -> Any:
        resolved_snapshot = self.store.get_snapshot(snapshot) if isinstance(snapshot, str) else snapshot
        if resolved_snapshot is None:
            raise TraceNotFoundError(f"BranchPoint snapshot {snapshot!r} does not exist")
        payload = resolved_snapshot.payload
        if payload is None and resolved_snapshot.payload_ref is not None:
            payload = self.blob_store.get_json(
                resolved_snapshot.payload_ref,
                expected_hash=resolved_snapshot.payload_hash,
            )
        return verify_snapshot_payload(resolved_snapshot, payload)

    def validate_run_blobs(self, run_id: str) -> list[dict[str, str]]:
        problems: list[dict[str, str]] = []
        for event in self.store.list_events(run_id):
            for field, ref, expected_hash in (
                ("input", event.input_payload_ref, event.input_hash),
                ("output", event.output_payload_ref, event.output_hash),
            ):
                if ref is None:
                    continue
                error = self.blob_store.validate_json(ref, expected_hash)
                if error is not None:
                    problems.append({"kind": "event", "id": event.event_id, "field": field, "ref": ref, "error": error})
        for snapshot in self.store.list_snapshots(run_id):
            if snapshot.payload_ref is None:
                continue
            error = self.blob_store.validate_json(snapshot.payload_ref, snapshot.payload_hash)
            if error is not None:
                problems.append(
                    {
                        "kind": "snapshot",
                        "id": snapshot.snapshot_id,
                        "field": "payload",
                        "ref": snapshot.payload_ref,
                        "error": error,
                    }
                )
        return problems

    def cleanup(self, *, older_than: str | timedelta | datetime) -> dict[str, object]:
        cutoff = _cleanup_cutoff(older_than)
        result = self.store.cleanup_runs_before(cutoff.isoformat())
        run_ids = list(result["run_ids"])
        blobs_removed = 0
        for run_id in run_ids:
            if self.blob_store.cleanup_run(run_id):
                blobs_removed += 1
        result["blobs_removed"] = blobs_removed
        return result

    def diff(self, before: Any, after: Any) -> list[dict[str, Any]]:
        return diff_json_like(before, after)

    def edge(
        self,
        source_event_id: str,
        target_event_id: str,
        edge_type: str,
        weight: float | None = None,
        confidence: float = 1.0,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
        *,
        source_kind: str = EDGE_SOURCE_EXPLICIT_USER,
        run_id: str | None = None,
        allow_self_edge: bool = False,
    ) -> GraphEdge:
        active_run_id = get_current_run_id()
        resolved_run_id = run_id or active_run_id
        if resolved_run_id is None:
            raise NoActiveTraceError("edge requires an active trace or explicit run_id")
        if active_run_id is not None and run_id is not None and run_id != active_run_id:
            raise EventContractError("edge run_id cannot differ from the active trace run_id")
        if source_event_id == target_event_id and not allow_self_edge:
            raise EventContractError("BranchPoint explicit edges cannot point an event to itself")

        run = self.store.get_run(resolved_run_id)
        if run is None:
            raise TraceNotFoundError(f"BranchPoint run {resolved_run_id!r} does not exist")

        events_by_id = {event.event_id: event for event in self.store.list_events(resolved_run_id)}
        missing_event_ids = [
            event_id
            for event_id in (source_event_id, target_event_id)
            if event_id not in events_by_id
        ]
        if missing_event_ids:
            missing = ", ".join(repr(event_id) for event_id in missing_event_ids)
            raise EventContractError(
                f"BranchPoint explicit edge endpoints must be events in run {resolved_run_id!r}; missing: {missing}"
            )

        normalized_edge_type = normalize_edge_type(edge_type)
        validate_edge_source_kind(source_kind)
        resolved_weight = validate_edge_weight(
            "weight",
            EDGE_WEIGHTS[normalized_edge_type] if weight is None else weight,
        )
        resolved_confidence = validate_edge_weight("confidence", confidence)
        edge_metadata = dict(safe_serialize(metadata or {}))
        edge_metadata["source_kind"] = source_kind
        edge_metadata["explicit"] = True
        if normalized_edge_type != edge_type:
            edge_metadata["edge_type_alias"] = edge_type

        edge = GraphEdge(
            edge_id=deterministic_explicit_edge_id(
                resolved_run_id,
                source_event_id,
                target_event_id,
                normalized_edge_type,
                source_kind,
                reason,
            ),
            run_id=resolved_run_id,
            source_event_id=source_event_id,
            target_event_id=target_event_id,
            edge_type=normalized_edge_type,
            weight=resolved_weight,
            confidence=resolved_confidence,
            reason=reason,
            metadata=edge_metadata,
        )
        self.store.append_edge(edge)
        return edge

    def prompt(self) -> BranchPointPrompt:
        return BranchPointPrompt(self.provenance_tracker)

    def format(self, template: str, **kwargs: Any) -> BranchPointPrompt:
        prompt = self.prompt()
        prompt.add(str(template).format(**kwargs), ref=tuple(kwargs.values()))
        return prompt

    def refs(self, *values: Any) -> list[str]:
        event_ids: set[str] = set()
        for value in values:
            event_ids.update(self.provenance_tracker.event_ids(value))
        return sorted(event_ids)

    def ref_details(self, *values: Any) -> list[dict[str, Any]]:
        details: dict[tuple[str, str, str], dict[str, Any]] = {}
        for value in values:
            for ref in self.provenance_tracker.details(value):
                key = (ref["event_id"], repr(ref["path"]), ref["reason"])
                details[key] = ref
        return [details[key] for key in sorted(details)]

    def unwrap(self, value: Any) -> Any:
        return self.provenance_tracker.unwrap(value)

    def detach(self, value: Any) -> Any:
        return self.provenance_tracker.detach(value)

    def tool(
        self,
        name: str | None = None,
        *,
        exclude_args: list[int] | None = None,
        exclude_kwargs: list[str] | None = None,
        track_output: bool = True,
        provenance_mode: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        return tool_decorator(
            self.recorder,
            self.provenance_tracker,
            name=name,
            exclude_args=exclude_args,
            exclude_kwargs=exclude_kwargs,
            track_output=track_output,
            provenance_mode=provenance_mode,
            metadata=metadata,
        )

    def llm(
        self,
        name: str | None = None,
        *,
        exclude_args: list[int] | None = None,
        exclude_kwargs: list[str] | None = None,
        track_output: bool = True,
        provenance_mode: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        return llm_decorator(
            self.recorder,
            self.provenance_tracker,
            name=name,
            exclude_args=exclude_args,
            exclude_kwargs=exclude_kwargs,
            track_output=track_output,
            provenance_mode=provenance_mode,
            metadata=metadata,
        )

    def memory_read(
        self,
        name: str | None = None,
        *,
        exclude_args: list[int] | None = None,
        exclude_kwargs: list[str] | None = None,
        track_output: bool = True,
        provenance_mode: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        return memory_read_decorator(
            self.recorder,
            self.provenance_tracker,
            name=name,
            exclude_args=exclude_args,
            exclude_kwargs=exclude_kwargs,
            track_output=track_output,
            provenance_mode=provenance_mode,
            metadata=metadata,
        )

    def memory_write(
        self,
        name: str | None = None,
        *,
        exclude_args: list[int] | None = None,
        exclude_kwargs: list[str] | None = None,
        track_output: bool = True,
        provenance_mode: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        return memory_write_decorator(
            self.recorder,
            self.provenance_tracker,
            name=name,
            exclude_args=exclude_args,
            exclude_kwargs=exclude_kwargs,
            track_output=track_output,
            provenance_mode=provenance_mode,
            metadata=metadata,
        )

    def state_reader(
        self,
        path: str | list[Any] | tuple[Any, ...],
        name: str | None = None,
        *,
        state_name: str | None = None,
        exclude_args: list[int] | None = None,
        exclude_kwargs: list[str] | None = None,
        track_output: bool = True,
        provenance_mode: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        return state_read_decorator(
            self.recorder,
            self.provenance_tracker,
            path=path,
            state_name=state_name,
            name=name,
            exclude_args=exclude_args,
            exclude_kwargs=exclude_kwargs,
            track_output=track_output,
            provenance_mode=provenance_mode,
            metadata=metadata,
        )

    def state_writer(
        self,
        path: str | list[Any] | tuple[Any, ...],
        name: str | None = None,
        *,
        state_name: str | None = None,
        exclude_args: list[int] | None = None,
        exclude_kwargs: list[str] | None = None,
        track_output: bool = True,
        provenance_mode: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        return state_write_decorator(
            self.recorder,
            self.provenance_tracker,
            path=path,
            state_name=state_name,
            name=name,
            exclude_args=exclude_args,
            exclude_kwargs=exclude_kwargs,
            track_output=track_output,
            provenance_mode=provenance_mode,
            metadata=metadata,
        )

    def retrieval(
        self,
        name: str | None = None,
        *,
        exclude_args: list[int] | None = None,
        exclude_kwargs: list[str] | None = None,
        track_output: bool = True,
        provenance_mode: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        return retrieval_decorator(
            self.recorder,
            self.provenance_tracker,
            name=name,
            exclude_args=exclude_args,
            exclude_kwargs=exclude_kwargs,
            track_output=track_output,
            provenance_mode=provenance_mode,
            metadata=metadata,
        )

    def validation(
        self,
        name: str | None = None,
        *,
        exclude_args: list[int] | None = None,
        exclude_kwargs: list[str] | None = None,
        track_output: bool = True,
        provenance_mode: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        return validation_decorator(
            self.recorder,
            self.provenance_tracker,
            name=name,
            exclude_args=exclude_args,
            exclude_kwargs=exclude_kwargs,
            track_output=track_output,
            provenance_mode=provenance_mode,
            metadata=metadata,
        )

    def route(
        self,
        name: str | None = None,
        *,
        exclude_args: list[int] | None = None,
        exclude_kwargs: list[str] | None = None,
        track_output: bool = True,
        provenance_mode: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        return route_decorator(
            self.recorder,
            self.provenance_tracker,
            name=name,
            exclude_args=exclude_args,
            exclude_kwargs=exclude_kwargs,
            track_output=track_output,
            provenance_mode=provenance_mode,
            metadata=metadata,
        )

    def handoff(
        self,
        name: str | None = None,
        *,
        exclude_args: list[int] | None = None,
        exclude_kwargs: list[str] | None = None,
        track_output: bool = True,
        provenance_mode: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        return handoff_decorator(
            self.recorder,
            self.provenance_tracker,
            name=name,
            exclude_args=exclude_args,
            exclude_kwargs=exclude_kwargs,
            track_output=track_output,
            provenance_mode=provenance_mode,
            metadata=metadata,
        )

    def retry(
        self,
        name: str | None = None,
        *,
        exclude_args: list[int] | None = None,
        exclude_kwargs: list[str] | None = None,
        track_output: bool = True,
        provenance_mode: str | None = None,
        metadata: dict[str, Any] | None = None,
    ):
        return retry_decorator(
            self.recorder,
            self.provenance_tracker,
            name=name,
            exclude_args=exclude_args,
            exclude_kwargs=exclude_kwargs,
            track_output=track_output,
            provenance_mode=provenance_mode,
            metadata=metadata,
        )

    def graph_builder(self) -> GraphBuilder:
        return GraphBuilder(self.store)


class _DependencyContext:
    def __init__(
        self,
        tracker: ProvenanceTracker,
        values: tuple[Any, ...],
        event_ids: list[str] | tuple[str, ...] | None,
        reason: str,
    ) -> None:
        self.tracker = tracker
        self.values = values
        self.event_ids = event_ids
        self.reason = reason
        self._token = None

    def __enter__(self) -> "_DependencyContext":
        detail_refs, _ = collect_input_refs(
            self.tracker,
            depends_on_values=self.values,
            manual_input_refs=self.event_ids,
            include_context_refs=True,
        )
        detail_refs = [_replace_ref_reason(ref, self.reason) for ref in detail_refs]
        self._token = set_dependency_refs(tuple(detail_refs))
        return self

    def __exit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> bool:
        if self._token is not None:
            reset_dependency_refs(self._token)
        return False


def _metadata_with_provenance(metadata: dict[str, Any] | None, input_refs: Any) -> dict[str, Any]:
    event_metadata = dict(metadata or {})
    existing_provenance = event_metadata.get("provenance")
    provenance = dict(existing_provenance) if isinstance(existing_provenance, dict) else {}
    provenance["input_refs_detail"] = refs_to_dicts(input_refs)
    event_metadata["provenance"] = provenance
    return event_metadata


def _metadata_state_name(metadata: dict[str, Any] | None, state_name: str | None) -> str | None:
    if state_name is not None:
        return state_name
    if isinstance(metadata, dict):
        metadata_state_name = metadata.get(METADATA_STATE_NAME)
        if isinstance(metadata_state_name, str):
            return metadata_state_name
    return None


def _state_event_metadata(
    metadata: dict[str, Any] | None,
    *,
    operation: str,
    state_name: str,
    state_path: str,
    value: Any = None,
    before: Any = None,
    after: Any = None,
    hash_payload: Any = None,
) -> dict[str, Any]:
    event_metadata = dict(metadata or {})
    event_metadata[METADATA_OPERATION] = operation
    event_metadata[METADATA_STATE_NAME] = state_name
    event_metadata[METADATA_STATE_PATH] = state_path
    hasher = hash_payload or _hash_state_value
    if operation == "read":
        event_metadata[METADATA_VALUE_HASH] = hasher(value)
    else:
        event_metadata[METADATA_BEFORE_HASH] = hasher(before)
        event_metadata[METADATA_AFTER_HASH] = hasher(after)
    return event_metadata


def _hash_state_value(value: Any) -> str:
    return hash_serialized_payload(value)


def _cleanup_cutoff(older_than: str | timedelta | datetime) -> datetime:
    if isinstance(older_than, datetime):
        if older_than.tzinfo is None:
            return older_than.replace(tzinfo=timezone.utc)
        return older_than.astimezone(timezone.utc)
    if isinstance(older_than, timedelta):
        return datetime.now(timezone.utc) - older_than
    match = re.fullmatch(r"\s*(\d+)\s*([dhms])\s*", older_than)
    if match is None:
        raise ValueError("older_than must be a datetime, timedelta, or duration like '30d', '12h', '10m', or '60s'")
    amount = int(match.group(1))
    unit = match.group(2)
    if unit == "d":
        delta = timedelta(days=amount)
    elif unit == "h":
        delta = timedelta(hours=amount)
    elif unit == "m":
        delta = timedelta(minutes=amount)
    else:
        delta = timedelta(seconds=amount)
    return datetime.now(timezone.utc) - delta


def _replace_ref_reason(ref: ProvenanceRef, reason: str) -> ProvenanceRef:
    return ProvenanceRef(
        event_id=ref.event_id,
        path=ref.path,
        source_event_type=ref.source_event_type,
        source_event_name=ref.source_event_name,
        reason=reason,
        confidence=ref.confidence,
        metadata=ref.metadata,
    )
