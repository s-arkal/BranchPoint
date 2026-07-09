"""Generic instrumentation decorators."""

from __future__ import annotations

import asyncio
import functools
import inspect
import time
from dataclasses import dataclass
from typing import Any, Callable, TypeVar

from branchpoint.core.ids import new_span_id
from branchpoint.core.provenance import ProvenanceTracker
from branchpoint.core.recorder import Recorder
from branchpoint.core.refs import collect_input_refs, refs_to_dicts
from branchpoint.core.schema import (
    ERROR,
    CANCELLED,
    HANDOFF,
    LLM_CALL,
    LLM_OUTPUT,
    MEMORY_READ,
    MEMORY_WRITE,
    RETRIEVAL_QUERY,
    RETRIEVAL_RESULT,
    RETRY,
    ROUTING_DECISION,
    STATE_READ,
    STATE_WRITE,
    SUCCESS,
    TIMEOUT,
    TOOL_CALL,
    TOOL_OUTPUT,
    VALIDATION_CHECK,
    canonical_state_name,
    canonical_state_path,
    error_metadata,
    utc_now_iso,
)
from branchpoint.core.serialization import safe_serialize

F = TypeVar("F", bound=Callable[..., Any])

RESERVED_KWARGS = frozenset({"bp_input_refs", "bp_depends_on", "bp_metadata", "bp_no_track"})


