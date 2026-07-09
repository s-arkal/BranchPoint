"""Generic instrumentation decorators."""

from __future__ import annotations

import functools
import inspect
from typing import Any, Callable, TypeVar

from branchpoint.core.ids import new_span_id
from branchpoint.core.provenance import ProvenanceTracker
from branchpoint.core.recorder import Recorder
from branchpoint.core.refs import collect_input_refs, refs_to_dicts
from branchpoint.core.schema import (
    ERROR,
    LLM_CALL,
    LLM_OUTPUT,
    MEMORY_READ,
    MEMORY_WRITE,
    RETRIEVAL_QUERY,
    RETRIEVAL_RESULT,
    STATE_READ,
    STATE_WRITE,
    SUCCESS,
    TOOL_CALL,
    TOOL_OUTPUT,
    canonical_state_name,
    canonical_state_path,
    error_metadata,
)
from branchpoint.core.serialization import safe_serialize

F = TypeVar("F", bound=Callable[..., Any])


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
                )
                call_kwargs, bp_metadata, no_track = _split_reserved_kwargs(kwargs)
                try:
                    with recorder.child_context(call_event.event_id, span_id):
                        result = await func(*args, **call_kwargs)
                except Exception as exc:
                    _emit_output_event(
                        recorder,
                        output_type,
                        name or func.__name__,
                        call_event.event_id,
                        span_id,
                        upstream_ids,
                        upstream_refs,
                        output=safe_serialize(exc),
                        status=ERROR,
                        metadata=metadata,
                        bp_metadata=_metadata_with_error(bp_metadata, exc),
                    )
                    raise
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
                )
                if track_output and not no_track:
                    result = tracker.attach(result, output_event, provenance_mode=provenance_mode)
                return result

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
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
            )
            call_kwargs, bp_metadata, no_track = _split_reserved_kwargs(kwargs)
            try:
                with recorder.child_context(call_event.event_id, span_id):
                    result = func(*args, **call_kwargs)
            except Exception as exc:
                _emit_output_event(
                    recorder,
                    output_type,
                    name or func.__name__,
                    call_event.event_id,
                    span_id,
                    upstream_ids,
                    upstream_refs,
                    output=safe_serialize(exc),
                    status=ERROR,
                    metadata=metadata,
                    bp_metadata=_metadata_with_error(bp_metadata, exc),
                )
                raise
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
    def decorate(func: F) -> F:
        if inspect.iscoroutinefunction(func):

            @functools.wraps(func)
            async def async_wrapper(*args: Any, **kwargs: Any) -> Any:
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
                event_metadata = _event_metadata(
                    metadata,
                    _memory_metadata(operation, args, call_kwargs),
                    upstream_refs,
                    bp_metadata,
                )
                try:
                    result = await func(*args, **call_kwargs)
                except Exception as exc:
                    recorder.emit(
                        type=event_type,
                        name=name or func.__name__,
                        input={"args": args, "kwargs": call_kwargs},
                        output=safe_serialize(exc),
                        input_refs=upstream_ids,
                        status=ERROR,
                        metadata=_metadata_with_error(event_metadata, exc),
                    )
                    raise
                event = recorder.emit(
                    type=event_type,
                    name=name or func.__name__,
                    input={"args": args, "kwargs": call_kwargs},
                    output=result,
                    input_refs=upstream_ids,
                    status=SUCCESS,
                    metadata=event_metadata,
                )
                if track_output and not no_track:
                    result = tracker.attach(result, event, provenance_mode=provenance_mode)
                return result

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
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
            event_metadata = _event_metadata(
                metadata,
                _memory_metadata(operation, args, call_kwargs),
                upstream_refs,
                bp_metadata,
            )
            try:
                result = func(*args, **call_kwargs)
            except Exception as exc:
                recorder.emit(
                    type=event_type,
                    name=name or func.__name__,
                    input={"args": args, "kwargs": call_kwargs},
                    output=safe_serialize(exc),
                    input_refs=upstream_ids,
                    status=ERROR,
                    metadata=_metadata_with_error(event_metadata, exc),
                )
                raise
            event = recorder.emit(
                type=event_type,
                name=name or func.__name__,
                input={"args": args, "kwargs": call_kwargs},
                output=result,
                input_refs=upstream_ids,
                status=SUCCESS,
                metadata=event_metadata,
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
                except Exception as exc:
                    recorder.emit(
                        type=event_type,
                        name=name or func.__name__,
                        input={"args": args, "kwargs": call_kwargs},
                        output=safe_serialize(exc),
                        input_refs=upstream_ids,
                        status=ERROR,
                        metadata=_metadata_with_error(
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
                        ),
                    )
                    raise
                event = recorder.emit(
                    type=event_type,
                    name=name or func.__name__,
                    input=_state_input_payload(operation, resolved_state_name, resolved_state_path, args, call_kwargs),
                    output=result,
                    input_refs=upstream_ids,
                    status=SUCCESS,
                    metadata=_state_metadata(
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
                )
                if track_output and not no_track:
                    result = tracker.attach(result, event, provenance_mode=provenance_mode)
                return result

            return async_wrapper  # type: ignore[return-value]

        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
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
            except Exception as exc:
                recorder.emit(
                    type=event_type,
                    name=name or func.__name__,
                    input={"args": args, "kwargs": call_kwargs},
                    output=safe_serialize(exc),
                    input_refs=upstream_ids,
                    status=ERROR,
                    metadata=_metadata_with_error(
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
                    ),
                )
                raise
            event = recorder.emit(
                type=event_type,
                name=name or func.__name__,
                input=_state_input_payload(operation, resolved_state_name, resolved_state_path, args, call_kwargs),
                output=result,
                input_refs=upstream_ids,
                status=SUCCESS,
                metadata=_state_metadata(
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
) -> Any:
    return recorder.emit(
        type=output_type,
        name=name,
        output=output,
        input_refs=_unique_ids([call_event_id, *upstream_ids]),
        status=status,
        parent_id=call_event_id,
        span_id=span_id,
        metadata=_event_metadata(metadata, {}, upstream_refs, bp_metadata),
    )


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
    call_kwargs.pop("bp_input_refs", None)
    call_kwargs.pop("bp_depends_on", None)
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


def _metadata_with_error(metadata: dict[str, Any] | None, exc: BaseException) -> dict[str, Any]:
    event_metadata = dict(metadata or {})
    _merge_metadata(event_metadata, error_metadata(exc))
    return event_metadata


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
    for key in ("model", "temperature", "tool_choice", "response_format"):
        if key in kwargs:
            metadata[key] = kwargs[key]
    return metadata
