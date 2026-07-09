"""Run and event recorder."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Any

from .context import (
    get_current_parent_event_id,
    get_current_project_id,
    get_current_run_id,
    get_current_span_id,
    reset_parent_event,
    reset_span,
    reset_trace_context,
    set_parent_event,
    set_span,
    set_trace_context,
)
from .errors import EventContractError, NoActiveTraceError
from .event_store import EventStore
from .ids import new_event_id, new_run_id, new_snapshot_id, new_span_id
from .provenance import ProvenanceTracker
from .schema import (
    CANCELLED,
    ERROR,
    LLM_CALL,
    LLM_OUTPUT,
    METADATA_STATE_NAME,
    METADATA_STATE_PATH,
    RETRIEVAL_RESULT,
    RUNNING,
    SNAPSHOT_LLM_PROMPT,
    SNAPSHOT_LLM_RESPONSE,
    SNAPSHOT_RETRIEVAL_RESULT,
    SNAPSHOT_STATE_AFTER,
    SNAPSHOT_STATE_BEFORE,
    SNAPSHOT_STATE_DIFF,
    SNAPSHOT_TOOL_OUTPUT,
    STATE_WRITE,
    SUCCESS,
    TOOL_OUTPUT,
    Snapshot,
    TraceEvent,
    TraceRun,
    validate_event_contract,
    utc_now_iso,
)
from .snapshots import diff_json_like, link_snapshot_metadata, prepare_snapshot_payload
from .serialization import RedactionConfig, prepare_serialized_payload, safe_serialize_for_storage
from branchpoint.storage.blob_store import BlobStore


@dataclass(frozen=True)
class ActiveTrace:
    run_id: str
    project_id: str
    name: str | None


class TraceContext:
    def __init__(self, recorder: "Recorder", name: str | None, metadata: dict[str, Any] | None) -> None:
        self.recorder = recorder
        self.name = name
        self.metadata = metadata or {}
        self.active: ActiveTrace | None = None
        self._tokens = None

    def __enter__(self) -> ActiveTrace:
        metadata_result = safe_serialize_for_storage(self.metadata, redaction_config=self.recorder.redaction_config)
        run_metadata = metadata_result.value
        if metadata_result.redacted:
            run_metadata["metadata_redaction"] = metadata_result.metadata()
        run = TraceRun(
            run_id=new_run_id(),
            project_id=self.recorder.project_id,
            name=self.name,
            started_at=utc_now_iso(),
            status=RUNNING,
            metadata=run_metadata,
        )
        self.recorder.store.create_run(run)
        self._tokens = set_trace_context(run.run_id, run.project_id)
        self.active = ActiveTrace(run_id=run.run_id, project_id=run.project_id, name=run.name)
        return self.active

    def __exit__(self, exc_type: Any, exc: BaseException | None, tb: Any) -> bool:
        try:
            if self.active is None:
                return False
            if exc is None:
                self.recorder.store.finish_run(self.active.run_id, SUCCESS)
            elif exc_type is not None and issubclass(exc_type, asyncio.CancelledError):
                self.recorder.store.finish_run(self.active.run_id, CANCELLED)
            else:
                self.recorder.store.finish_run(self.active.run_id, ERROR)
        finally:
            if self._tokens is not None:
                reset_trace_context(self._tokens)
            if self.recorder.provenance_tracker is not None:
                self.recorder.provenance_tracker.clear()
        return False


class Recorder:
    def __init__(
        self,
        project_id: str,
        store: EventStore,
        blob_store: BlobStore,
        provenance_tracker: ProvenanceTracker | None = None,
        strict_event_types: bool = True,
        redaction_config: RedactionConfig | None = None,
        max_preview_chars: int = 2_000,
        max_blob_bytes: int | None = None,
    ) -> None:
        self.project_id = project_id
        self.store = store
        self.blob_store = blob_store
        self.provenance_tracker = provenance_tracker
        self.strict_event_types = strict_event_types
        self.redaction_config = redaction_config or RedactionConfig.from_rules()
        self.max_preview_chars = max_preview_chars
        self.max_blob_bytes = max_blob_bytes

    def trace(self, name: str | None = None, metadata: dict[str, Any] | None = None) -> TraceContext:
        return TraceContext(self, name=name, metadata=metadata)

    def emit(
        self,
        type: str,
        name: str | None = None,
        input: Any = None,
        output: Any = None,
        input_refs: list[str] | None = None,
        output_refs: list[str] | None = None,
        status: str = SUCCESS,
        metadata: dict[str, Any] | None = None,
        parent_id: str | None = None,
        span_id: str | None = None,
        run_id: str | None = None,
        project_id: str | None = None,
    ) -> TraceEvent:
        active_run_id = get_current_run_id()
        active_project_id = get_current_project_id()
        resolved_run_id = run_id or get_current_run_id()
        if resolved_run_id is None:
            raise NoActiveTraceError("emit requires an active trace or explicit run_id")
        if active_run_id is not None and run_id is not None and run_id != active_run_id:
            raise EventContractError("emit run_id cannot differ from the active trace run_id")
        if active_project_id is not None and project_id is not None and project_id != active_project_id:
            raise EventContractError("emit project_id cannot differ from the active trace project_id")
        resolved_project_id = project_id or active_project_id or self.project_id
        metadata_result = safe_serialize_for_storage(metadata or {}, redaction_config=self.redaction_config)
        event_metadata = metadata_result.value
        if metadata_result.redacted:
            event_metadata["metadata_redaction"] = metadata_result.metadata()
        validate_event_contract(
            type,
            status,
            event_metadata,
            strict_event_types=self.strict_event_types,
        )
        event = TraceEvent(
            event_id=new_event_id(),
            run_id=resolved_run_id,
            project_id=resolved_project_id,
            type=type,
            name=name,
            parent_id=parent_id if parent_id is not None else get_current_parent_event_id(),
            span_id=span_id if span_id is not None else get_current_span_id(),
            timestamp_start=utc_now_iso(),
            input=input,
            output=output,
            input_refs=list(input_refs or []),
            output_refs=list(output_refs or []),
            status=status,
            metadata=event_metadata,
        )
        snapshots = self._automatic_snapshots_for_event(event)
        for snapshot in snapshots:
            event.metadata = link_snapshot_metadata(event.metadata, snapshot)
        self._prepare_payloads(event)
        self.store.append_event(event)
        for snapshot in snapshots:
            self.store.append_snapshot(snapshot)
        return event

    def _prepare_payloads(self, event: TraceEvent) -> None:
        if event.input is not None:
            prepared = prepare_serialized_payload(
                event.input,
                redaction_config=self.redaction_config,
                max_preview_chars=self.max_preview_chars,
                max_blob_bytes=self.max_blob_bytes,
            )
            event.input_hash = prepared.payload_hash
            _record_payload_safety(event.metadata, "input", redaction=prepared.redaction, truncation=prepared.truncation)
            if self.blob_store.should_externalize(prepared.value):
                event.input_payload_ref = self.blob_store.put_json(event.run_id, event.event_id, "input", prepared.value)
                event.input = None
            else:
                event.input = prepared.value
        if event.output is not None:
            prepared = prepare_serialized_payload(
                event.output,
                redaction_config=self.redaction_config,
                max_preview_chars=self.max_preview_chars,
                max_blob_bytes=self.max_blob_bytes,
            )
            event.output_hash = prepared.payload_hash
            _record_payload_safety(event.metadata, "output", redaction=prepared.redaction, truncation=prepared.truncation)
            if self.blob_store.should_externalize(prepared.value):
                event.output_payload_ref = self.blob_store.put_json(event.run_id, event.event_id, "output", prepared.value)
                event.output = None
            else:
                event.output = prepared.value

    def _automatic_snapshots_for_event(self, event: TraceEvent) -> list[Snapshot]:
        snapshots: list[Snapshot] = []

        def add_snapshot(kind: str, payload: Any, *, name_suffix: str | None = None, metadata: dict[str, Any] | None = None) -> None:
            snapshot = Snapshot(
                snapshot_id=new_snapshot_id(),
                run_id=event.run_id,
                event_id=event.event_id,
                project_id=event.project_id,
                kind=kind,
                name=_snapshot_name(event.name, name_suffix),
                payload=payload,
                metadata={
                    "automatic": True,
                    "source_event_type": event.type,
                    **(metadata or {}),
                },
            )
            prepare_snapshot_payload(
                snapshot,
                self.blob_store,
                redaction_config=self.redaction_config,
                max_preview_chars=self.max_preview_chars,
                max_blob_bytes=self.max_blob_bytes,
            )
            snapshots.append(snapshot)

        if event.type == TOOL_OUTPUT and event.output is not None:
            add_snapshot(SNAPSHOT_TOOL_OUTPUT, event.output)
        elif event.type == RETRIEVAL_RESULT and event.output is not None:
            add_snapshot(SNAPSHOT_RETRIEVAL_RESULT, event.output)
        elif event.type == LLM_CALL and event.input is not None:
            add_snapshot(SNAPSHOT_LLM_PROMPT, event.input)
        elif event.type == LLM_OUTPUT and event.output is not None:
            add_snapshot(SNAPSHOT_LLM_RESPONSE, event.output)
        elif event.type == STATE_WRITE:
            state_metadata = _state_snapshot_metadata(event)
            before = event.input.get("before") if isinstance(event.input, dict) else None
            after = event.output
            add_snapshot(SNAPSHOT_STATE_BEFORE, before, name_suffix="before", metadata=state_metadata)
            add_snapshot(SNAPSHOT_STATE_AFTER, after, name_suffix="after", metadata=state_metadata)
            add_snapshot(SNAPSHOT_STATE_DIFF, diff_json_like(before, after), name_suffix="diff", metadata=state_metadata)

        return snapshots

    def child_context(self, parent_id: str, span_id: str):
        return _ChildContext(parent_id=parent_id, span_id=span_id)

    def hash_payload(self, value: Any) -> str:
        prepared = prepare_serialized_payload(
            value,
            redaction_config=self.redaction_config,
            max_preview_chars=self.max_preview_chars,
            max_blob_bytes=self.max_blob_bytes,
        )
        return prepared.payload_hash


class _ChildContext:
    def __init__(self, parent_id: str, span_id: str) -> None:
        self.parent_id = parent_id
        self.span_id = span_id
        self._parent_token = None
        self._span_token = None

    def __enter__(self) -> None:
        self._parent_token = set_parent_event(self.parent_id)
        self._span_token = set_span(self.span_id)

    def __exit__(self, exc_type: Any, exc: Any, tb: Any) -> bool:
        if self._span_token is not None:
            reset_span(self._span_token)
        if self._parent_token is not None:
            reset_parent_event(self._parent_token)
        return False


def _record_payload_safety(
    metadata: dict[str, Any],
    channel: str,
    *,
    redaction: dict[str, Any],
    truncation: dict[str, Any],
) -> None:
    if not redaction.get("redacted") and not truncation.get("truncated"):
        return
    safety = metadata.get("payload_safety")
    if not isinstance(safety, dict):
        safety = {}
    channel_safety = dict(safety.get(channel) or {})
    if redaction.get("redacted"):
        channel_safety["redaction"] = redaction
    if truncation.get("truncated"):
        channel_safety["truncation"] = truncation
    safety[channel] = channel_safety
    metadata["payload_safety"] = safety


def _snapshot_name(event_name: str | None, suffix: str | None) -> str | None:
    if event_name is None:
        return suffix
    if suffix is None:
        return event_name
    return f"{event_name}.{suffix}"


def _state_snapshot_metadata(event: TraceEvent) -> dict[str, Any]:
    metadata: dict[str, Any] = {}
    state_name = event.metadata.get(METADATA_STATE_NAME)
    state_path = event.metadata.get(METADATA_STATE_PATH)
    if state_name is not None:
        metadata[METADATA_STATE_NAME] = state_name
    if state_path is not None:
        metadata[METADATA_STATE_PATH] = state_path
    return metadata


def new_recording_span_id() -> str:
    return new_span_id()
