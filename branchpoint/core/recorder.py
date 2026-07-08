"""Run and event recorder."""

from __future__ import annotations

import hashlib
import json
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
from .errors import NoActiveTraceError
from .event_store import EventStore
from .ids import new_event_id, new_run_id, new_span_id
from .schema import ERROR, RUNNING, SUCCESS, TraceEvent, TraceRun, utc_now_iso
from .serialization import safe_serialize
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
        run = TraceRun(
            run_id=new_run_id(),
            project_id=self.recorder.project_id,
            name=self.name,
            started_at=utc_now_iso(),
            status=RUNNING,
            metadata=safe_serialize(self.metadata),
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
            else:
                self.recorder.store.finish_run(self.active.run_id, ERROR)
        finally:
            if self._tokens is not None:
                reset_trace_context(self._tokens)
        return False


class Recorder:
    def __init__(self, project_id: str, store: EventStore, blob_store: BlobStore) -> None:
        self.project_id = project_id
        self.store = store
        self.blob_store = blob_store

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
        resolved_run_id = run_id or get_current_run_id()
        if resolved_run_id is None:
            raise NoActiveTraceError("emit requires an active trace or explicit run_id")
        resolved_project_id = project_id or get_current_project_id() or self.project_id
        event = TraceEvent(
            event_id=new_event_id(),
            run_id=resolved_run_id,
            project_id=resolved_project_id,
            type=type,
            name=name,
            parent_id=parent_id if parent_id is not None else get_current_parent_event_id(),
            span_id=span_id if span_id is not None else get_current_span_id(),
            timestamp_start=utc_now_iso(),
            input=safe_serialize(input),
            output=safe_serialize(output),
            input_refs=list(input_refs or []),
            output_refs=list(output_refs or []),
            status=status,
            metadata=safe_serialize(metadata or {}),
        )
        self._prepare_payloads(event)
        self.store.append_event(event)
        return event

    def _prepare_payloads(self, event: TraceEvent) -> None:
        if event.input is not None:
            event.input_hash = _hash_json(event.input)
            if self.blob_store.should_externalize(event.input):
                event.input_payload_ref = self.blob_store.put_json(event.run_id, event.event_id, "input", event.input)
                event.input = None
        if event.output is not None:
            event.output_hash = _hash_json(event.output)
            if self.blob_store.should_externalize(event.output):
                event.output_payload_ref = self.blob_store.put_json(event.run_id, event.event_id, "output", event.output)
                event.output = None

    def child_context(self, parent_id: str, span_id: str):
        return _ChildContext(parent_id=parent_id, span_id=span_id)


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


def _hash_json(value: Any) -> str:
    payload = json.dumps(safe_serialize(value), sort_keys=True).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def new_recording_span_id() -> str:
    return new_span_id()