def tool_decorator(
    recorder: Recorder,
    tracker: ProvenanceTracker,
    name: str | None = None,
    *,
    exclude_args: list[int] | None = None,
    exclude_kwargs: list[str] | None = None,
    track_output: bool = True,
    provenance_mode: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    return _call_output_pair(
        recorder,
        tracker,
        TOOL_CALL,
        TOOL_OUTPUT,
        name=name,
        exclude_args=exclude_args,
        exclude_kwargs=exclude_kwargs,
        track_output=track_output,
        provenance_mode=provenance_mode,
        metadata=metadata,
    )


def llm_decorator(
    recorder: Recorder,
    tracker: ProvenanceTracker,
    name: str | None = None,
    *,
    exclude_args: list[int] | None = None,
    exclude_kwargs: list[str] | None = None,
    track_output: bool = True,
    provenance_mode: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    return _call_output_pair(
        recorder,
        tracker,
        LLM_CALL,
        LLM_OUTPUT,
        name=name,
        exclude_args=exclude_args,
        exclude_kwargs=exclude_kwargs,
        track_output=track_output,
        provenance_mode=provenance_mode,
        metadata=metadata,
        metadata_builder=_llm_metadata,
    )


def retrieval_decorator(
    recorder: Recorder,
    tracker: ProvenanceTracker,
    name: str | None = None,
    *,
    exclude_args: list[int] | None = None,
    exclude_kwargs: list[str] | None = None,
    track_output: bool = True,
    provenance_mode: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    return _call_output_pair(
        recorder,
        tracker,
        RETRIEVAL_QUERY,
        RETRIEVAL_RESULT,
        name=name,
        exclude_args=exclude_args,
        exclude_kwargs=exclude_kwargs,
        track_output=track_output,
        provenance_mode=provenance_mode,
        metadata=metadata,
    )


def validation_decorator(
    recorder: Recorder,
    tracker: ProvenanceTracker,
    name: str | None = None,
    *,
    exclude_args: list[int] | None = None,
    exclude_kwargs: list[str] | None = None,
    track_output: bool = True,
    provenance_mode: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    return _single_event(
        recorder,
        tracker,
        VALIDATION_CHECK,
        {"operation": "validation"},
        name=name,
        exclude_args=exclude_args,
        exclude_kwargs=exclude_kwargs,
        track_output=track_output,
        provenance_mode=provenance_mode,
        metadata=metadata,
    )


def route_decorator(
    recorder: Recorder,
    tracker: ProvenanceTracker,
    name: str | None = None,
    *,
    exclude_args: list[int] | None = None,
    exclude_kwargs: list[str] | None = None,
    track_output: bool = True,
    provenance_mode: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    return _single_event(
        recorder,
        tracker,
        ROUTING_DECISION,
        {"operation": "route"},
        name=name,
        exclude_args=exclude_args,
        exclude_kwargs=exclude_kwargs,
        track_output=track_output,
        provenance_mode=provenance_mode,
        metadata=metadata,
    )


def handoff_decorator(
    recorder: Recorder,
    tracker: ProvenanceTracker,
    name: str | None = None,
    *,
    exclude_args: list[int] | None = None,
    exclude_kwargs: list[str] | None = None,
    track_output: bool = True,
    provenance_mode: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    return _single_event(
        recorder,
        tracker,
        HANDOFF,
        {"operation": "handoff"},
        name=name,
        exclude_args=exclude_args,
        exclude_kwargs=exclude_kwargs,
        track_output=track_output,
        provenance_mode=provenance_mode,
        metadata=metadata,
    )


def retry_decorator(
    recorder: Recorder,
    tracker: ProvenanceTracker,
    name: str | None = None,
    *,
    exclude_args: list[int] | None = None,
    exclude_kwargs: list[str] | None = None,
    track_output: bool = True,
    provenance_mode: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    return _single_event(
        recorder,
        tracker,
        RETRY,
        {"operation": "retry"},
        name=name,
        exclude_args=exclude_args,
        exclude_kwargs=exclude_kwargs,
        track_output=track_output,
        provenance_mode=provenance_mode,
        metadata=metadata,
    )


def memory_read_decorator(
    recorder: Recorder,
    tracker: ProvenanceTracker,
    name: str | None = None,
    *,
    exclude_args: list[int] | None = None,
    exclude_kwargs: list[str] | None = None,
    track_output: bool = True,
    provenance_mode: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    return _memory_event(
        recorder,
        tracker,
        MEMORY_READ,
        "read",
        name=name,
        exclude_args=exclude_args,
        exclude_kwargs=exclude_kwargs,
        track_output=track_output,
        provenance_mode=provenance_mode,
        metadata=metadata,
    )


def memory_write_decorator(
    recorder: Recorder,
    tracker: ProvenanceTracker,
    name: str | None = None,
    *,
    exclude_args: list[int] | None = None,
    exclude_kwargs: list[str] | None = None,
    track_output: bool = True,
    provenance_mode: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    return _memory_event(
        recorder,
        tracker,
        MEMORY_WRITE,
        "write",
        name=name,
        exclude_args=exclude_args,
        exclude_kwargs=exclude_kwargs,
        track_output=track_output,
        provenance_mode=provenance_mode,
        metadata=metadata,
    )


def state_read_decorator(
    recorder: Recorder,
    tracker: ProvenanceTracker,
    path: str | list[Any] | tuple[Any, ...],
    state_name: str | None = None,
    name: str | None = None,
    *,
    exclude_args: list[int] | None = None,
    exclude_kwargs: list[str] | None = None,
    track_output: bool = True,
    provenance_mode: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    return _state_event(
        recorder,
        tracker,
        STATE_READ,
        "read",
        path,
        state_name,
        name=name,
        exclude_args=exclude_args,
        exclude_kwargs=exclude_kwargs,
        track_output=track_output,
        provenance_mode=provenance_mode,
        metadata=metadata,
    )


def state_write_decorator(
    recorder: Recorder,
    tracker: ProvenanceTracker,
    path: str | list[Any] | tuple[Any, ...],
    state_name: str | None = None,
    name: str | None = None,
    *,
    exclude_args: list[int] | None = None,
    exclude_kwargs: list[str] | None = None,
    track_output: bool = True,
    provenance_mode: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    return _state_event(
        recorder,
        tracker,
        STATE_WRITE,
        "write",
        path,
        state_name,
        name=name,
        exclude_args=exclude_args,
        exclude_kwargs=exclude_kwargs,
        track_output=track_output,
        provenance_mode=provenance_mode,
        metadata=metadata,
    )


def _call_output_pair(
    recorder: Recorder,
    tracker: ProvenanceTracker,
    call_type: str,
    output_type: str,
    *,
    name: str | None = None,
    exclude_args: list[int] | None = None,
    exclude_kwargs: list[str] | None = None,
    track_output: bool = True,
    provenance_mode: str | None = None,
    metadata: dict[str, Any] | None = None,
    metadata_builder: Callable[[dict[str, Any]], dict[str, Any]] | None = None,
) -> Callable[[F], F]:
    def decorate(func: F) -> F:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                start = _start_timing()
                call_event, span_id, upstream_refs, upstream_ids = _emit_call_event(
                    recorder,
                    tracker,
                    call_type,
                    name or func.__name__,
                    args,
                    kwargs,
                    exclude_args,
                    exclude_kwargs,
                    metadata,
                    metadata_builder,
                    timestamp_start=start.timestamp_start,
                )
                call_kwargs, bp_metadata, no_track = _split_reserved_kwargs(kwargs)
                try:
                    with recorder.child_context(call_event.event_id, span_id):
                        result = await func(*args, **call_kwargs)
                except asyncio.CancelledError as exc:
                    _emit_output_event(
                        recorder,
                        output_type,
                        name or func.__name__,
                        call_event.event_id,
                        span_id,
                        upstream_ids,
                        upstream_refs,
                        output=safe_serialize(exc),
                        status=CANCELLED,
                        metadata=metadata,
                        bp_metadata=_metadata_with_exception(bp_metadata, exc, status=CANCELLED),
                        timing=start.finish(),
                        dynamic_metadata=_llm_output_metadata(call_kwargs, metadata, bp_metadata)
                        if output_type == LLM_OUTPUT
                        else None,
                    )
                    raise
                except Exception as exc:
                    status = _exception_status(exc)
                    _emit_output_event(
                        recorder,
                        output_type,
                        name or func.__name__,
                        call_event.event_id,
                        span_id,
                        upstream_ids,
                        upstream_refs,
                        output=safe_serialize(exc),
                        status=status,
                        metadata=metadata,
                        bp_metadata=_metadata_with_exception(bp_metadata, exc, status=status),
                        timing=start.finish(),
                        dynamic_metadata=_llm_output_metadata(call_kwargs, metadata, bp_metadata)
                        if output_type == LLM_OUTPUT
                        else None,
                    )
                    raise
                streaming_metadata = _llm_output_metadata(call_kwargs, metadata, bp_metadata) if output_type == LLM_OUTPUT else {}
                if streaming_metadata.get("streaming") and _is_sync_stream(result):
                    return _wrap_sync_stream(
                        result,
                        recorder=recorder,
                        tracker=tracker,
                        output_type=output_type,
                        name=name or func.__name__,
                        call_event_id=call_event.event_id,
                        span_id=span_id,
                        upstream_ids=upstream_ids,
                        upstream_refs=upstream_refs,
                        metadata=metadata,
                        bp_metadata=bp_metadata,
                        timing=start,
                        dynamic_metadata=streaming_metadata,
                        track_output=track_output and not no_track,
                        provenance_mode=provenance_mode,
                    )
                if streaming_metadata.get("streaming") and _is_async_stream(result):
                    return _wrap_async_stream(
                        result,
                        recorder=recorder,
                        tracker=tracker,
                        output_type=output_type,
                        name=name or func.__name__,
                        call_event_id=call_event.event_id,
                        span_id=span_id,
                        upstream_ids=upstream_ids,
                        upstream_refs=upstream_refs,
                        metadata=metadata,
                        bp_metadata=bp_metadata,
                        timing=start,
                        dynamic_metadata=streaming_metadata,
                        track_output=track_output and not no_track,
                        provenance_mode=provenance_mode,
                    )
                output_event = _emit_output_event(
                    recorder,
                    output_type,
                    name or func.__name__,
                    call_event.event_id,
                    span_id,
                    upstream_ids,
                    upstream_refs,
                    output=result,
                    status=SUCCESS,
                    metadata=metadata,
                    bp_metadata=bp_metadata,
                    timing=start.finish(),
                    dynamic_metadata=streaming_metadata,
                )
                if track_output and not no_track:
                    result = tracker.attach(result, output_event, provenance_mode=provenance_mode)
                return result

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = _start_timing()
            call_event, span_id, upstream_refs, upstream_ids = _emit_call_event(
                recorder,
                tracker,
                call_type,
                name or func.__name__,
                args,
                kwargs,
                exclude_args,
                exclude_kwargs,
                metadata,
                metadata_builder,
                timestamp_start=start.timestamp_start,
            )
            call_kwargs, bp_metadata, no_track = _split_reserved_kwargs(kwargs)
            try:
                with recorder.child_context(call_event.event_id, span_id):
                    result = func(*args, **call_kwargs)
            except asyncio.CancelledError as exc:
                _emit_output_event(
                    recorder,
                    output_type,
                    name or func.__name__,
                    call_event.event_id,
                    span_id,
                    upstream_ids,
                    upstream_refs,
                    output=safe_serialize(exc),
                    status=CANCELLED,
                    metadata=metadata,
                    bp_metadata=_metadata_with_exception(bp_metadata, exc, status=CANCELLED),
                    timing=start.finish(),
                    dynamic_metadata=_llm_output_metadata(call_kwargs, metadata, bp_metadata)
                    if output_type == LLM_OUTPUT
                    else None,
                )
                raise
            except Exception as exc:
                status = _exception_status(exc)
                _emit_output_event(
                    recorder,
                    output_type,
                    name or func.__name__,
                    call_event.event_id,
                    span_id,
                    upstream_ids,
                    upstream_refs,
                    output=safe_serialize(exc),
                    status=status,
                    metadata=metadata,
                    bp_metadata=_metadata_with_exception(bp_metadata, exc, status=status),
                    timing=start.finish(),
                    dynamic_metadata=_llm_output_metadata(call_kwargs, metadata, bp_metadata)
                    if output_type == LLM_OUTPUT
                    else None,
                )
                raise
            streaming_metadata = _llm_output_metadata(call_kwargs, metadata, bp_metadata) if output_type == LLM_OUTPUT else {}
            if streaming_metadata.get("streaming") and _is_sync_stream(result):
                return _wrap_sync_stream(
                    result,
                    recorder=recorder,
                    tracker=tracker,
                    output_type=output_type,
                    name=name or func.__name__,
                    call_event_id=call_event.event_id,
                    span_id=span_id,
                    upstream_ids=upstream_ids,
                    upstream_refs=upstream_refs,
                    metadata=metadata,
                    bp_metadata=bp_metadata,
                    timing=start,
                    dynamic_metadata=streaming_metadata,
                    track_output=track_output and not no_track,
                    provenance_mode=provenance_mode,
                )
            output_event = _emit_output_event(
                recorder,
                output_type,
                name or func.__name__,
                call_event.event_id,
                span_id,
                upstream_ids,
                upstream_refs,
                output=result,
                status=SUCCESS,
                metadata=metadata,
                bp_metadata=bp_metadata,
                timing=start.finish(),
                dynamic_metadata=streaming_metadata,
            )
            if track_output and not no_track:
                result = tracker.attach(result, output_event, provenance_mode=provenance_mode)
            return result

        return wrapper  # type: ignore[return-value]

    return decorate


def _memory_event(
    recorder: Recorder,
    tracker: ProvenanceTracker,
    event_type: str,
    operation: str,
    *,
    name: str | None = None,
    exclude_args: list[int] | None = None,
    exclude_kwargs: list[str] | None = None,
    track_output: bool = True,
    provenance_mode: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    return _single_event(
        recorder,
        tracker,
        event_type,
        lambda args, call_kwargs, result: _memory_metadata(operation, args, call_kwargs),
        name=name,
        exclude_args=exclude_args,
        exclude_kwargs=exclude_kwargs,
        track_output=track_output,
        provenance_mode=provenance_mode,
        metadata=metadata,
    )


def _single_event(
    recorder: Recorder,
    tracker: ProvenanceTracker,
    event_type: str,
    dynamic_metadata: dict[str, Any] | Callable[[tuple[Any, ...], dict[str, Any], Any], dict[str, Any]],
    *,
    name: str | None = None,
    exclude_args: list[int] | None = None,
    exclude_kwargs: list[str] | None = None,
    track_output: bool = True,
    provenance_mode: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    def decorate(func: F) -> F:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                start = _start_timing()
                call_kwargs, bp_metadata, no_track = _split_reserved_kwargs(kwargs)
                upstream_refs, upstream_ids = collect_input_refs(
                    tracker,
                    args=args,
                    kwargs=call_kwargs,
                    exclude_args=exclude_args,
                    exclude_kwargs=exclude_kwargs,
                    manual_input_refs=kwargs.get("bp_input_refs"),
                    depends_on_values=kwargs.get("bp_depends_on"),
                )
                try:
                    result = await func(*args, **call_kwargs)
                except asyncio.CancelledError as exc:
                    _emit_single_event(
                        recorder,
                        event_type,
                        name or func.__name__,
                        args,
                        call_kwargs,
                        safe_serialize(exc),
                        upstream_ids,
                        upstream_refs,
                        metadata,
                        bp_metadata,
                        dynamic_metadata,
                        start.finish(),
                        status=CANCELLED,
                        exc=exc,
                    )
                    raise
                except Exception as exc:
                    status = _exception_status(exc)
                    _emit_single_event(
                        recorder,
                        event_type,
                        name or func.__name__,
                        args,
                        call_kwargs,
                        safe_serialize(exc),
                        upstream_ids,
                        upstream_refs,
                        metadata,
                        bp_metadata,
                        dynamic_metadata,
                        start.finish(),
                        status=status,
                        exc=exc,
                    )
                    raise
                event = _emit_single_event(
                    recorder,
                    event_type,
                    name or func.__name__,
                    args,
                    call_kwargs,
                    result,
                    upstream_ids,
                    upstream_refs,
                    metadata,
                    bp_metadata,
                    dynamic_metadata,
                    start.finish(),
                    status=SUCCESS,
                )
                if track_output and not no_track:
                    result = tracker.attach(result, event, provenance_mode=provenance_mode)
                return result

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = _start_timing()
            call_kwargs, bp_metadata, no_track = _split_reserved_kwargs(kwargs)
            upstream_refs, upstream_ids = collect_input_refs(
                tracker,
                args=args,
                kwargs=call_kwargs,
                exclude_args=exclude_args,
                exclude_kwargs=exclude_kwargs,
                manual_input_refs=kwargs.get("bp_input_refs"),
                depends_on_values=kwargs.get("bp_depends_on"),
            )
            try:
                result = func(*args, **call_kwargs)
            except asyncio.CancelledError as exc:
                _emit_single_event(
                    recorder,
                    event_type,
                    name or func.__name__,
                    args,
                    call_kwargs,
                    safe_serialize(exc),
                    upstream_ids,
                    upstream_refs,
                    metadata,
                    bp_metadata,
                    dynamic_metadata,
                    start.finish(),
                    status=CANCELLED,
                    exc=exc,
                )
                raise
            except Exception as exc:
                status = _exception_status(exc)
                _emit_single_event(
                    recorder,
                    event_type,
                    name or func.__name__,
                    args,
                    call_kwargs,
                    safe_serialize(exc),
                    upstream_ids,
                    upstream_refs,
                    metadata,
                    bp_metadata,
                    dynamic_metadata,
                    start.finish(),
                    status=status,
                    exc=exc,
                )
                raise
            event = _emit_single_event(
                recorder,
                event_type,
                name or func.__name__,
                args,
                call_kwargs,
                result,
                upstream_ids,
                upstream_refs,
                metadata,
                bp_metadata,
                dynamic_metadata,
                start.finish(),
                status=SUCCESS,
            )
            if track_output and not no_track:
                result = tracker.attach(result, event, provenance_mode=provenance_mode)
            return result

        return wrapper  # type: ignore[return-value]

    return decorate


def _state_event(
    recorder: Recorder,
    tracker: ProvenanceTracker,
    event_type: str,
    operation: str,
    path: str | list[Any] | tuple[Any, ...],
    state_name: str | None,
    *,
    name: str | None = None,
    exclude_args: list[int] | None = None,
    exclude_kwargs: list[str] | None = None,
    track_output: bool = True,
    provenance_mode: str | None = None,
    metadata: dict[str, Any] | None = None,
) -> Callable[[F], F]:
    resolved_state_name = canonical_state_name(_metadata_state_name(metadata, state_name))
    resolved_state_path = canonical_state_path(path)

    def decorate(func: F) -> F:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
                start = _start_timing()
                call_kwargs, bp_metadata, no_track = _split_reserved_kwargs(kwargs)
                upstream_refs, upstream_ids = collect_input_refs(
                    tracker,
                    args=args,
                    kwargs=call_kwargs,
                    exclude_args=exclude_args,
                    exclude_kwargs=exclude_kwargs,
                    manual_input_refs=kwargs.get("bp_input_refs"),
                    depends_on_values=kwargs.get("bp_depends_on"),
                )
                try:
                    result = await func(*args, **call_kwargs)
                except asyncio.CancelledError as exc:
                    timing = start.finish()
                    recorder.emit(
                        type=event_type,
                        name=name or func.__name__,
                        input={"args": args, "kwargs": call_kwargs},
                        output=safe_serialize(exc),
                        input_refs=upstream_ids,
                        status=CANCELLED,
                        metadata=_with_timing(
                            _metadata_with_exception(
                                _state_metadata(
                                    recorder,
                                    metadata,
                                    operation,
                                    resolved_state_name,
                                    resolved_state_path,
                                    upstream_refs,
                                    bp_metadata,
                                    result=None,
                                    call_kwargs=call_kwargs,
                                ),
                                exc,
                                status=CANCELLED,
                            ),
                            timing,
                        ),
                        timestamp_start=start.timestamp_start,
                        timestamp_end=timing.timestamp_end,
                    )
                    raise
                except Exception as exc:
                    status = _exception_status(exc)
                    timing = start.finish()
                    recorder.emit(
                        type=event_type,
                        name=name or func.__name__,
                        input={"args": args, "kwargs": call_kwargs},
                        output=safe_serialize(exc),
                        input_refs=upstream_ids,
                        status=status,
                        metadata=_with_timing(
                            _metadata_with_exception(
                                _state_metadata(
                                    recorder,
                                    metadata,
                                    operation,
                                    resolved_state_name,
                                    resolved_state_path,
                                    upstream_refs,
                                    bp_metadata,
                                    result=None,
                                    call_kwargs=call_kwargs,
                                ),
                                exc,
                                status=status,
                            ),
                            timing,
                        ),
                        timestamp_start=start.timestamp_start,
                        timestamp_end=timing.timestamp_end,
                    )
                    raise
                timing = start.finish()
                event = recorder.emit(
                    type=event_type,
                    name=name or func.__name__,
                    input=_state_input_payload(operation, resolved_state_name, resolved_state_path, args, call_kwargs),
                    output=result,
                    input_refs=upstream_ids,
                    status=SUCCESS,
                    metadata=_with_timing(
                        _state_metadata(
                            recorder,
                            metadata,
                            operation,
                            resolved_state_name,
                            resolved_state_path,
                            upstream_refs,
                            bp_metadata,
                            result=result,
                            call_kwargs=call_kwargs,
                        ),
                        timing,
                    ),
                    timestamp_start=start.timestamp_start,
                    timestamp_end=timing.timestamp_end,
                )
                if track_output and not no_track:
                    result = tracker.attach(result, event, provenance_mode=provenance_mode)
                return result

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            start = _start_timing()
            call_kwargs, bp_metadata, no_track = _split_reserved_kwargs(kwargs)
            upstream_refs, upstream_ids = collect_input_refs(
                tracker,
                args=args,
                kwargs=call_kwargs,
                exclude_args=exclude_args,
                exclude_kwargs=exclude_kwargs,
                manual_input_refs=kwargs.get("bp_input_refs"),
                depends_on_values=kwargs.get("bp_depends_on"),
            )
            try:
                result = func(*args, **call_kwargs)
            except asyncio.CancelledError as exc:
                timing = start.finish()
                recorder.emit(
                    type=event_type,
                    name=name or func.__name__,
                    input={"args": args, "kwargs": call_kwargs},
                    output=safe_serialize(exc),
                    input_refs=upstream_ids,
                    status=CANCELLED,
                    metadata=_with_timing(
                        _metadata_with_exception(
                            _state_metadata(
                                recorder,
                                metadata,
                                operation,
                                resolved_state_name,
                                resolved_state_path,
                                upstream_refs,
                                bp_metadata,
                                result=None,
                                call_kwargs=call_kwargs,
                            ),
                            exc,
                            status=CANCELLED,
                        ),
                        timing,
                    ),
                    timestamp_start=start.timestamp_start,
                    timestamp_end=timing.timestamp_end,
                )
                raise
            except Exception as exc:
                status = _exception_status(exc)
                timing = start.finish()
                recorder.emit(
                    type=event_type,
                    name=name or func.__name__,
                    input={"args": args, "kwargs": call_kwargs},
                    output=safe_serialize(exc),
                    input_refs=upstream_ids,
                    status=status,
                    metadata=_with_timing(
                        _metadata_with_exception(
                            _state_metadata(
                                recorder,
                                metadata,
                                operation,
                                resolved_state_name,
                                resolved_state_path,
                                upstream_refs,
                                bp_metadata,
                                result=None,
                                call_kwargs=call_kwargs,
                            ),
                            exc,
                            status=status,
                        ),
                        timing,
                    ),
                    timestamp_start=start.timestamp_start,
                    timestamp_end=timing.timestamp_end,
                )
                raise
            timing = start.finish()
            event = recorder.emit(
                type=event_type,
                name=name or func.__name__,
                input=_state_input_payload(operation, resolved_state_name, resolved_state_path, args, call_kwargs),
                output=result,
                input_refs=upstream_ids,
                status=SUCCESS,
                metadata=_with_timing(
                    _state_metadata(
                        recorder,
                        metadata,
                        operation,
                        resolved_state_name,
                        resolved_state_path,
                        upstream_refs,
                        bp_metadata,
                        result=result,
                        call_kwargs=call_kwargs,
                    ),
                    timing,
                ),
                timestamp_start=start.timestamp_start,
                timestamp_end=timing.timestamp_end,
            )
            if track_output and not no_track:
                result = tracker.attach(result, event, provenance_mode=provenance_mode)
            return result

        return wrapper  # type: ignore[return-value]

    return decorate


def _emit_call_event(
    recorder: Recorder,
    tracker: ProvenanceTracker,
    call_type: str,
    name: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
    exclude_args: list[int] | None,
    exclude_kwargs: list[str] | None,
    metadata: dict[str, Any] | None,
    metadata_builder: Callable[[dict[str, Any]], dict[str, Any]] | None,
    *,
    timestamp_start: str | None = None,
) -> tuple[Any, str, Any, list[str]]:
    call_kwargs, bp_metadata, _ = _split_reserved_kwargs(kwargs)
    upstream_refs, upstream_ids = collect_input_refs(
        tracker,
        args=args,
        kwargs=call_kwargs,
        exclude_args=exclude_args,
        exclude_kwargs=exclude_kwargs,
        manual_input_refs=kwargs.get("bp_input_refs"),
        depends_on_values=kwargs.get("bp_depends_on"),
    )
    dynamic_metadata = metadata_builder(call_kwargs) if metadata_builder is not None else {}
    event_metadata = _event_metadata(metadata, dynamic_metadata, upstream_refs, bp_metadata)
    span_id = new_span_id()
    call_event = recorder.emit(
        type=call_type,
        name=name,
        input={"args": args, "kwargs": call_kwargs},
        input_refs=upstream_ids,
        status=SUCCESS,
        span_id=span_id,
        metadata=event_metadata,
        timestamp_start=timestamp_start,
    )
    return call_event, span_id, upstream_refs, upstream_ids


def _emit_output_event(
    recorder: Recorder,
    output_type: str,
    name: str,
    call_event_id: str,
    span_id: str,
    upstream_ids: list[str],
    upstream_refs: Any,
    *,
    output: Any,
    status: str,
    metadata: dict[str, Any] | None,
    bp_metadata: dict[str, Any] | None = None,
    timing: "_FinishedTiming | None" = None,
    dynamic_metadata: dict[str, Any] | None = None,
) -> Any:
    event_metadata = _event_metadata(metadata, dynamic_metadata or {}, upstream_refs, bp_metadata)
    if timing is not None:
        event_metadata = _with_timing(event_metadata, timing)
    return recorder.emit(
        type=output_type,
        name=name,
        output=output,
        input_refs=_unique_ids([call_event_id, *upstream_ids]),
        status=status,
        parent_id=call_event_id,
        span_id=span_id,
        metadata=event_metadata,
        timestamp_end=timing.timestamp_end if timing is not None else None,
    )


@dataclass(frozen=True)
class _StartedTiming:
    timestamp_start: str
    perf_start: float

    def finish(self) -> "_FinishedTiming":
        return _FinishedTiming(
            timestamp_start=self.timestamp_start,
            timestamp_end=utc_now_iso(),
            latency_ms=max(0.0, round((time.perf_counter() - self.perf_start) * 1000, 3)),
        )


@dataclass(frozen=True)
class _FinishedTiming:
    timestamp_start: str
    timestamp_end: str
    latency_ms: float


def _start_timing() -> _StartedTiming:
    return _StartedTiming(timestamp_start=utc_now_iso(), perf_start=time.perf_counter())


def _with_timing(metadata: dict[str, Any], timing: _FinishedTiming) -> dict[str, Any]:
    event_metadata = dict(metadata)
    event_metadata["timestamp_start"] = timing.timestamp_start
    event_metadata["timestamp_end"] = timing.timestamp_end
    event_metadata["latency_ms"] = timing.latency_ms
    return event_metadata


def _emit_single_event(
    recorder: Recorder,
    event_type: str,
    name: str,
    args: tuple[Any, ...],
    call_kwargs: dict[str, Any],
    output: Any,
    upstream_ids: list[str],
    upstream_refs: Any,
    static_metadata: dict[str, Any] | None,
    bp_metadata: dict[str, Any] | None,
    dynamic_metadata: dict[str, Any] | Callable[[tuple[Any, ...], dict[str, Any], Any], dict[str, Any]],
    timing: _FinishedTiming,
    *,
    status: str,
    exc: BaseException | None = None,
) -> Any:
    resolved_dynamic_metadata = (
        dynamic_metadata(args, call_kwargs, output) if callable(dynamic_metadata) else dynamic_metadata
    )
    event_metadata = _event_metadata(static_metadata, resolved_dynamic_metadata, upstream_refs, bp_metadata)
    if exc is not None:
        event_metadata = _metadata_with_exception(event_metadata, exc, status=status)
    event_metadata = _with_timing(event_metadata, timing)
    return recorder.emit(
        type=event_type,
        name=name,
        input={"args": args, "kwargs": call_kwargs},
        output=output,
        input_refs=upstream_ids,
        status=status,
        metadata=event_metadata,
        timestamp_start=timing.timestamp_start,
        timestamp_end=timing.timestamp_end,
    )


def _is_timeout(exc: BaseException) -> bool:
    return isinstance(exc, TimeoutError)


def _exception_status(exc: BaseException) -> str:
    return TIMEOUT if _is_timeout(exc) else ERROR


def _metadata_with_exception(metadata: dict[str, Any] | None, exc: BaseException, *, status: str) -> dict[str, Any]:
    event_metadata = dict(metadata or {})
    _merge_metadata(event_metadata, error_metadata(exc))
    if status == TIMEOUT:
        event_metadata["timeout"] = True
    if status == CANCELLED:
        event_metadata["cancelled"] = True
    return event_metadata


def _is_sync_stream(value: Any) -> bool:
    return (
        hasattr(value, "__iter__")
        and not isinstance(value, (str, bytes, bytearray, dict, list, tuple, set, frozenset))
    )


def _is_async_stream(value: Any) -> bool:
    return hasattr(value, "__aiter__")


def _wrap_sync_stream(
    stream: Any,
    *,
    recorder: Recorder,
    tracker: ProvenanceTracker,
    output_type: str,
    name: str,
    call_event_id: str,
    span_id: str,
    upstream_ids: list[str],
    upstream_refs: Any,
    metadata: dict[str, Any] | None,
    bp_metadata: dict[str, Any] | None,
    timing: _StartedTiming,
    dynamic_metadata: dict[str, Any],
    track_output: bool,
    provenance_mode: str | None,
) -> Any:
    def generator():
        chunks: list[Any] = []
        try:
            with recorder.child_context(call_event_id, span_id):
                for chunk in stream:
                    chunks.append(chunk)
                    yield chunk
        except asyncio.CancelledError as exc:
            _emit_output_event(
                recorder,
                output_type,
                name,
                call_event_id,
                span_id,
                upstream_ids,
                upstream_refs,
                output=safe_serialize(exc),
                status=CANCELLED,
                metadata=metadata,
                bp_metadata=_metadata_with_exception(bp_metadata, exc, status=CANCELLED),
                timing=timing.finish(),
                dynamic_metadata={**dynamic_metadata, "stream_chunks": len(chunks)},
            )
            raise
        except Exception as exc:
            status = _exception_status(exc)
            _emit_output_event(
                recorder,
                output_type,
                name,
                call_event_id,
                span_id,
                upstream_ids,
                upstream_refs,
                output={"chunks": chunks, "error": safe_serialize(exc)},
                status=status,
                metadata=metadata,
                bp_metadata=_metadata_with_exception(bp_metadata, exc, status=status),
                timing=timing.finish(),
                dynamic_metadata={**dynamic_metadata, "stream_chunks": len(chunks), "partial": True},
            )
            raise
        final_output: Any = chunks
        output_event = _emit_output_event(
            recorder,
            output_type,
            name,
            call_event_id,
            span_id,
            upstream_ids,
            upstream_refs,
            output=final_output,
            status=SUCCESS,
            metadata=metadata,
            bp_metadata=bp_metadata,
            timing=timing.finish(),
            dynamic_metadata={**dynamic_metadata, "stream_chunks": len(chunks)},
        )
        if track_output:
            tracker.attach(final_output, output_event, provenance_mode=provenance_mode)

    return generator()


def _wrap_async_stream(
    stream: Any,
    *,
    recorder: Recorder,
    tracker: ProvenanceTracker,
    output_type: str,
    name: str,
    call_event_id: str,
    span_id: str,
    upstream_ids: list[str],
    upstream_refs: Any,
    metadata: dict[str, Any] | None,
    bp_metadata: dict[str, Any] | None,
    timing: _StartedTiming,
    dynamic_metadata: dict[str, Any],
    track_output: bool,
    provenance_mode: str | None,
) -> Any:
    async def generator():
        chunks: list[Any] = []
        try:
            with recorder.child_context(call_event_id, span_id):
                async for chunk in stream:
                    chunks.append(chunk)
                    yield chunk
        except asyncio.CancelledError as exc:
            _emit_output_event(
                recorder,
                output_type,
                name,
                call_event_id,
                span_id,
                upstream_ids,
                upstream_refs,
                output=safe_serialize(exc),
                status=CANCELLED,
                metadata=metadata,
                bp_metadata=_metadata_with_exception(bp_metadata, exc, status=CANCELLED),
                timing=timing.finish(),
                dynamic_metadata={**dynamic_metadata, "stream_chunks": len(chunks)},
            )
            raise
        except Exception as exc:
            status = _exception_status(exc)
            _emit_output_event(
                recorder,
                output_type,
                name,
                call_event_id,
                span_id,
                upstream_ids,
                upstream_refs,
                output={"chunks": chunks, "error": safe_serialize(exc)},
                status=status,
                metadata=metadata,
                bp_metadata=_metadata_with_exception(bp_metadata, exc, status=status),
                timing=timing.finish(),
                dynamic_metadata={**dynamic_metadata, "stream_chunks": len(chunks), "partial": True},
            )
            raise
        final_output: Any = chunks
        output_event = _emit_output_event(
            recorder,
            output_type,
            name,
            call_event_id,
            span_id,
            upstream_ids,
            upstream_refs,
            output=final_output,
            status=SUCCESS,
            metadata=metadata,
            bp_metadata=bp_metadata,
            timing=timing.finish(),
            dynamic_metadata={**dynamic_metadata, "stream_chunks": len(chunks)},
        )
        if track_output:
            tracker.attach(final_output, output_event, provenance_mode=provenance_mode)

    return generator()


def _event_metadata(
    static_metadata: dict[str, Any] | None,
    dynamic_metadata: dict[str, Any],
    input_refs: Any,
    bp_metadata: dict[str, Any] | None = None,
) -> dict[str, Any]:
    event_metadata: dict[str, Any] = {}
    _merge_metadata(event_metadata, static_metadata)
    _merge_metadata(event_metadata, dynamic_metadata)
    _merge_metadata(event_metadata, bp_metadata)
    existing_provenance = event_metadata.get("provenance")
    provenance = dict(existing_provenance) if isinstance(existing_provenance, dict) else {}
    provenance["input_refs_detail"] = refs_to_dicts(input_refs)
    event_metadata["provenance"] = provenance
    return event_metadata


def _split_reserved_kwargs(kwargs: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any] | None, bool]:
    call_kwargs = dict(kwargs)
    for key in RESERVED_KWARGS - {"bp_metadata", "bp_no_track"}:
        call_kwargs.pop(key, None)
    bp_metadata = call_kwargs.pop("bp_metadata", None)
    no_track = bool(call_kwargs.pop("bp_no_track", False))
    return call_kwargs, bp_metadata if isinstance(bp_metadata, dict) else None, no_track


def _merge_metadata(target: dict[str, Any], source: dict[str, Any] | None) -> None:
    if not isinstance(source, dict):
        return
    for key, value in source.items():
        if key == "provenance" and isinstance(value, dict):
            existing = target.get("provenance")
            merged = dict(existing) if isinstance(existing, dict) else {}
            merged.update(value)
            target["provenance"] = merged
        else:
            target[key] = value


def _unique_ids(event_ids: list[str]) -> list[str]:
    seen: set[str] = set()
    unique: list[str] = []
    for event_id in event_ids:
        if event_id not in seen:
            unique.append(event_id)
            seen.add(event_id)
    return unique


def _memory_metadata(operation: str, args: tuple[Any, ...], kwargs: dict[str, Any]) -> dict[str, Any]:
    metadata: dict[str, Any] = {"operation": operation}
    memory_key = _detect_memory_key(args, kwargs)
    if memory_key is not None:
        metadata["memory_key"] = memory_key
    return metadata


def _detect_memory_key(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str | None:
    for key_name in ("memory_key", "key"):
        value = kwargs.get(key_name)
        if isinstance(value, str):
            return value
    for value in args:
        if isinstance(value, str):
            return value
    return None


def _metadata_state_name(metadata: dict[str, Any] | None, state_name: str | None) -> str | None:
    if state_name is not None:
        return state_name
    if isinstance(metadata, dict):
        metadata_state_name = metadata.get("state_name")
        if isinstance(metadata_state_name, str):
            return metadata_state_name
    return None


def _state_input_payload(
    operation: str,
    state_name: str,
    state_path: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "state_name": state_name,
        "state_path": state_path,
    }
    if operation == "write":
        payload["before"] = kwargs.get("before")
    else:
        payload["args"] = args
        payload["kwargs"] = kwargs
    return payload


def _state_metadata(
    recorder: Recorder,
    static_metadata: dict[str, Any] | None,
    operation: str,
    state_name: str,
    state_path: str,
    input_refs: Any,
    bp_metadata: dict[str, Any] | None,
    *,
    result: Any,
    call_kwargs: dict[str, Any],
) -> dict[str, Any]:
    dynamic_metadata = {
        "operation": operation,
        "state_name": state_name,
        "state_path": state_path,
    }
    if operation == "read":
        dynamic_metadata["value_hash"] = recorder.hash_payload(result)
    else:
        dynamic_metadata["before_hash"] = recorder.hash_payload(call_kwargs.get("before"))
        dynamic_metadata["after_hash"] = recorder.hash_payload(result)
    return _event_metadata(static_metadata, dynamic_metadata, input_refs, bp_metadata)


def _llm_metadata(kwargs: dict[str, Any]) -> dict[str, Any]:
    metadata = {}
    for key in ("model", "temperature", "tool_choice", "response_format", "stream", "streaming"):
        if key in kwargs:
            metadata[key] = kwargs[key]
    if _metadata_streaming(metadata):
        metadata["streaming"] = True
        metadata["stream_recording_strategy"] = "single_call_final_output"
    return metadata


def _llm_output_metadata(
    call_kwargs: dict[str, Any],
    static_metadata: dict[str, Any] | None,
    bp_metadata: dict[str, Any] | None,
) -> dict[str, Any]:
    metadata = _llm_metadata(call_kwargs)
    if isinstance(static_metadata, dict):
        for key in ("stream", "streaming"):
            if key in static_metadata:
                metadata[key] = static_metadata[key]
    if isinstance(bp_metadata, dict):
        for key in ("stream", "streaming"):
            if key in bp_metadata:
                metadata[key] = bp_metadata[key]
    if _metadata_streaming(metadata):
        metadata["streaming"] = True
        metadata["stream_recording_strategy"] = "single_call_final_output"
    return metadata


def _metadata_streaming(metadata: dict[str, Any]) -> bool:
    return bool(metadata.get("streaming") or metadata.get("stream"))
