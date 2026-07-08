"""Generic instrumentation decorators."""

from __future__ import annotations

import functools
from typing import Any, Callable, TypeVar

from branchpoint.core.ids import new_span_id
from branchpoint.core.recorder import Recorder
from branchpoint.core.schema import (
    ERROR,
    LLM_CALL,
    LLM_OUTPUT,
    MEMORY_READ,
    MEMORY_WRITE,
    RETRIEVAL_QUERY,
    RETRIEVAL_RESULT,
    SUCCESS,
    TOOL_CALL,
    TOOL_OUTPUT,
)
from branchpoint.core.serialization import safe_serialize

F = TypeVar("F", bound=Callable[..., Any])


def tool_decorator(recorder: Recorder, name: str | None = None) -> Callable[[F], F]:
    return _call_output_pair(recorder, TOOL_CALL, TOOL_OUTPUT, name=name)


def llm_decorator(recorder: Recorder, name: str | None = None) -> Callable[[F], F]:
    return _call_output_pair(recorder, LLM_CALL, LLM_OUTPUT, name=name, metadata_from_kwargs=True)


def retrieval_decorator(recorder: Recorder, name: str | None = None) -> Callable[[F], F]:
    return _call_output_pair(recorder, RETRIEVAL_QUERY, RETRIEVAL_RESULT, name=name)


def memory_read_decorator(recorder: Recorder, name: str | None = None) -> Callable[[F], F]:
    return _memory_event(recorder, MEMORY_READ, "read", name=name)


def memory_write_decorator(recorder: Recorder, name: str | None = None) -> Callable[[F], F]:
    return _memory_event(recorder, MEMORY_WRITE, "write", name=name)


def _call_output_pair(
    recorder: Recorder,
    call_type: str,
    output_type: str,
    name: str | None = None,
    metadata_from_kwargs: bool = False,
) -> Callable[[F], F]:
    def decorate(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            span_id = new_span_id()
            metadata = _llm_metadata(kwargs) if metadata_from_kwargs else {}
            call_event = recorder.emit(
                type=call_type,
                name=name or func.__name__,
                input={"args": args, "kwargs": kwargs},
                status=SUCCESS,
                span_id=span_id,
                metadata=metadata,
            )
            try:
                with recorder.child_context(call_event.event_id, span_id):
                    result = func(*args, **kwargs)
            except Exception as exc:
                recorder.emit(
                    type=output_type,
                    name=name or func.__name__,
                    output=safe_serialize(exc),
                    input_refs=[call_event.event_id],
                    status=ERROR,
                    parent_id=call_event.event_id,
                    span_id=span_id,
                )
                raise
            recorder.emit(
                type=output_type,
                name=name or func.__name__,
                output=result,
                input_refs=[call_event.event_id],
                status=SUCCESS,
                parent_id=call_event.event_id,
                span_id=span_id,
            )
            return result

        return wrapper  # type: ignore[return-value]

    return decorate


def _memory_event(recorder: Recorder, event_type: str, operation: str, name: str | None = None) -> Callable[[F], F]:
    def decorate(func: F) -> F:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            metadata = {"operation": operation}
            memory_key = _detect_memory_key(args, kwargs)
            if memory_key is not None:
                metadata["memory_key"] = memory_key
            try:
                result = func(*args, **kwargs)
            except Exception as exc:
                recorder.emit(
                    type=event_type,
                    name=name or func.__name__,
                    input={"args": args, "kwargs": kwargs},
                    output=safe_serialize(exc),
                    status=ERROR,
                    metadata=metadata,
                )
                raise
            recorder.emit(
                type=event_type,
                name=name or func.__name__,
                input={"args": args, "kwargs": kwargs},
                output=result,
                status=SUCCESS,
                metadata=metadata,
            )
            return result

        return wrapper  # type: ignore[return-value]

    return decorate


def _detect_memory_key(args: tuple[Any, ...], kwargs: dict[str, Any]) -> str | None:
    for key_name in ("memory_key", "key"):
        value = kwargs.get(key_name)
        if isinstance(value, str):
            return value
    for value in args:
        if isinstance(value, str):
            return value
    return None


def _llm_metadata(kwargs: dict[str, Any]) -> dict[str, Any]:
    metadata = {}
    for key in ("model", "temperature", "tool_choice", "response_format"):
        if key in kwargs:
            metadata[key] = kwargs[key]
    return metadata
